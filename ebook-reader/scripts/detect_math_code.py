#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Locate math formulas, code examples, and derivation/worked-proof passages inside
an extracted-PDF JSON (produced by extract_pdf.py). Used by the summary mode so
that these high-value contents are NEVER dropped or paraphrased away: the agent
must reproduce them verbatim in the chapter summary.

Usage:
    python detect_math_code.py <extracted.json> [--chapter N] [--context L]

Arguments:
    <extracted.json>   JSON produced by scripts/extract_pdf.py
    --chapter N        only scan one chapter (its "index" field); default: all
    --context L        include up to L chars of following context per hit (default 0)

Output (JSON to stdout):
{
  "scanned_chapters": int,
  "chapters": [
    {
      "chapter_index": int,
      "chapter_title": str,
      "math":     [ {"page": int, "text": str}, ... ],
      "code":     [ {"page": int, "text": str}, ... ],
      "derivation": [ {"page": int, "text": str}, ... ]
    }, ...
  ]
}

Detection heuristics:
- math:     LaTeX markers ($...$, \\frac, \\sum, \\int, \\sqrt, \\alpha...), Unicode
            math symbols (∑ ∫ √ ≤ ≥ ≠ π θ → ·), equation-numbered lines like (1.1),
            or lines with high digit/operator density.
- code:     indented blocks, programming keywords (def/import/return/if/for/class/
            include/print/let/const/function), braces/semicolons, // or # comments.
- derivation: lines containing 证明/推导/证/由..得/代入/化简/因此/可知/可得 (or English
            proof/derive/therefore), or consecutive `=` chains.

Page numbers are zero-based (same as the extracted JSON); add 1 when presenting.
"""

import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------
MATH_MARKERS = re.compile(
    r"\$|\\\\frac|\\\\sum|\\\\int|\\\\sqrt|\\\\alpha|\\\\beta|\\\\theta|\\\\lambda|"
    r"∑|∫|√|≤|≥|≠|π|θ|λ|Δ|∞|→|·|×|÷|∈|∀|∃"
)
EQ_NUMBER = re.compile(r"\(\s*[\d.]+\s*\)\s*$")  # line ending like (1.1)

CODE_KEYWORDS = re.compile(
    r"\b(def|import|from|return|if|elif|else|for|while|class|function|include|"
    r"print|let|const|var|void|int|float|double|char|true|false|null|"
    r"public|private|static|namespace|using|std::|new|try|catch|throw)\b"
)
CODE_CHARS = re.compile(r"[{};]|//|/\*|\*/|#include|=>|->|::")

DERIVATION_MARKERS = re.compile(
    r"证明|推导|证[明毕]?[:：]|由.{0,6}得|代入|化简|因此|可知|可得|同理|"
    r"proof|derive|therefore|hence|q\.e\.d"
)
CHAIN_EQUALS = re.compile(r"={2,}")


def detect_in_page(text: str, page: int, context: int):
    """Return (math_hits, code_hits, derivation_hits) for one page of text.

    Adjacent hit lines are merged into contiguous blocks so the returned text
    spans do not overlap each other.
    """
    lines = text.splitlines()
    math_hits, code_hits, deriv_hits = [], [], []
    in_code_block = False
    for li, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        # --- fenced/indented code blocks ---
        if s.startswith("```"):
            in_code_block = not in_code_block
            continue
        indented = s.startswith((" ", "\t")) and len(s) >= 4

        # --- math ---
        # Formula-numbered lines like (1.1), Unicode math symbols, or lines with
        # an equals sign plus fraction/set/probability operators (e.g. P(A|B)=...)
        if (
            MATH_MARKERS.search(s)
            or (EQ_NUMBER.search(s) and re.search(r"\d", s))
            or (re.search(r"=", s) and re.search(r"[\/|∩∪∈]", s) and re.search(r"[A-Za-zΑ-Ωα-ω]", s))
        ):
            if len(s) <= 300:
                math_hits.append(li)

        # --- code ---
        if CODE_KEYWORDS.search(s) or (CODE_CHARS.search(s) and indented):
            code_hits.append(li)
            # fold contiguous indented lines into the same code block
            j = li + 1
            while j < len(lines) and lines[j].strip():
                if not (lines[j].startswith((" ", "\t")) or lines[j].strip().startswith(
                    ("def ", "for ", "if ", "while ", "return ", "import ", "class ", "else", "elif", "try", "except")
                )):
                    break
                code_hits.append(j)
                j += 1
        elif in_code_block and len(s) <= 300:
            code_hits.append(li)

        # --- derivation ---
        if DERIVATION_MARKERS.search(s):
            deriv_hits.append(li)

    def _blocks(hit_lines):
        """Merge adjacent hit lines into non-overlapping text blocks."""
        if not hit_lines:
            return []
        blocks = []
        start = hit_lines[0]
        prev = start
        for li in hit_lines[1:]:
            if li - prev > 1:  # gap -> close current block
                blocks.append((start, prev))
                start = li
            prev = li
        blocks.append((start, prev))
        out = []
        for lo, hi in blocks:
            hi = min(hi, len(lines) - 1)
            buf = "\n".join(lines[lo : hi + 1])
            # extend with context lines until the budget is met
            nxt = hi + 1
            while context > 0 and nxt < len(lines) and len(buf) < context:
                buf += "\n" + lines[nxt]
                nxt += 1
            out.append({"page": page, "text": buf[: context if context > 0 else len(buf)]})
        return out

    return _blocks(math_hits), _blocks(code_hits), _blocks(deriv_hits)


def main():
    ap = argparse.ArgumentParser(description="Locate math/code/derivation in extracted PDF text.")
    ap.add_argument("json_path", help="Path to extracted JSON from extract_pdf.py")
    ap.add_argument("--chapter", type=int, default=None, help="Only scan chapter with this index")
    ap.add_argument("--context", type=int, default=0, help="Include up to L chars of context after each hit")
    args = ap.parse_args()

    with open(args.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = data.get("chapters", [])
    if args.chapter is not None:
        chapters = [c for c in chapters if c["index"] == args.chapter]

    result = {"scanned_chapters": len(chapters), "chapters": []}
    for ch in chapters:
        math, code, deriv = [], [], []
        # Re-split chapter text by page is not possible directly; instead map the
        # chapter text back to pages via the pages array (page range known).
        lo, hi = ch["start_page"], ch["end_page"]
        for pg in data.get("pages", []):
            if lo <= pg["page"] <= hi and pg.get("text"):
                m, c, d = detect_in_page(pg["text"], pg["page"], args.context)
                math += m
                code += c
                deriv += d
        result["chapters"].append(
            {
                "chapter_index": ch["index"],
                "chapter_title": ch["title"],
                "math": math,
                "code": code,
                "derivation": deriv,
            }
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
