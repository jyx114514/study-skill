---
name: pdf-smart-reader
description: Read difficult PDFs on Windows with a light text path, cached local RapidOCR for scanned pages, and selective page-level vision checks.
---

# PDF Smart Reader

Use this explicit-only adapter for PDFs that may be scanned, have a damaged text
layer, or contain pages whose meaning requires visual inspection. It augments the
official PDF skill; it never replaces or modifies it.

## Fixed runtime and storage

The literal Windows root is `D:\codex_pdf`. Do not insert another path separator
between `codex` and `_pdf`.

- RapidOCR Python: `D:\codex_pdf\rapidocr-venv\Scripts\python.exe`
- persistent OCR text cache: `D:\codex_pdf\cache`
- temporary sessions: `D:\codex_pdf\temp\<session-id>`
- OCR page images: `...\<session-id>\ocr`
- model-vision page images: `...\<session-id>\vision`

Use the bundled Codex Python returned by `load_workspace_dependencies` to run the
adapter scripts. The adapter invokes only the fixed RapidOCR Python for OCR. Do
not install packages or substitute a global Python.

Set these paths for the current installation and check dependencies first:

```powershell
$skill = "$env:USERPROFILE\.agents\skills\pdf-smart-reader"
$python = "<bundled-python-from-load_workspace_dependencies>"
& $python "$skill\scripts\pdf_router.py" doctor
```

`doctor` must report the exact root and interpreter above, `rapidocr`,
`onnxruntime`, `pdfplumber`, and `pdftoppm` as ready before OCR work.

## Route before reading

```powershell
& $python "$skill\scripts\pdf_router.py" inspect "C:\path\document.pdf"
```

The result uses `route: light` when the sampled native text layer is usable and
`route: ocr` when it is missing, sparse, or measurably garbled.

### Light route

Continue with the official PDF skill and `pdfplumber` or `pypdf`. Do not render
or OCR the document. `layout_candidates` are conservative hints, not a reason to
discard a healthy text layer; inspect only pages whose figures, tables, formulas,
or layout matter to the user's request.

### OCR route

```powershell
& $python "$skill\scripts\pdf_router.py" ocr "C:\path\document.pdf"
```

The adapter renders one page at a time with `pdftoppm`, invokes RapidOCR 3.x in
the fixed environment, records text and available confidence data, deletes that
page PNG immediately, and cleans the entire session in `finally`. Read page text
from the returned cache JSON.

The cache filename is human-readable and content-addressed:
`<PDF-stem>__<SHA256-first-8>.ocr.json`. A cache hit is accepted only when the
stored full SHA-256 and processing schema match. Cache JSON contains source name,
full hash, backend and versions, processing schema, page numbers, text, and
quality values. Never cache OCR or vision PNGs.

## Automatic signals versus model judgment

The scripts may automatically detect only observable signals:

- native text is missing, sparse, or contains replacement/control/CID text;
- OCR produced no/very little text;
- OCR confidence is low or a large share of detections is low-confidence;
- OCR or rendering failed;
- lightweight geometry suggests columns, tables, or formula-heavy text.

These signals may nominate `vision_candidates`. They do not determine whether a
page contains a scientifically meaningful illustration, whether a diagram is
important, or how a complex layout should be interpreted. Those are semantic
judgments for the model in the context of the user's request. If a reliable
lightweight rule cannot distinguish a real illustration from a scanned page
background, prefer conservative whole-page vision over a heavy layout detector.

## Selective native vision

Render only the candidate or user-relevant pages, not an entire scanned book:

```powershell
& $python "$skill\scripts\render_pages.py" "C:\path\document.pdf" --pages 3,8
```

Run this command in a PTY. It prints absolute paths under
`D:\codex_pdf\temp\<session-id>\vision` and waits. Keep that PTY and session
alive until the model has completed visual inspection.

Use the printed PNG as native model-vision input:

1. Try Codex `view_image` on the exact printed path.
2. If the visual attachment reader rejects the D-drive path, request read access
   for that exact PNG or its session `vision` directory.
3. Through the approved file-reading channel, read the same PNG's original bytes
   and forward those bytes as an image content item to the model's native vision.
   Do not paste base64 as ordinary prompt text, copy the PNG into the project, or
   change the fixed runtime root.
4. After the model has inspected every required page, send Enter to the waiting
   renderer. Its `finally` block removes the complete session, including the PNGs.
5. Confirm the printed `cleaned` path no longer exists. If file-read approval is
   denied or no available channel can deliver the bytes as image content, report
   that visual inspection is blocked; do not claim the page was inspected.

This is the supported handoff to native model vision, not an assumed external
vision API. PNGs remain only in the prescribed temporary directory until cleanup.

## Boundaries

- Use page-level vision escalation; do not add a layout detector for figure crops.
- Do not make MinerU, Docling, PP-Structure, Surya, Torch, or Paddle dependencies
  or fallback routes.
- A missing `pdftoppm` blocks OCR rendering and visual checks, but not the light
  text route.
- A failed OCR run must not publish a completed cache file.
- Preserve persistent JSON cache entries; remove temporary session data.
- Never modify the source PDF or the official PDF skill.
