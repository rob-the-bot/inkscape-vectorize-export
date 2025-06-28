import argparse
import tempfile
import subprocess
import logging
from pathlib import Path
from lxml import etree
from urllib.parse import urlparse, unquote
import os


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NSMAP = {"svg": SVG_NS, "xlink": XLINK_NS}

etree.register_namespace("svg", SVG_NS)
etree.register_namespace("xlink", XLINK_NS)

logger = logging.getLogger(__name__)


def make_absolute_href(href: str, base_path: Path) -> str:
    """
    Converts a relative path in href into an absolute file URI path (with forward slashes).
    Leaves data URIs, http(s), and existing absolute paths alone.
    """
    if (
        href.startswith("data:")
        or href.startswith("http:")
        or href.startswith("https:")
        or href.startswith("file:///")
        or href.startswith("file:////")
        or Path(href).is_absolute()
    ):
        return href

    abs_path = (base_path / href).resolve()
    assert abs_path.is_file(), f"File {abs_path} doesn't exist!"
    return "file:///" + abs_path.as_posix()


def extract_path_from_href(href: str) -> Path:
    """
    Convert href to a filesystem Path.
    Handles:
        - file:/// URIs (e.g. file:///C:/path/to/file.svg)
        - relative paths
        - already-absolute paths
    """
    if href.startswith("file://"):
        parsed = urlparse(href)
        path = unquote(parsed.path)

        # On Windows, remove leading slash if followed by a drive letter (e.g. /C:/...)
        if (
            os.name == "nt"
            and path.startswith("/")
            and len(path) > 2
            and path[2] == ":"
        ):
            path = path[1:]

        return Path(path)

    return Path(href)


def convert_to_plain_svg_if_needed(svg_path: Path) -> Path:
    """
    If the SVG uses Inkscape-specific namespaces, convert it to Plain SVG
    using the modern Inkscape CLI. Otherwise, return the original path.
    """
    try:
        tree = etree.parse(str(svg_path))
        root = tree.getroot()
    except etree.XMLSyntaxError as e:
        logger.error(f"Failed to parse SVG: {svg_path} — {e}")
        raise

    nsmap = root.nsmap
    if any(ns for ns in nsmap if ns in ["inkscape", "sodipodi"]):
        logger.info(
            f"{svg_path.name} uses Inkscape-specific features. Converting to Plain SVG."
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".svg", dir=svg_path.parent) as tmp_file:
            plain_svg_path = Path(tmp_file.name)

        result = subprocess.run(
            [
                "inkscape",
                str(svg_path),
                "--export-plain-svg",
                "--export-type=svg",
                f"--export-filename={str(plain_svg_path)}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            logger.error(
                f"Inkscape failed to convert to plain SVG:\n{result.stderr.decode()}"
            )
            raise RuntimeError("Inkscape export-plain-svg failed.")

        if not plain_svg_path.exists() or plain_svg_path.stat().st_size == 0:
            logger.error("Inkscape output file is empty or missing.")
            raise RuntimeError("Plain SVG output is empty.")

        return plain_svg_path
    else:
        logger.info(f"{svg_path.name} is already a plain SVG.")
        return svg_path


def inline_svg_image_element(image_elem, href: str, parser, href_attr: str):
    """
    Inlines a linked SVG file by replacing the <image> element with a transformed <g> element.

    Args:
        image_elem: The <image> element in the main SVG tree.
        href (str): The absolute path to the linked SVG file (as a string).
        parser: XML parser instance.
        href_attr (str): Fully-qualified xlink:href attribute name.
    """
    linked_path = extract_path_from_href(href)
    if not linked_path.exists():
        logger.warning(f"Linked SVG not found: {linked_path}")
        return

    logger.info(f"Inlining linked SVG: {linked_path}")
    sub_tree = etree.parse(str(linked_path), parser)
    sub_root = sub_tree.getroot()

    viewBox = sub_root.attrib.get("viewBox")
    if viewBox:
        _, _, vb_width, vb_height = map(float, viewBox.strip().split())
    else:
        vb_width = float(sub_root.attrib.get("width", "1").replace("px", ""))
        vb_height = float(sub_root.attrib.get("height", "1").replace("px", ""))
        logger.warning(
            f"No viewBox in {linked_path.name}; using width/height: {vb_width} x {vb_height}"
        )

    x = float(image_elem.attrib.get("x", "0"))
    y = float(image_elem.attrib.get("y", "0"))
    width = float(image_elem.attrib.get("width", str(vb_width)))
    height = float(image_elem.attrib.get("height", str(vb_height)))

    scale_x = width / vb_width
    scale_y = height / vb_height

    transform_parts = [f"translate({x},{y})", f"scale({scale_x},{scale_y})"]
    if "transform" in image_elem.attrib:
        transform_parts.insert(0, image_elem.attrib["transform"])
    total_transform = " ".join(transform_parts)

    wrapper = etree.Element(f"{{{SVG_NS}}}g", attrib={"transform": total_transform})
    for elem in sub_root:
        wrapper.append(elem)

    image_elem.getparent().replace(image_elem, wrapper)


def inline_linked_vectors(svg_path: Path) -> Path:
    """
    Parses the input SVG file and:
      - Converts all relative xlink:href paths to absolute paths.
      - Inlines linked SVG images as editable vector content with preserved position and size.

    Args:
        svg_path (Path): Path to the input SVG file.

    Returns:
        Path: Path to a temporary SVG file ready for export.
    """
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(svg_path), parser)
    root = tree.getroot()

    href_attr = f"{{{XLINK_NS}}}href"

    # First pass: make all relative paths absolute (png, jpg, pdf, svg)
    for image in root.xpath(".//svg:image", namespaces=NSMAP):
        href = image.attrib.get(href_attr)
        if not href:
            continue

        new_href = make_absolute_href(href, svg_path.parent)
        if new_href != href:
            if Path(extract_path_from_href(new_href)).exists():
                image.attrib[href_attr] = new_href
                logger.info(f"Made path absolute: {new_href}")
            else:
                logger.warning(f"Linked file not found: {new_href}")

    # Second pass: inline SVGs only
    for image in root.xpath(".//svg:image", namespaces=NSMAP):
        href = image.attrib.get(href_attr)
        if not href or not href.lower().endswith(".svg"):
            continue

        inline_svg_image_element(image, href, parser, href_attr)

    # Write to temporary SVG file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".svg") as tmp_file:
        tree.write(
            tmp_file.name, pretty_print=True, xml_declaration=True, encoding="UTF-8"
        )
        logger.info(f"Temporary SVG written: {tmp_file.name}")
        return Path(tmp_file.name)


def export_to_pdf(svg_path: Path, pdf_path: Path):
    """
    Exports the given SVG file to a PDF using Inkscape's command-line interface.

    Args:
        svg_path (Path): Path to the SVG file to export.
        pdf_path (Path): Path to the output PDF file.
    """
    logger.info("Exporting to PDF with Inkscape...")
    result = subprocess.run(
        [
            "inkscape",
            str(svg_path),
            "--export-type=pdf",
            f"--export-filename={str(pdf_path)}",
        ]
    )
    if result.returncode != 0:
        logger.error("Inkscape export failed.")
        raise RuntimeError("Inkscape export failed.")
    logger.info(f"Exported successfully to: {pdf_path}")


def main():
    """
    Command-line interface for inlining linked vector images in an SVG
    and exporting the result as a vector-preserving PDF.
    """
    parser = argparse.ArgumentParser(
        description="Inline linked SVGs and export to a fully vectorized PDF."
    )
    parser.add_argument("input_svg", type=Path, help="Path to the input SVG file.")
    parser.add_argument("output_pdf", type=Path, help="Path to the output PDF file.")
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("Checking for Inkscape-specific format...")
    cleaned_svg_path = convert_to_plain_svg_if_needed(args.input_svg)

    logger.info("Inlining linked vector files...")
    inlined_svg_path = inline_linked_vectors(cleaned_svg_path)

    if cleaned_svg_path != args.input_svg:
        cleaned_svg_path.unlink(missing_ok=True)
        logger.info(f"Temporary plain SVG {cleaned_svg_path} removed.")

    try:
        export_to_pdf(inlined_svg_path, args.output_pdf)
    finally:
        inlined_svg_path.unlink(missing_ok=True)
        logger.info(f"Temporary file {inlined_svg_path} removed.")


if __name__ == "__main__":
    main()
