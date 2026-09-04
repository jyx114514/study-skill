#!/usr/bin/env python3
"""Route PDFs to native text or cached page-by-page RapidOCR."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


PROCESSING_SCHEMA = "pdf-smart-reader-rapidocr-v2"
DEFAULT_SAMPLE_PAGES = 12
PDF_ROOT = Path(r"D:\codex_pdf")
RAPIDOCR_PYTHON = PDF_ROOT / "rapidocr-venv" / "Scripts" / "python.exe"
CACHE_ROOT = PDF_ROOT / "cache"
TEMP_ROOT = PDF_ROOT / "temp"
WORKER = Path(__file__).with_name("rapidocr_worker.py")
FORMULA_CHARS = frozenset("∑∫√∞≈≠≤≥±×÷∂∇αβγδεζηθικλμνξοπρστυφχψω")


def _pdf_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_stem(stem: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).rstrip(" .")
    return value or "document"


def _cache_path(pdf: Path, source_hash: str, cache_root: Path = CACHE_ROOT) -> Path:
    return cache_root / f"{_safe_stem(pdf.stem)}__{source_hash[:8]}.ocr.json"


def _sample_indices(page_count: int, limit: int) -> list[int]:
    if page_count <= limit:
        return list(range(page_count))
    if limit < 2:
        return [0]
    return sorted({round(i * (page_count - 1) / (limit - 1)) for i in range(limit)})


def _is_bad_char(char: str) -> bool:
    code = ord(char)
    return char == "\ufffd" or (code < 32 and char not in "\n\r\t") or 0xE000 <= code <= 0xF8FF


def _column_evidence(words: list[dict[str, Any]], width: float) -> bool:
    if len(words) < 40 or width <= 0:
        return False
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        lines.setdefault(round(float(word["top"]) / 4), []).append(word)
    left_only = right_only = 0
    gutter_left, gutter_right = width * 0.44, width * 0.56
    for line in lines.values():
        x0 = min(float(word["x0"]) for word in line)
        x1 = max(float(word["x1"]) for word in line)
        if x1 < gutter_left:
            left_only += 1
        elif x0 > gutter_right:
            right_only += 1
    return left_only >= 6 and right_only >= 6


def _page_metrics(page: Any) -> dict[str, Any]:
    text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
    nonspace = [char for char in text if not char.isspace()]
    bad_count = sum(_is_bad_char(char) for char in nonspace)
    cid_count = len(re.findall(r"\(cid:\d+\)", text, flags=re.IGNORECASE))
    formula_count = sum(char in FORMULA_CHARS for char in nonspace)
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
    try:
        table_count = len(page.find_tables())
    except Exception:
        table_count = 0
    char_count = len(nonspace)
    garbage_ratio = (bad_count + cid_count) / max(char_count, 1)
    formula_ratio = formula_count / max(char_count, 1)
    columns = _column_evidence(words, float(page.width))
    layout_reasons: list[str] = []
    if columns:
        layout_reasons.append("multi_column_geometry")
    if table_count:
        layout_reasons.append("table_geometry")
    if formula_ratio >= 0.015:
        layout_reasons.append("formula_heavy_text")
    return {
        "page": page.page_number,
        "characters": char_count,
        "words": len(words),
        "images": len(page.images or []),
        "tables": table_count,
        "garbage_ratio": round(garbage_ratio, 4),
        "formula_ratio": round(formula_ratio, 4),
        "sparse_text": char_count < 50,
        "layout_reasons": layout_reasons,
    }


def inspect_pdf(pdf: Path, sample_limit: int = DEFAULT_SAMPLE_PAGES) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is unavailable; use the bundled Codex Python") from exc

    result: dict[str, Any] = {"source": str(pdf), "schema": PROCESSING_SCHEMA}
    try:
        with pdfplumber.open(pdf) as document:
            page_count = len(document.pages)
            indices = _sample_indices(page_count, max(1, sample_limit))
            pages = [_page_metrics(document.pages[index]) for index in indices]
    except Exception as exc:
        result.update(
            route="ocr",
            reasons=["native_text_extraction_failed"],
            error=f"{type(exc).__name__}: {exc}",
            layout_candidates=[],
        )
        return result

    characters = [page["characters"] for page in pages]
    sparse_share = sum(page["sparse_text"] for page in pages) / max(len(pages), 1)
    garbage_share = sum(page["garbage_ratio"] >= 0.02 for page in pages) / max(len(pages), 1)
    reasons: list[str] = []
    if not pages or statistics.median(characters) < 50 or sparse_share >= 0.5:
        reasons.append("missing_or_sparse_text_layer")
    if garbage_share >= 0.2:
        reasons.append("garbled_text_layer")
    layout_candidates = [
        {"page": page["page"], "reasons": page["layout_reasons"]}
        for page in pages
        if page["layout_reasons"]
    ]
    result.update(
        page_count=page_count,
        sampled_pages=[page["page"] for page in pages],
        route="ocr" if reasons else "light",
        reasons=reasons or ["usable_native_text_layer"],
        summary={
            "median_characters": statistics.median(characters) if characters else 0,
            "sparse_page_share": round(sparse_share, 3),
            "garbled_page_share": round(garbage_share, 3),
        },
        pages=pages,
        layout_candidates=layout_candidates,
    )
    return result


def _run_worker(*arguments: str, timeout: int = 300) -> dict[str, Any]:
    if not RAPIDOCR_PYTHON.is_file():
        raise RuntimeError(f"Fixed RapidOCR Python is missing: {RAPIDOCR_PYTHON}")
    if not WORKER.is_file():
        raise RuntimeError(f"RapidOCR bridge is missing: {WORKER}")
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [str(RAPIDOCR_PYTHON), "-X", "utf8", str(WORKER), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"RapidOCR worker failed ({completed.returncode}): {message[-4000:]}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RapidOCR worker returned invalid JSON") from exc


def _ocr_quality(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    nonspace_count = sum(not char.isspace() for char in payload.get("text", ""))
    mean_confidence = payload.get("mean_confidence")
    low_share = payload.get("low_confidence_share")
    if nonspace_count < 20:
        reasons.append("empty_or_very_sparse_ocr_text")
    if mean_confidence is not None and mean_confidence < 0.6:
        reasons.append("low_mean_ocr_confidence")
    if low_share is not None and low_share >= 0.4:
        reasons.append("many_low_confidence_detections")
    return reasons


def _read_valid_cache(cache_path: Path, source_hash: str) -> dict[str, Any] | None:
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("status") != "complete"
        or payload.get("source_sha256") != source_hash
        or payload.get("processing_schema") != PROCESSING_SCHEMA
    ):
        return None
    return payload


def _page_count(pdf: Path) -> int:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is unavailable; use the bundled Codex Python") from exc
    with pdfplumber.open(pdf) as document:
        return len(document.pages)


def run_ocr(
    pdf: Path,
    cache_root: Path = CACHE_ROOT,
    temp_root: Path = TEMP_ROOT,
    dpi: int = 200,
    timeout: int = 300,
) -> dict[str, Any]:
    source_hash = _sha256(pdf)
    cache_path = _cache_path(pdf, source_hash, cache_root.resolve())
    cached = _read_valid_cache(cache_path, source_hash)
    if cached is not None:
        return {
            "source": str(pdf),
            "route": "ocr",
            "cache_hit": True,
            "cache_published": True,
            "cache_path": str(cache_path),
            "source_sha256": source_hash,
            "vision_candidates": cached.get("vision_candidates", []),
            "pages": cached.get("pages", []),
        }

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is unavailable")
    backend = _run_worker("doctor", timeout=30)
    cache_root = cache_root.resolve()
    temp_root = temp_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix=f"{_safe_stem(pdf.stem)}-", dir=temp_root))
    ocr_dir = session / "ocr"
    vision_dir = session / "vision"
    ocr_dir.mkdir()
    vision_dir.mkdir()
    staging = cache_root / f".{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        pages: list[dict[str, Any]] = []
        vision_candidates: list[dict[str, Any]] = []
        page_failures = False
        for page_number in range(1, _page_count(pdf) + 1):
            prefix = ocr_dir / f"page-{page_number:04d}"
            image = prefix.with_suffix(".png")
            try:
                completed = subprocess.run(
                    [
                        pdftoppm,
                        "-f", str(page_number),
                        "-l", str(page_number),
                        "-r", str(dpi),
                        "-singlefile",
                        "-png",
                        str(pdf),
                        str(prefix),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if completed.returncode != 0 or not image.is_file():
                    detail = (completed.stderr or completed.stdout).strip()
                    raise RuntimeError(f"pdftoppm failed for page {page_number}: {detail[-2000:]}")
                page_payload = _run_worker("recognize", str(image), timeout=timeout)
                quality_reasons = _ocr_quality(page_payload)
            except Exception as exc:
                page_failures = True
                quality_reasons = ["ocr_page_failed"]
                page_payload = {
                    "text": "",
                    "lines": [],
                    "detection_count": 0,
                    "mean_confidence": None,
                    "min_confidence": None,
                    "low_confidence_share": None,
                    "elapsed_seconds": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                image.unlink(missing_ok=True)
            page_payload = {"page": page_number, **page_payload, "quality_signals": quality_reasons}
            pages.append(page_payload)
            if quality_reasons:
                vision_candidates.append({"page": page_number, "reasons": quality_reasons})

        if page_failures:
            return {
                "source": str(pdf),
                "route": "ocr",
                "status": "partial",
                "cache_hit": False,
                "cache_published": False,
                "cache_path": None,
                "source_sha256": source_hash,
                "vision_candidates": vision_candidates,
                "pages": pages,
            }

        cache_payload = {
            "status": "complete",
            "processing_schema": PROCESSING_SCHEMA,
            "source_name": pdf.name,
            "source_sha256": source_hash,
            "backend": "rapidocr",
            "rapidocr_version": backend["rapidocr_version"],
            "onnxruntime_version": backend["onnxruntime_version"],
            "render_dpi": dpi,
            "created_unix": time.time(),
            "pages": pages,
            "vision_candidates": vision_candidates,
        }
        staging.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, cache_path)
        return {
            "source": str(pdf),
            "route": "ocr",
            "cache_hit": False,
            "cache_published": True,
            "cache_path": str(cache_path),
            "source_sha256": source_hash,
            "vision_candidates": vision_candidates,
            "pages": pages,
        }
    finally:
        staging.unlink(missing_ok=True)
        shutil.rmtree(session, ignore_errors=True)


def doctor() -> dict[str, Any]:
    try:
        import pdfplumber  # noqa: F401
        pdfplumber_ok = True
    except ImportError:
        pdfplumber_ok = False
    poppler = shutil.which("pdftoppm")
    backend: dict[str, Any] | None = None
    backend_error: str | None = None
    if RAPIDOCR_PYTHON.is_file():
        try:
            backend = _run_worker("doctor", timeout=30)
        except Exception as exc:
            backend_error = f"{type(exc).__name__}: {exc}"
    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "pdf_root": str(PDF_ROOT),
        "rapidocr_python": str(RAPIDOCR_PYTHON),
        "rapidocr_python_exists": RAPIDOCR_PYTHON.is_file(),
        "rapidocr": backend,
        "rapidocr_error": backend_error,
        "pdfplumber": pdfplumber_ok,
        "pdftoppm": poppler,
        "cache_root": str(CACHE_ROOT),
        "temp_root": str(TEMP_ROOT),
        "ready_light": pdfplumber_ok,
        "ready_ocr": bool(pdfplumber_ok and poppler and backend and backend.get("ready")),
        "ready_visual": poppler is not None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="check the light, OCR, and render dependencies")
    inspect_parser = subparsers.add_parser("inspect", help="classify native text without changing the PDF")
    inspect_parser.add_argument("pdf")
    inspect_parser.add_argument("--sample-pages", type=int, default=DEFAULT_SAMPLE_PAGES)
    ocr_parser = subparsers.add_parser("ocr", help="run or reuse cached page-by-page RapidOCR")
    ocr_parser.add_argument("pdf")
    ocr_parser.add_argument("--dpi", type=int, default=200)
    ocr_parser.add_argument("--timeout", type=int, default=300)
    ocr_parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    ocr_parser.add_argument("--temp-root", type=Path, default=TEMP_ROOT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "doctor":
            payload = doctor()
        elif args.command == "inspect":
            payload = inspect_pdf(_pdf_path(args.pdf), args.sample_pages)
        else:
            payload = run_ocr(
                _pdf_path(args.pdf),
                cache_root=args.cache_root,
                temp_root=args.temp_root,
                dpi=args.dpi,
                timeout=args.timeout,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
