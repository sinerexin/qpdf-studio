"""Fail-closed text-only cross-object layout for isolated PDF paragraphs.

This module never covers existing content. It calls the strict paragraph
content-stream rewriter and, when supported, changes actual Tm origins of
subsequent independent text objects in the same column.

It does not claim general collision detection for image-heavy or rotated PDFs.
"""
from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory

import pikepdf

from .editing import page_contents_ready
from .paragraph_reflow import list_reflow_blocks, reflow_text_object
from .replace_text import _collect_runs, _hash

_ALLOWED_INSIDE_TEXT = {"Tm", "Tf", "TL", "Tj", "T*"}


def _number(value) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite PDF coordinate")
    return value


def _text_objects(page: pikepdf.Page) -> list[dict]:
    """Return strictly positioned text objects; reject paint or complex state."""
    commands = list(pikepdf.parse_content_stream(page))
    runs = {(r["operator_index"], r["array_index"]): r for r in _collect_runs(page)}
    result: list[dict] = []
    start = None
    current = []
    for index, ins in enumerate(commands):
        name = str(ins.operator)
        if start is None:
            if name == "BT":
                start = index
                current = []
            elif name == "cm":
                if [_number(x) for x in ins.operands] != [1., 0., 0., 1., 0., 0.]:
                    raise ValueError("Transformed page graphics state is not supported")  # 限制：带变换的页面图形状态不支持自动重排
            elif name in {"q", "Q"}:
                raise ValueError("Scoped graphics state is not supported by automatic text flow")  # 限制：带局部图形状态的页面不支持自动文字流
            else:
                raise ValueError(f"Non-text painting or state instruction {name} prevents safe flow")
        elif name == "ET":
            positioned = [i for i in current if str(commands[i].operator) == "Tm"]
            shown = [i for i in current if str(commands[i].operator) == "Tj"]
            unsupported = [i for i in current
                           if str(commands[i].operator) not in _ALLOWED_INSIDE_TEXT]
            if unsupported or len(positioned) > 1:
                raise ValueError("Complex text-position or graphics command prevents safe flow")
            if shown:
                if len(positioned) != 1:
                    raise ValueError("Text object lacks a unique absolute Tm")
                tm_index = positioned[0]
                matrix = [_number(x) for x in commands[tm_index].operands]
                if len(matrix) != 6 or matrix[:4] != [1., 0., 0., 1.]:
                    raise ValueError("Rotated/skewed text matrices cannot be automatically moved")  # 限制：旋转或倾斜的文字不能自动挪动
                text_runs = [runs.get((i, None)) for i in shown]
                if any(row is None or not row["editable"] for row in text_runs):
                    raise ValueError("Unknown glyphs or encodings block safe text layout")
                result.append({
                    "start": start, "end": index, "tm_index": tm_index,
                    "x": matrix[4], "y": matrix[5],
                    "texts": [row["text"] for row in text_runs],
                    "font_resource": text_runs[0]["font_resource"],
                })
            start = None
            current = []
        else:
            if name not in _ALLOWED_INSIDE_TEXT:
                raise ValueError(f"Unsupported text command {name} prevents safe flow")
            current.append(index)
    if start is not None:
        raise ValueError("Unterminated PDF BT text block")
    return result


def plan_text_flow(source: Path, *, page: int, start_operator_index: int,
                   new_height_pt: float, horizontal_tolerance: float = 1.5,
                   margin_pt: float = 18.0) -> dict:
    """Preflight downstream text objects that can be shifted as one column."""
    if not math.isfinite(new_height_pt) or new_height_pt <= 0:
        raise ValueError("Invalid new paragraph height")
    path = Path(source).resolve(strict=True)
    with pikepdf.Pdf.open(path) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside PDF")
        sheet = pdf.pages[page - 1]
        if sheet.obj.get("/Annots") is not None:
            raise ValueError("PDF page annotations/forms may overlap text; automatic flow is unavailable")
        media = [float(value) for value in sheet.MediaBox]
        # CropBox and Rotate are inheritable in the PDF page tree.
        # Missing CropBox defaults to MediaBox; do not reject ordinary PDFs.
        cursor = sheet.obj
        for _ in range(40):
            rotated = cursor.get("/Rotate")
            if rotated is not None:
                if int(rotated) % 360 != 0:
                    raise ValueError("Rotated PDF page is not supported by automatic flow")  # 限制：旋转页面不支持自动重排
                break
            cursor = cursor.get("/Parent")
            if cursor is None:
                break
        cursor = sheet.obj
        for _ in range(40):
            crop = cursor.get("/CropBox")
            if crop is not None:
                if [float(value) for value in crop] != media:
                    raise ValueError("Cropped PDF pages require coordinate-aware layout processing")
                break
            cursor = cursor.get("/Parent")
            if cursor is None:
                break
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        matches = list_reflow_blocks(path, page)
        chosen = next((block for block in matches
                       if block["start_operator_index"] == start_operator_index), None)
        if chosen is None:
            raise ValueError("Selected paragraph is not a safely editable text object")
        objects = _text_objects(sheet)
        anchor = next((item for item in objects if item["start"] == start_operator_index), None)
        if anchor is None:
            raise ValueError("Anchor text object is not independently positioned")
        old_height = chosen["font_size_pt"] + (
            chosen["original_lines"] - 1
        ) * chosen["leading_pt"]
        shift = round(new_height_pt - old_height, 4)
        chosen_bottom = chosen["start_y_pt"] - old_height
        shifted = []
        for obj in objects:
            if obj["start"] == start_operator_index:
                continue
            if obj["y"] >= chosen["start_y_pt"] + 0.01:
                # Above the anchor; keep unchanged.
                continue
            if obj["y"] >= chosen_bottom - margin_pt:
                raise ValueError("A text object already overlaps the anchor paragraph")
            if abs(obj["x"] - anchor["x"]) > horizontal_tolerance:
                raise ValueError(
                    "Additional text belongs to another column; safe automatic flow is unavailable"
                )
            if obj["y"] - shift < media[1] + margin_pt:
                raise ValueError("Moving following text would cross the page's lower safety margin")
            if obj["y"] - shift > media[3] - margin_pt:
                raise ValueError("Moving following text would cross the page's upper safety margin")
            shifted.append({
                "start_operator_index": obj["start"], "tm_operator_index": obj["tm_index"],
                "x": obj["x"], "before_y": obj["y"],
                "after_y": round(obj["y"] - shift, 4),
                "text": "".join(obj["texts"])[:180],
            })
        if abs(shift) > 0.005 and not shifted:
            return {
                "shift_pt": shift, "old_height_pt": old_height,
                "new_height_pt": new_height_pt, "following": [],
                "note": "No eligible following text in the same column",
            }
        return {
            "shift_pt": shift, "old_height_pt": old_height,
            "new_height_pt": new_height_pt, "following": shifted,
            "note": "Only safe independent text objects will be moved; no image/vector auto-layout",
        }


def reflow_with_following_text(
    source: Path, destination: Path, *, page: int, start_operator_index: int,
    expected_text: str, replacement: str, width_pt: float, height_pt: float,
) -> dict:
    """Rewrite the anchor paragraph, then move subsequent Tm in same column.

    Two outputs are never created. The intermediate PDF exists only inside
    a temporary directory and is removed even if validation fails.
    """
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if dst == src or dst.exists():
        raise ValueError("Destination must be an unused file, not the source")  # 限制：重排结果必须是未使用的新文件，不能覆盖原稿
    with TemporaryDirectory(prefix="qpdf-flow-") as folder:
        intermediary = Path(folder) / "reflowed.pdf"
        first = reflow_text_object(
            src, intermediary, page=page, start_operator_index=start_operator_index,
            expected_text=expected_text, replacement=replacement,
            width_pt=width_pt, height_pt=height_pt,
        )
        planned = plan_text_flow(
            src, page=page, start_operator_index=start_operator_index,
            new_height_pt=first["height_used_pt"],
        )
        if not planned["following"]:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with pikepdf.Pdf.open(intermediary) as pdf:
                try:
                    with dst.open("xb") as output:
                        pdf.save(output)
                except Exception:
                    dst.unlink(missing_ok=True)
                    raise
        else:
            with pikepdf.Pdf.open(intermediary) as pdf:
                sheet = pdf.pages[page - 1]
                operations = list(pikepdf.parse_content_stream(sheet))
                # The reflow changed the number of operators inside the
                # paragraph, so indexes after its ET must be re-resolved by
                # their text and x/y anchors, never reused blindly.
                current = _text_objects(sheet)
                already_used: set[int] = set()
                for entry in planned["following"]:
                    matches = [
                        item for item in current
                        if item["tm_index"] not in already_used
                        and item["x"] == entry["x"]
                        and abs(item["y"] - entry["before_y"]) < 0.01
                        and "".join(item["texts"])[:180] == entry["text"]
                    ]
                    if len(matches) != 1:
                        raise ValueError("Following text position was ambiguous after reflow")
                    item = matches[0]
                    already_used.add(item["tm_index"])
                    tm = operations[item["tm_index"]]
                    operands = list(tm.operands)
                    operands[5] = pikepdf.Real(entry["after_y"])
                    operations[item["tm_index"]] = pikepdf.ContentStreamInstruction(
                        operands, tm.operator,
                    )
                sheet.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(operations))
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with dst.open("xb") as output:
                        pdf.save(output)
                except Exception:
                    dst.unlink(missing_ok=True)
                    raise
        # Verify the relocated objects in the saved PDF, not the intermediary.
        with pikepdf.Pdf.open(dst) as pdf:
            actual = _text_objects(pdf.pages[page - 1])
            for entry in planned["following"]:
                matching = [
                    obj for obj in actual
                    if obj["x"] == entry["x"]
                    and abs(obj["y"] - entry["after_y"]) < 0.01
                    and "".join(obj["texts"])[:180] == entry["text"]
                ]
                if len(matching) != 1:
                    dst.unlink(missing_ok=True)
                    raise ValueError("After-save verification failed for relocated text")
        report = {
            **first,
            "operation": "reflow-paragraph-and-position-following-text",
            "source": src.name, "source_sha256": _hash(src),
            "output": dst.name, "output_sha256": _hash(dst),
            "flow_plan": planned,
            "method": "Rewrite original Tj/T* and following Tm operands; no cover, overlay or new font",
            "scope_warning": "Only plain same-column text without drawings, images or transforms is supported",
        }
        return report
