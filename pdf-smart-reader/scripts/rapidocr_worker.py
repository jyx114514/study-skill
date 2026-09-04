#!/usr/bin/env python3
"""Small JSON bridge executed only by the dedicated RapidOCR environment."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def doctor() -> dict[str, object]:
    from rapidocr import RapidOCR  # noqa: F401
    import onnxruntime  # noqa: F401

    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "rapidocr_version": _version("rapidocr"),
        "onnxruntime_version": _version("onnxruntime"),
        "ready": True,
    }


def recognize(image: Path) -> dict[str, object]:
    if not image.is_file():
        raise FileNotFoundError(f"Image not found: {image}")

    from rapidocr import RapidOCR

    result = RapidOCR()(image)
    texts = list(result.txts or ())
    scores = [float(score) for score in (result.scores or ())]
    lines = [
        {"text": text, "confidence": score}
        for text, score in zip(texts, scores)
    ]
    return {
        "text": "\n".join(texts),
        "lines": lines,
        "detection_count": len(texts),
        "mean_confidence": (sum(scores) / len(scores)) if scores else None,
        "min_confidence": min(scores) if scores else None,
        "low_confidence_share": (
            sum(score < 0.6 for score in scores) / len(scores) if scores else None
        ),
        "elapsed_seconds": float(result.elapse),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    recognize_parser = subparsers.add_parser("recognize")
    recognize_parser.add_argument("image", type=Path)
    args = parser.parse_args()

    try:
        payload = doctor() if args.command == "doctor" else recognize(args.image.resolve())
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
