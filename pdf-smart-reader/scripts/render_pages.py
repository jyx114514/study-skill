#!/usr/bin/env python3
"""Render selected PDF pages for native model vision and always clean the session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


TEMP_ROOT = Path(r"D:\codex_pdf\temp")


def _parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError("Page numbers are one-based")
            pages.add(page)
    if not pages:
        raise ValueError("No pages selected")
    return sorted(pages)


def render_for_vision(
    pdf: Path,
    pages: list[int],
    dpi: int = 150,
    temp_root: Path = TEMP_ROOT,
    wait: bool = True,
) -> int:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is unavailable")
    temp_root = temp_root.resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="vision-", dir=temp_root))
    vision_dir = session / "vision"
    vision_dir.mkdir()
    try:
        rendered: list[str] = []
        for page in pages:
            prefix = vision_dir / f"page-{page:04d}"
            completed = subprocess.run(
                [
                    pdftoppm,
                    "-f", str(page),
                    "-l", str(page),
                    "-r", str(dpi),
                    "-singlefile",
                    "-png",
                    str(pdf),
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            output = prefix.with_suffix(".png")
            if completed.returncode != 0 or not output.is_file():
                detail = (completed.stderr or completed.stdout).strip()
                raise RuntimeError(f"pdftoppm failed for page {page}: {detail[-2000:]}")
            rendered.append(str(output))
        print(
            json.dumps(
                {
                    "session": str(session),
                    "vision_dir": str(vision_dir),
                    "pngs": rendered,
                    "instruction": "Open each listed PNG with Codex view_image, then send Enter.",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if wait:
            input("Inspect the PNGs with model vision, then press Enter to clean the session... ")
        return 0
    finally:
        shutil.rmtree(session, ignore_errors=True)
        print(json.dumps({"cleaned": str(session), "exists": session.exists()}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--pages", required=True, help="one-based pages, for example 3,8-9")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--temp-root", type=Path, default=TEMP_ROOT)
    parser.add_argument("--no-wait", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        parser.error(f"PDF not found: {pdf}")
    pages = _parse_pages(args.pages)
    if len(pages) > args.max_pages:
        parser.error(f"Selected {len(pages)} pages; limit is {args.max_pages}")
    try:
        return render_for_vision(pdf, pages, args.dpi, args.temp_root, not args.no_wait)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
