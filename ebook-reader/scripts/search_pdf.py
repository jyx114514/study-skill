#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Search an extracted-PDF JSON (produced by extract_pdf.py) by keywords and
return the matching snippets with chapter + page info. This powers the
"ask questions about the book" mode: locate the exact places in the book
that discuss a topic, then answer based on the actual content.

Usage:
    python search_pdf.py <extracted.json> <query...> [--top N] [--window W]

Arguments:
    <extracted.json>   JSON produced by scripts/extract_pdf.py
    <query...>         one or more search terms (phrase matching, case-insensitive)

Options:
    --top N       max results to return (default 10)
    --window W     characters of context around each hit (default 220)

Output (JSON to stdout):
{
  "query": [...],
  "total_hits": int,
  "results": [
    {
      "chapter_index": int,
      "chapter_title": str,
      "page": int,                    # zero-based PDF page index
      "match_count": int,             # occurrences on this page
      "snippet": str                  # context window around the first match
    }, ...
  ]
}

Note: `chapter_index` refers to the "index" field of the chapters array in
the extracted JSON. Page numbers are zero-based; add 1 when presenting
"第 N 页" to the user.
"""

import argparse
import json
import sys


def normalize(s: str) -> str:
    return " ".join(s.split()).lower()


def main():
    ap = argparse.ArgumentParser(description="Keyword search over extracted PDF text.")
    ap.add_argument("json_path", help="Path to extracted JSON from extract_pdf.py")
    ap.add_argument("query", nargs="+", help="Search terms (phrase matching)")
    ap.add_argument("--top", type=int, default=10, help="Max results (default 10)")
    ap.add_argument("--window", type=int, default=220, help="Context chars per hit (default 220)")
    args = ap.parse_args()

    with open(args.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = data.get("chapters", [])
    pages = data.get("pages", [])
    if not pages:
        sys.stderr.write("ERROR: extracted JSON has no 'pages' array.\n")
        sys.exit(1)

    queries = [normalize(q) for q in args.query]
    results = []

    for pi, pg in enumerate(pages):
        page_text = pg.get("text", "")
        norm_text = normalize(page_text)
        count = sum(norm_text.count(q) for q in queries)
        if count == 0:
            continue

        # Find the chapter block this page belongs to.
        ch_index = None
        ch_title = ""
        for ch in chapters:
            if ch["start_page"] <= pi <= ch["end_page"]:
                ch_index = ch["index"]
                ch_title = ch["title"]
                break

        # Build a snippet around the first match (on original-case text).
        flat = " ".join(page_text.split())
        first_pos = len(flat)
        for q in queries:
            pos = flat.lower().find(q)
            if pos != -1 and pos < first_pos:
                first_pos = pos
        lo = max(0, first_pos - args.window // 2)
        hi = min(len(flat), first_pos + args.window // 2)
        snippet = flat[lo:hi]

        results.append(
            {
                "chapter_index": ch_index,
                "chapter_title": ch_title,
                "page": pi,
                "match_count": count,
                "snippet": snippet,
            }
        )

    results.sort(key=lambda r: -r["match_count"])
    results = results[: args.top]

    out = {
        "query": args.query,
        "total_hits": sum(r["match_count"] for r in results),
        "results": results,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
