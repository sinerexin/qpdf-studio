"""Read-only, safe export of embedded PDF attachments.

Source PDF remains untouched. Output filenames are generated rather than
trusting embedded PDF names as filesystem paths.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader


MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024


def _safe_label(value: str) -> str:
    leaf = str(value).replace("\\", "/").split("/")[-1]
    leaf = re.sub(r"[^\w.\- ]", "_", leaf, flags=re.UNICODE)
    leaf = leaf.strip(" ._")
    return leaf[:100] or "attachment"


def extract_attachments(source: Path, destination: Path) -> dict:
    """Extract PDF attachments into a *new* directory with audit metadata."""
    source = source.resolve(strict=True)
    target = destination.resolve()
    if target.exists():
        raise ValueError("Choose a new, empty destination directory")
    if source.stat().st_size > 150_000_000:
        raise ValueError("PDF exceeds safe attachment scanning limit")  # 限制：PDF 超过附件扫描的安全大小
    reader = PdfReader(str(source), strict=False)
    exported: list[dict] = []
    total_size = 0
    target.mkdir(parents=True, exist_ok=False)
    try:
        for index, (filename, payloads) in enumerate(reader.attachments.items(), start=1):
            if index > 500:
                raise ValueError("Too many attachment names")
            values = payloads if isinstance(payloads, list) else [payloads]
            for copy_index, data in enumerate(values, start=1):
                if not isinstance(data, bytes):
                    raise ValueError(f"Attachment {index} is not a raw byte stream")
                if len(data) > MAX_ATTACHMENT_BYTES or total_size + len(data) > MAX_TOTAL_BYTES:
                    raise ValueError("Embedded attachment size limit exceeded")  # 限制：内嵌附件超过允许大小
                total_size += len(data)
                safe = _safe_label(filename)
                outfile = target / f"{index:03d}-{copy_index:02d}-{safe}"
                # Generated prefix prevents collisions and traversal via attachment names.
                if outfile.parent != target:
                    raise ValueError("Attachment output path escaped destination")
                with outfile.open("xb") as handle:
                    handle.write(data)
                exported.append({
                    "source_name": str(filename), "exported_as": outfile.name,
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                })
        report = {
            "operation": "extract-pdf-attachments",
            "input_pdf": source.name,
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "attachment_count": len(exported),
            "attachments": exported,
            "note": "Embedded files are untrusted content; examine them with appropriate precautions",
        }
        (target / "manifest.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report
    except Exception:
        # Incomplete extraction is left in place for debugging; do not claim success.
        (target / "INCOMPLETE.txt").write_text(
            "Attachment extraction failed before completion. Do not use as a verified export.",
            encoding="utf-8",
        )
        raise
