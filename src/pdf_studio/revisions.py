"""Read-only PDF revision and signature byte-range forensics.

Important: markers and byte ranges are structural evidence, not authenticity
verdicts. This module intentionally never modifies input PDF bytes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pikepdf

from .purepdf import PdfDocument, PdfSyntaxError
from .editing import _signed_or_certified

MAX_FILE_BYTES = 150_000_000


def inspect_revisions(source: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Input exceeds revision-analysis size limit")  # 限制：文件超过修订分析的大小上限
    raw = source.read_bytes()
    marker_locations = [m.start() for m in re.finditer(rb"%%EOF", raw)]
    xref_labels = [
        {"byte_offset": m.start(), "xref_target": int(m.group(1))}
        for m in re.finditer(rb"startxref\s+(\d+)", raw)
    ]
    report: dict[str, Any] = {
        "schema": "qpdf-studio/revision-evidence-v1",
        "source": source.name,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "eof_marker_offsets": marker_locations,
        "startxref_markers": xref_labels,
        "revision_chain": [],
        "chain_status": "unavailable",
        "byte_ranges": [],
        "notes": [
            "EOF/startxref marker count is only a heuristic; embedded streams can contain these bytes",
            "Signed bytes may exclude later legitimate incremental revisions",
            "Signature presence or cryptographic integrity does not prove who issued the document",
        ],
    }
    try:
        parser = PdfDocument(raw)
        report["revision_chain"] = [
            {
                "xref_offset": item["xref_offset"],
                "has_prev": "/Prev" in item["trailer"],
                "trailer_size": item["trailer"].get("/Size"),
            }
            for item in parser.revisions
        ]
        report["chain_status"] = "parsed"
    except (PdfSyntaxError, ValueError) as error:
        report["chain_status"] = f"not_parsed: {error}"

    with pikepdf.Pdf.open(source) as pdf:
        report["signature_or_certification_structure_present"] = _signed_or_certified(pdf)  # 限制标记：报告记录是否存在签名或认证结构
        for value in pdf.objects:
            if not isinstance(value, pikepdf.Dictionary):
                continue
            br = value.get("/ByteRange")
            if br is None:
                continue
            row: dict[str, Any] = {
                "object_ref": f"{value.objgen[0]} {value.objgen[1]} R",
                "filter": str(value.get("/Filter", "")),
                "subfilter": str(value.get("/SubFilter", "")),
            }
            try:
                entries = [int(v) for v in br]
                row["byte_range"] = entries
                valid = (
                    len(entries) == 4 and entries[0] == 0 and
                    entries[1] >= 0 and entries[2] >= entries[1] and
                    entries[3] >= 0 and entries[2] + entries[3] <= len(raw)
                )
                row["structurally_plausible"] = valid
                if valid:
                    row["signed_revision_ends_at"] = entries[2] + entries[3]
                    row["bytes_after_signed_revision"] = len(raw) - (entries[2] + entries[3])
                    row["signed_content_sha256"] = hashlib.sha256(
                        raw[:entries[1]] + raw[entries[2]:entries[2] + entries[3]]
                    ).hexdigest()
                    row["signature_gap_bytes"] = entries[2] - entries[1]
                else:
                    row["error"] = "ByteRange lengths or offsets inconsistent with input bytes"
            except (TypeError, ValueError, OverflowError) as error:
                row["structurally_plausible"] = False
                row["error"] = str(error)
            report["byte_ranges"].append(row)
    return report
