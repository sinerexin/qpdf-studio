"""Read-only ordered PDF page content-operator diagnostics using libqpdf.

The report distinguishes observed graphics operators from interpretation.
White fills and hidden text are legitimate in some PDFs; their presence is not
proof of document alteration or fraud.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pikepdf

from .core import encode


GRAPHICS = {"m", "l", "c", "v", "y", "h", "re", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n", "W", "W*"}
TEXT = {"BT", "ET", "Tf", "Tm", "Td", "TD", "T*", "Tj", "TJ", "'", '"', "Tr", "Ts", "Tc", "Tw", "Tz", "TL"}
IMAGE = {"Do", "BI", "ID", "EI"}
COLOR = {"rg", "RG", "g", "G", "k", "K", "cs", "CS", "sc", "SC", "scn", "SCN"}


def inspect_paint_operations(source: Path, *, max_pages: int = 200,
                             max_events_per_page: int = 20_000) -> dict[str, Any]:
    """Enumerate content stream operators in native order, without rewriting PDF."""
    source = source.resolve(strict=True)
    if not 1 <= max_pages <= 1000 or not 1 <= max_events_per_page <= 100_000:
        raise ValueError("Invalid page/operator inspection limits")  # 限制：绘制诊断的页数或操作数上限不合法
    if source.stat().st_size > 150_000_000:
        raise ValueError("PDF exceeds analysis size limit")  # 限制：PDF 超过绘制诊断的大小上限
    result: dict[str, Any] = {
        "schema": "qpdf-studio/paint-diagnostics-v1",
        "source": source.name,
        "pages": [],
        "notes": [
            "Operator ordering reflects parsed page content streams; Form XObjects can contain nested drawing commands not recursively expanded here",
            "White fills, invisible-text modes, clip paths, and overlays can be legitimate PDF construction techniques",
            "This does not prove when or by whom the file was changed",
        ],
    }
    with pikepdf.Pdf.open(source) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            if page_number > max_pages:
                result["truncated_pages"] = len(pdf.pages) - max_pages
                break
            events: list[dict] = []
            counters: Counter = Counter()
            flags: list[dict] = []
            text_render_mode = 0
            fill_gray: float | None = None
            for index, op in enumerate(pikepdf.parse_content_stream(page)):
                name = str(op.operator)
                counters[name] += 1
                if index >= max_events_per_page:
                    flags.append({"kind": "event_limit", "operator_index": index})
                    break
                kind = (
                    "text" if name in TEXT else "drawing" if name in GRAPHICS else
                    "xobject_or_image" if name in IMAGE else "color" if name in COLOR else
                    "state"
                )
                operands = [encode(v) for v in op.operands]
                event = {"index": index, "operator": name, "kind": kind,
                         "operands": operands[:64]}
                if name == "Tr" and op.operands:
                    try:
                        text_render_mode = int(op.operands[0])
                    except (ValueError, TypeError):
                        text_render_mode = 0
                    if text_render_mode == 3:
                        flags.append({"kind": "invisible_text_mode", "operator_index": index,
                                      "interpretation": "No text paint, but text extraction/search may still work"})
                if name in ("Tj", "TJ", "'", '"') and text_render_mode == 3:
                    event["text_render_mode"] = 3
                if name == "g" and op.operands:
                    try:
                        fill_gray = float(op.operands[0])
                    except (TypeError, ValueError):
                        fill_gray = None
                if name == "rg" and len(op.operands) == 3:
                    try:
                        values = [float(v) for v in op.operands]
                        fill_gray = 1.0 if all(abs(v - 1) < 0.001 for v in values) else None
                    except (TypeError, ValueError):
                        fill_gray = None
                if name in {"f", "F", "f*"} and fill_gray == 1.0:
                    flags.append({"kind": "white_path_fill", "operator_index": index,
                                  "interpretation": "Page content paints a white path; may be ordinary background"})
                if name == "Do":
                    flags.append({"kind": "xobject_draw", "operator_index": index,
                                  "resource_operand": operands[0] if operands else None})
                events.append(event)
            result["pages"].append({
                "page": page_number,
                "page_object_ref": f"{page.obj.objgen[0]} {page.obj.objgen[1]} R",
                "operator_counts": dict(counters),
                "events": events,
                "observations": flags,
                "events_exported": len(events),
            })
    return result
