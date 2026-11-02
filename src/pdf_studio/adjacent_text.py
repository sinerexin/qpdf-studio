"""Direct editing across consecutive PDF text-showing operations.

Only uninterrupted Tj/TJ operations within one BT..ET text object, sharing
the same verified font, are merged into an editable logical span. Numeric
TJ kerning and all drawing/state operations are preserved byte-semantically.
No white paint, background cover, page overlay or visual fake replacement.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from .editing import page_contents_ready
from .replace_text import _collect_runs, _encode_for_run, _hash, list_text_runs
from .text_metrics import describe_layout_change


def _spans_on_page(page: pikepdf.Page) -> list[dict]:
    operations = list(pikepdf.parse_content_stream(page))
    runs_by_operator: dict[int, list[dict]] = {}
    for row in _collect_runs(page):
        runs_by_operator.setdefault(row["operator_index"], []).append(row)
    spans: list[dict] = []
    current: list[dict] = []
    current_indices: list[int] = []
    inside_text = False

    def flush() -> None:
        if len(current_indices) >= 2 and len({row["font_resource"] for row in current}) == 1:
            if all(row["editable"] for row in current) and current:
                spans.append({
                    "start_operator_index": current_indices[0],
                    "end_operator_index": current_indices[-1],
                    "operators": list(current_indices),
                    "fragment_count": len(current),
                    "text": "".join(row["text"] for row in current),
                    "font_resource": current[0]["font_resource"],
                    "font_encoding": current[0]["font_encoding"],
                    "restriction": "Consecutive Tj/TJ operations only; fixed Unicode length and no reflow",
                })
        current.clear()
        current_indices.clear()

    for index, operation in enumerate(operations):
        name = str(operation.operator)
        if name == "BT":
            flush()
            inside_text = True
        elif name == "ET":
            flush()
            inside_text = False
        elif inside_text and name in {"Tj", "TJ"}:
            rows = sorted(
                runs_by_operator.get(index, []),
                key=lambda row: row["array_index"] if row["array_index"] is not None else -1,
            )
            if not rows or not all(row["editable"] for row in rows):
                flush()
                continue
            if current and current[0]["font_resource"] != rows[0]["font_resource"]:
                flush()
            current_indices.append(index)
            current.extend(rows)
        else:
            flush()
    flush()
    return spans


def list_adjacent_text_spans(source: Path, page: int) -> list[dict]:
    with pikepdf.Pdf.open(Path(source).resolve(strict=True)) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        return _spans_on_page(pdf.pages[page - 1])


def replace_adjacent_text_span(
    source: Path, destination: Path, *, page: int,
    start_operator_index: int, expected_text: str, replacement: str,
) -> dict:
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if src == dst or dst.exists():
        raise ValueError("Destination must be a new unused PDF file")  # 限制：必须另存为新的未使用 PDF，不能覆盖原稿
    if (not expected_text or expected_text == replacement
            or len(expected_text) > 3000 or len(expected_text) != len(replacement)
            or "\n" in replacement or "\r" in replacement):
        raise ValueError("Consecutive operation replacement requires same nonzero text length")  # 限制：连续指令替换必须字符数相同
    if not isinstance(start_operator_index, int) or start_operator_index < 0:
        raise ValueError("Invalid starting PDF content operator")
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        span = next(
            (row for row in _spans_on_page(sheet)
             if row["start_operator_index"] == start_operator_index),
            None,
        )
        if span is None or span["text"] != expected_text:
            raise ValueError("Adjacent text operations no longer match selected span")
        ops = list(pikepdf.parse_content_stream(sheet))
        all_runs = _collect_runs(sheet)
        selected_runs = sorted(
            (row for row in all_runs if row["operator_index"] in span["operators"]),
            key=lambda row: (
                row["operator_index"], row["array_index"]
                if row["array_index"] is not None else -1,
            ),
        )
        position = 0
        report_segments = []
        for row in selected_runs:
            length = len(row["text"])
            new_piece = replacement[position:position + length]
            position += length
            instr = ops[row["operator_index"]]
            args = list(instr.operands)
            if row["array_index"] is None:
                old_obj = args[row["operand_index"]]
            else:
                old_obj = args[row["operand_index"]][row["array_index"]]
            if bytes(old_obj).hex() != row["source_bytes_hex"]:
                raise ValueError("Selected PDF text bytes have changed")
            encoded = _encode_for_run(sheet, all_runs, row, row["text"], new_piece)
            new_object = pikepdf.String(
                encoded if row["font_encoding"] == "embedded-to-unicode-cmap" else new_piece
            )
            if bytes(new_object) != encoded:
                raise ValueError("Replacement encoded glyph bytes do not match")
            if row["array_index"] is None:
                args[row["operand_index"]] = new_object
            else:
                array = list(args[row["operand_index"]])
                array[row["array_index"]] = new_object
                args[row["operand_index"]] = pikepdf.Array(array)
            ops[row["operator_index"]] = pikepdf.ContentStreamInstruction(args, instr.operator)
            report_segments.append({
                "operator_index": row["operator_index"],
                "array_index": row["array_index"],
                "before": row["text"], "after": new_piece,
            })
        sheet.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf.save(dst)
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    after = next((row for row in list_adjacent_text_spans(dst, page)
                  if row["start_operator_index"] == start_operator_index), None)
    if after is None or after["text"] != replacement:
        dst.unlink(missing_ok=True)
        raise ValueError("Saved PDF failed cross-operator text verification")
    report = {
        "operation": "replace-adjacent-PDF-text-operators",
        "source": src.name, "source_sha256": _hash(src),
        "output": dst.name, "output_sha256": _hash(dst),
        "page": page, "first_operator_index": start_operator_index,
        "operators": span["operators"],
        "old_text": expected_text, "new_text": replacement,
        "segments": report_segments,
        "method": "Direct content-stream operand rewrite; no overlay or cover",
        "layout_warning": "Font metrics can change visual width; check PDF preview; no automatic reflow",
        "layout_diagnostics": describe_layout_change(
            src, dst, page=page, original=expected_text, replacement=replacement,
        ),
    }
    return report
