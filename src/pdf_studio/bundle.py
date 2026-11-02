"""One-click read-only analysis bundle for documents the user may access."""
from __future__ import annotations

from pathlib import Path
import json

from .core import inspect_pdf
from .deep import semantic_inventory
from .signature_audit import audit_signatures
from .visual import render_document
from .revisions import inspect_revisions
from .paint_diagnostics import inspect_paint_operations


def analyze_complete(source: Path, destination: Path, *, streams: bool = True,
                     assets: bool = True, render: bool = True,
                     max_pages: int = 150, max_glyphs: int = 25000) -> dict:
    source = source.resolve(strict=True)
    dest = destination.resolve()
    if dest.exists():
        raise ValueError("Output directory already exists; choose an unused path")  # 限制：输出目录必须是尚未存在的新路径
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("The input must be a PDF file")
    if not 1 <= max_pages <= 10000:
        raise ValueError("Invalid max_pages")
    dest.mkdir(parents=True, exist_ok=False)
    output = {
        "format": "qpdf-studio-bundle/0.1",
        "input_file": source.name,
        "status": "incomplete",
        "files": {},
        "limitations": [
            "Signature integrity and certificate trust are independent",
            "Text extraction does not replace OCR for scanned images",  # 限制：文字提取不能代替扫描件 OCR
            "Historic data overwritten during previous re-saves cannot be recovered from this bundle",
        ],
    }
    index = dest / "index.json"

    def save_index():
        index.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    save_index()
    try:
        raw = inspect_pdf(source, dest / "objects", streams=streams,
                          assets=assets, operators=True, text_geometry=True)
        output["source_sha256"] = raw["source"]["sha256"]
        output["pages"] = raw["page_count"]
        output["objects"] = raw["object_count"]
        output["files"]["objects"] = "objects/manifest.json"
        rich = semantic_inventory(source, max_pages=max_pages,
                                  max_glyphs_per_page=max_glyphs)
        (dest / "semantics.json").write_text(
            json.dumps(rich, ensure_ascii=False, indent=2), encoding="utf-8")
        output["files"]["semantics"] = "semantics.json"
        output["forms"] = len(rich["form_fields"])
        output["attachments"] = len(rich["attachments"])
        output["signatures_found"] = len(raw["signatures"])
        revisions = inspect_revisions(source)
        (dest / "revisions.json").write_text(
            json.dumps(revisions, ensure_ascii=False, indent=2), encoding="utf-8")
        output["files"]["revisions"] = "revisions.json"
        output["revision_chain_status"] = revisions["chain_status"]
        paint = inspect_paint_operations(source, max_pages=min(max_pages, 100))
        (dest / "paint.json").write_text(
            json.dumps(paint, ensure_ascii=False, indent=2), encoding="utf-8")
        output["files"]["paint"] = "paint.json"
        if raw["signatures"]:
            signatures = audit_signatures(source)
            (dest / "signatures.json").write_text(
                json.dumps(signatures, ensure_ascii=False, indent=2), encoding="utf-8")
            output["files"]["signatures"] = "signatures.json"
        if render:
            visual = render_document(source, dest / "pages", max_pages=min(max_pages, 100))
            output["rendered_pages"] = visual["rendered_page_count"]
            output["files"]["pages"] = "pages/"
        output["status"] = "complete"
    except Exception as exc:
        output["error"] = f"{type(exc).__name__}: {exc}"
        save_index()
        raise
    save_index()
    return output
