#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_images.py — Render PDF pages (or embedded raster images) to PNG files
so that an AI vision capability can read figures whose text layer is garbled
or missing.

Why this exists
---------------
PDF text extraction often produces mojibake for figure labels/captions (e.g.
lines full of "$...$" tags or unmappable glyphs) while the body prose extracts
fine. Instead of guessing figure content from garbage, export the page image
and let the AI read it visually (use the Read tool on the generated PNG).

Usage
-----
# 1) Render specific pages (page numbers are 0-based, matching the
#    `start_page`/`end_page` fields in _pdf_extract.json):
python export_images.py "<PDF>" --pages 43,44 --out "./_pdf_images" --dpi 150

# 2) Render a contiguous range:
python export_images.py "<PDF>" --pages 40-45 --out "./_pdf_images" --dpi 150

# 3) Extract embedded raster images from a page (fallback when PyMuPDF
#    is unavailable or rendering looks wrong):
python export_images.py "<PDF>" --embedded --page 43 --out "./_pdf_images"

Dependencies: PyMuPDF (fitz) preferred; pypdf fallback for --embedded.
Install PyMuPDF (into the managed env, never globally):
  ~/.workbuddy/binaries/python/versions/<ver>/python.exe -m pip install pymupdf
"""

import argparse
import os
import sys


def parse_pages(spec):
    """Parse '1,3,5-7' into a sorted list of ints."""
    pages = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            for p in range(int(a), int(b) + 1):
                pages.add(p)
        else:
            pages.add(int(part))
    return sorted(pages)


def render_with_fitz(pdf_path, pages, out_dir, dpi):
    """Render pages to PNG via PyMuPDF."""
    try:
        import pymupdf as fitz  # PyMuPDF (new API name; 'import fitz' is deprecated)
    except ImportError:
        try:
            import fitz  # legacy module name
        except ImportError:
            print("ERROR: PyMuPDF not installed. Run:")
            print('  ~/.workbuddy/binaries/python/versions/<ver>/python.exe -m pip install pymupdf')
            print("or use --embedded to extract raw embedded images instead.")
            sys.exit(2)

    doc = fitz.open(pdf_path)
    total = doc.page_count
    written = []
    for pno in pages:
        if pno < 0 or pno >= total:
            print(f"  [skip] page {pno} out of range (0..{total - 1})")
            continue
        page = doc.load_page(pno)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        name = os.path.join(out_dir, f"page_{pno:04d}.png")
        pix.save(name)
        written.append((name, pix.width, pix.height))
        print(f"  [ok]   p.{pno} -> {name} ({pix.width}x{pix.height})")
    doc.close()

    if not written:
        print("No pages rendered.")
        return 1
    print(f"\nRendered {len(written)} page(s) to {out_dir}.")
    print("Next: open each PNG with the AI's image-reading tool (Read) and")
    print("transcribe/describe the figure faithfully for the summary or answer.")
    return 0


def extract_embedded(pdf_path, pages, out_dir):
    """Extract raw embedded images via pypdf (fallback)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("ERROR: pypdf not installed. Install it first.")
        sys.exit(2)

    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    written = []
    for pno in pages:
        if pno < 0 or pno >= total:
            print(f"  [skip] page {pno} out of range (0..{total - 1})")
            continue
        page = reader.pages[pno]
        imgs = getattr(page, "images", None)
        if not imgs:
            print(f"  [info] p.{pno}: no embedded images found")
            continue
        for i, img in enumerate(imgs):
            data = bytes(img.data)
            # guess extension from magic bytes
            ext = ".img"
            if data[:3] == b"\xff\xd8\xff":
                ext = ".jpg"
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                ext = ".png"
            elif data[:4] == b"GIF8":
                ext = ".gif"
            elif data[:4] == b"%PDF":
                ext = ".pdf"
            name = os.path.join(out_dir, f"p{pno:04d}_img{i:02d}{ext}")
            with open(name, "wb") as f:
                f.write(data)
            written.append(name)
            print(f"  [ok]   p.{pno} img#{i} -> {name} ({len(data)} bytes)")
    if not written:
        print("No embedded images extracted.")
        return 1
    print(f"\nExtracted {len(written)} embedded image(s) to {out_dir}.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Export PDF pages to PNG for AI vision reading.",
        epilog="Page numbers are 0-based, matching _pdf_extract.json.",
    )
    ap.add_argument("pdf", help="path to the PDF file")
    ap.add_argument("--pages", default=None,
                    help="pages to render, e.g. '43,44' or '40-45' (0-based)")
    ap.add_argument("--embedded", action="store_true",
                    help="extract raw embedded images instead of rendering (pypdf fallback)")
    ap.add_argument("--page", type=int, default=None,
                    help="single page for --embedded mode (0-based)")
    ap.add_argument("--out", default="_pdf_images",
                    help="output directory (default: ./_pdf_images)")
    ap.add_argument("--dpi", type=int, default=150,
                    help="render resolution in DPI (default: 150)")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"ERROR: PDF not found: {args.pdf}")
        sys.exit(2)

    os.makedirs(args.out, exist_ok=True)

    if args.embedded:
        pno = args.page if args.page is not None else (
            parse_pages(args.pages)[0] if args.pages else None)
        if pno is None:
            print("ERROR: specify --page (or --pages) for --embedded mode.")
            sys.exit(2)
        return extract_embedded(args.pdf, [pno], args.out)

    if not args.pages:
        print("ERROR: specify --pages (e.g. --pages 43,44 or --pages 40-45).")
        sys.exit(2)

    pages = parse_pages(args.pages)
    return render_with_fitz(args.pdf, pages, args.out, args.dpi)


if __name__ == "__main__":
    sys.exit(main())
