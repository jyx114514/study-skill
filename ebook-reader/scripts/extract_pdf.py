#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extract text, page numbers, embedded bookmarks (TOC) and best-effort
chapter boundaries from a PDF book, to support chapter-level summarization.

Usage:
    python extract_pdf.py <input.pdf> [--out <extracted.json>] [--no-toc-split]

Output (JSON to stdout, or to --out file if given):
{
  "path": str,
  "num_pages": int,
  "has_embedded_toc": bool,
  "toc": [ {"title": str, "page": int}, ... ],          # embedded bookmarks
  "chapters": [                                           # best-effort segmentation
      {"index": int, "title": str, "start_page": int, "end_page": int, "text": str}
  ],
  "pages": [ {"page": int, "text": str}, ... ]           # full per-page text
}

Dependency: this script reads PDFs with `pypdf` (preferred) or `PyPDF2`.
If neither is installed, install with:  pip install pypdf
"""

import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# PDF backend selection
# ---------------------------------------------------------------------------
PDF_LIB = None
try:
    from pypdf import PdfReader
    PDF_LIB = "pypdf"
except ImportError:
    try:
        from PyPDF2 import PdfReader
        PDF_LIB = "PyPDF2"
    except ImportError:
        sys.stderr.write(
            "ERROR: neither 'pypdf' nor 'PyPDF2' is installed.\n"
            "Install one with:  pip install pypdf\n"
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# Heuristics for chapter/section heading detection
# ---------------------------------------------------------------------------
# Strong chapter markers (multi-language): Chapter / 第N章 / Part / 卷 / 单元 / 模块 / Lesson / Section ...
# Supports both Arabic and Chinese numerals: 第一章 / 第1章 / Chapter 1 / 1.1 ...
CN_NUM = r"[一二三四五六七八九十百千零〇0-9]+"
CHAPTER_RE = re.compile(
    r"^\s*(?:"
    r"(第\s*" + CN_NUM + r"\s*[章节篇卷])"          # 第一章 / 第1章 / 第 12 节
    r"|"
    r"(chapter|part|unit|module|lesson|section|book)\s*[:\s]*\d+\b"
    r"|"
    r"(卷|编|篇|单元|模块|章节)\s*" + CN_NUM + r"\b"
    r"|"
    r"([0-9]+\s*[\.、]\s*\S+)"
    r")",
    re.IGNORECASE,
)

# Lines that are clearly NOT headings even if short.
NOISE_RE = re.compile(
    r"^\s*(\d+\s*/\s*\d+$|contents?|目录|table of contents|参考文献|references?|"
    r"appendix|索引|index|版权|copyright|isbn|http)",
    re.IGNORECASE,
)


def looks_like_heading(line: str, first_on_page: bool) -> bool:
    """Heuristic: is this line a chapter/section title?"""
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if NOISE_RE.search(s):
        return False
    if CHAPTER_RE.search(s):
        return True
    # Short, title-cased or ALL-CAPS line near the top of a page.
    if first_on_page and len(s) <= 50:
        if s.isupper() and len(s) > 3:
            return True
        words = s.split()
        if words and sum(1 for w in words if w[:1].isupper()) >= max(2, len(words) // 2):
            return True
    return False


def all_titles_numeric(toc):
    """True when every TOC entry is just a page number (e.g. bookmarks by page)."""
    if not toc:
        return False
    return all(re.fullmatch(r"\d+", (t.get("title") or "").strip()) for t in toc)


def build_chapters(pages, toc):
    """Segment pages into chapter blocks using TOC if present, else heuristics."""
    chapters = []

    if toc:
        # Build sorted (page, title) anchors from the TOC.
        anchors = sorted(
            [(max(0, t["page"]), t["title"]) for t in toc if t.get("title")],
            key=lambda x: x[0],
        )
        for i, (start, title) in enumerate(anchors):
            end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else len(pages) - 1
            text = "\n".join(
                pages[p]["text"] for p in range(start, end + 1) if 0 <= p < len(pages)
            )
            chapters.append(
                {
                    "index": i + 1,
                    "title": title.strip(),
                    "start_page": start,
                    "end_page": end,
                    "text": text,
                }
            )
        return chapters

    # Heuristic: scan for heading lines, group text between them.
    boundaries = []  # (page_index, line_index, title)
    for pi, pg in enumerate(pages):
        lines = pg["text"].splitlines()
        for li, line in enumerate(lines):
            if looks_like_heading(line, first_on_page=(li == 0)):
                boundaries.append((pi, li, line.strip()))
                break  # one heading candidate per page is enough

    if not boundaries:
        # No headings found: treat the whole book as one block.
        full = "\n".join(p["text"] for p in pages)
        return [
            {
                "index": 1,
                "title": "全书概览",
                "start_page": 0,
                "end_page": len(pages) - 1,
                "text": full,
            }
        ]

    for i, (pi, li, title) in enumerate(boundaries):
        # Determine the character span where this heading begins.
        start_char = sum(len(pages[b[0]]["text"]) + 1 for b in boundaries[:i])
        # simpler: just join from this page's heading line onward until next boundary page
        next_pi = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(pages)
        text_parts = []
        for p in range(pi, next_pi):
            page_text = pages[p]["text"]
            if p == pi:
                page_lines = page_text.splitlines()
                # start from the heading line
                page_text = "\n".join(page_lines[li:])
            text_parts.append(page_text)
        chapters.append(
            {
                "index": i + 1,
                "title": title,
                "start_page": pi,
                "end_page": next_pi - 1,
                "text": "\n".join(text_parts),
            }
        )
    return chapters


def extract(pdf_path):
    reader = PdfReader(pdf_path)
    num_pages = len(reader.pages)

    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append({"page": i, "text": text})

    # Embedded bookmarks / outline.
    toc = []
    try:
        outlines = reader.outline
        # Flatten the nested outline into (title, page) pairs.
        def _walk(items, depth=0):
            for item in items:
                if isinstance(item, list):
                    _walk(item, depth + 1)
                else:
                    try:
                        title = item.title
                        # page is a zero-based index via the destination
                        pg = reader.get_destination_page_number(item)
                        toc.append({"title": title, "page": int(pg)})
                    except Exception:
                        continue

        _walk(outlines)
    except Exception:
        toc = []

    # De-duplicate consecutive identical page numbers, keep first title.
    seen = set()
    clean_toc = []
    for t in toc:
        key = (t["page"], t["title"])
        if key in seen:
            continue
        seen.add(key)
        clean_toc.append(t)

    # Total extracted text — used to detect scanned / image-only PDFs.
    total_text = sum(len(p["text"]) for p in pages)
    is_scanned = total_text < max(200, num_pages)  # essentially no text layer

    # A TOC made only of page numbers (1,2,3,...) is not real structure.
    toc_usable = bool(clean_toc) and not all_titles_numeric(clean_toc)

    chapters = build_chapters(pages, clean_toc if toc_usable else [])

    if is_scanned:
        # No text to summarize. Return a single informative block so the caller
        # can stop gracefully instead of producing 787 empty "chapters".
        chapters = [
            {
                "index": 1,
                "title": "（扫描版 PDF：未检测到文字层，需 OCR 才能总结）",
                "start_page": 0,
                "end_page": num_pages - 1,
                "text": "",
            }
        ]

    return {
        "path": pdf_path,
        "num_pages": num_pages,
        "has_embedded_toc": bool(clean_toc),
        "toc_usable": toc_usable,
        "is_scanned": is_scanned,
        "extracted_text_total": total_text,
        "toc": clean_toc,
        "chapters": chapters,
        "pages": pages,
    }


def main():
    ap = argparse.ArgumentParser(description="Extract text + chapters from a PDF book.")
    ap.add_argument("pdf", help="Path to the input PDF file")
    ap.add_argument("--out", help="Write JSON result to this file instead of stdout")
    args = ap.parse_args()

    result = extract(args.pdf)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        sys.stderr.write(
            f"OK: {result['num_pages']} pages, "
            f"{len(result['chapters'])} chapter blocks, "
            f"embedded TOC={'yes' if result['has_embedded_toc'] else 'no'}\n"
        )
    else:
        print(payload)


if __name__ == "__main__":
    main()
