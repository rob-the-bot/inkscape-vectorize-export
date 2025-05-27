# inkscape-vectorize-export

## Overview

This script provides a workaround for a common issue in Inkscape:
when exporting an SVG file to PDF,
linked vector images (such as other SVGs) are often rasterized.
This results in blurry or non-scalable graphics in the output.

The script automatically replaces linked SVG images with inline vector content, preserving their position, size, and transform.
Raster images (e.g., PNG, JPG) are preserved without modification.
All changes are made in a temporary file; the original SVG is not modified.

## Why this tool

Inkscape does not currently inline linked vector images during PDF export unless they are manually imported as editable objects.
This script automates that process to ensure the exported PDF remains fully vectorized.

## Features

- Inlines linked SVG files as editable groups
- Preserves raster images as-is
- Converts relative paths to absolute paths (including `file:///`)
- Compatible with Inkscape's command-line interface

## Usage

```bash
python export_vectorized.py input.svg output.pdf
```

Enable verbose output:

```bash
python export_vectorized.py input.svg output.pdf --verbose
```

## Requirements

- Python 3.7+
- Inkscape (installed and in PATH)
- lxml (`pip install lxml`)

## Limitations

- Only processes `<image>` elements (not `<use>` or `<foreignObject>`)
- Linked PDFs are not inlined
- The effective DPI of raster images in the PDF is determined by layout, not embedded metadata
