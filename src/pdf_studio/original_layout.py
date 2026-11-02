"""Derive a conservative layout profile from an ORIGINAL PDF, not a new template.

PDF content streams preserve final font/coordinates/line placements but usually
do not contain the author's Word/InDesign paragraph width, style sheet, or
flow rules. Stored instructions are reported as observed facts; inferred
column width, text margins and text flow are explicitly marked estimates.
"""
from __future__ import annotations

import math
from pathlib import Path

import pikepdf

from .editing import page_contents_ready
from .paragraph_reflow import (
    _font_encoder, _measure_font, _wrapped_lines, list_reflow_blocks,
)
from .replace_text import _collect_runs, _hash
from .flow_layout import plan_text_flow, reflow_with_following_text


def _finite(value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite source PDF geometry")
    return result


def read_original_layout(source: Path, *, page: int,
                         start_operator_index: int) -> dict:
    """Extract exact text attributes and a labeled inferred paragraph envelope.

    Auto layout is intentionally supported only for simple same-column,
    text-only pages where direct Tm movement is verifiable.
    """
    src = Path(source).resolve(strict=True)
    with pikepdf.Pdf.open(src) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        if pdf.is_encrypted:
            raise ValueError("Encrypted source PDF cannot be edited")  # 限制：加密的原稿不能按原排版编辑
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        blocks = list_reflow_blocks(src, page)
        target = next(
            (item for item in blocks if item["start_operator_index"] == start_operator_index),
            None,
        )
        if target is None:
            raise ValueError("Original PDF does not contain a supported isolated paragraph here")

        media = [_finite(value) for value in sheet.MediaBox]
        x = _finite(target["start_x_pt"])
        y = _finite(target["start_y_pt"])
        font = target["font_resource"]
        size = _finite(target["font_size_pt"])
        leading = _finite(target["leading_pt"])
        encoder = _font_encoder(sheet, font, _collect_runs(sheet))

        original_lines = target["text"].split("\n")
        widths = [
            round(_measure_font(sheet, font, line, size, encoder(line)), 3)
            if line else 0.0
            for line in original_lines
        ]
        if not widths or not any(width > 0 for width in widths):
            raise ValueError("Original paragraph has no measurable glyph advances")
        original_width = max(widths)
        original_height = size + (len(original_lines) - 1) * leading
        if not (media[0] <= x <= media[2] and media[1] <= y <= media[3]):
            raise ValueError("Original paragraph origin is outside the page")

        # This is an estimated column boundary: the output PDF normally stores
        # no paragraph rectangle. Use the longest observed line *plus up to one
        # character spacing*, and other paragraphs in the same aligned column.
        # Never describe this as an author-provided field width.
        neighbors = [
            block for block in blocks
            if block["start_operator_index"] != start_operator_index
            and abs(block["start_x_pt"] - x) <= 1.5
        ]
        neighbor_widths = []
        for neighbor in neighbors:
            # Only same-font and same-size blocks can establish this column's
            # textual envelope; footers with unrelated styles are not templates.
            if (neighbor["font_resource"] != font
                    or abs(neighbor["font_size_pt"] - size) > 0.01):
                continue
            for line in neighbor["text"].split("\n"):
                if line:
                    neighbor_widths.append(_measure_font(
                        sheet, font, line, size, encoder(line),
                    ))
        envelope = max([original_width] + neighbor_widths)
        left_margin = x - media[0]
        usable_right = media[2] - max(left_margin, size)
        physical_max = usable_right - x
        if physical_max < original_width - 0.001:
            raise ValueError("Original paragraph geometry extends beyond the inferred page margins")
        inferred_width = min(
            physical_max,
            max(original_width, envelope) + max(0.65 * size, 7.0),
        )
        inferred_width = round(inferred_width, 3)
        if inferred_width < 20:
            raise ValueError("Unable to infer a usable paragraph width")

        # Do not assume this is the author's original line width if text is
        # short: a PDF may contain only final line starts and glyph positions.
        confidence = "medium" if len(original_lines) >= 2 else "low"
        if len(neighbor_widths) >= 2:
            confidence = "medium"
        warnings = [
            "PDF stores final drawing instructions, not necessarily an original layout/style template",
            "Paragraph width/right margin are inferred from observed line widths and aligned text, not source fields",
            "Only text objects with strictly supported font metrics are eligible",
        ]

        # An inferred height is bounded by page geometry and downstream Tm
        # moves. The flow planner will perform the exact safety preflight.
        safety_margin = max(18.0, size)
        available_height = y - media[1] - safety_margin
        if available_height < original_height:
            raise ValueError("Original page has insufficient safe vertical space")

        return {
            "schema": "qpdf-studio/original-layout-profile-v1",
            "source": src.name,
            "page": page,
            "start_operator_index": start_operator_index,
            "observed": {
                "font_resource": font,
                "font_size_pt": size,
                "line_leading_pt": leading,
                "origin_pdf": [x, y],
                "page_media_box": media,
                "original_text": target["text"],
                "original_line_count": len(original_lines),
                "observed_line_widths_pt": widths,
                "original_height_pt": round(original_height, 3),
                "aligned_same_font_line_widths_pt": [
                    round(n, 3) for n in neighbor_widths
                ],
            },
            "inferred": {
                "max_line_width_pt": inferred_width,
                "available_vertical_height_pt": round(available_height, 3),
                "right_margin_policy": "mirror left page margin with font-size floor",
                "line_width_policy": "widest observed same-column text + <= one-character allowance",
                "layout_confidence": confidence,
                "is_exact_author_layout_template": False,
            },
            "warnings": warnings,
        }


def plan_original_reflow(source: Path, *, page: int, start_operator_index: int,
                         replacement: str) -> dict:
    """Plan line wrapping and following object shifts using original layout."""
    profile = read_original_layout(
        source, page=page, start_operator_index=start_operator_index,
    )
    width = profile["inferred"]["max_line_width_pt"]
    original = profile["observed"]
    with pikepdf.Pdf.open(Path(source).resolve(strict=True)) as pdf:
        sheet = pdf.pages[page - 1]
        encoder = _font_encoder(
            sheet, original["font_resource"], _collect_runs(sheet),
        )
        def measure(text: str) -> float:
            return _measure_font(
                sheet, original["font_resource"], text,
                original["font_size_pt"], encoder(text),
            ) if text else 0.0
        wrapped = _wrapped_lines(replacement, measure, width)
    new_height = (
        original["font_size_pt"]
        + (len(wrapped) - 1) * original["line_leading_pt"]
    )
    if new_height > profile["inferred"]["available_vertical_height_pt"] + 0.001:
        raise ValueError("New text cannot fit within the source page's vertical limits")  # 限制：新文字超出原稿页面的垂直空间
    movement = plan_text_flow(
        source, page=page, start_operator_index=start_operator_index,
        new_height_pt=new_height,
    )
    return {
        "source_layout": profile,
        "wrapped_lines": wrapped,
        "new_height_pt": round(new_height, 3),
        "flow_plan": movement,
        "warnings": profile["warnings"] + [
            "Flow moves only safely matched same-column text objects; images and graphics are not automatically repositioned",
        ],
    }


def reflow_like_original(source: Path, destination: Path, *,
                         page: int, start_operator_index: int,
                         expected_text: str, replacement: str) -> dict:
    """Use extracted original formatting. Caller supplies text, not dimensions."""
    src = Path(source).resolve(strict=True)
    dest = Path(destination).resolve()
    if dest.exists() or src == dest:
        raise ValueError("Output must be a new unused PDF")  # 限制：原稿排版结果必须是新的未使用 PDF
    plan = plan_original_reflow(
        src, page=page, start_operator_index=start_operator_index,
        replacement=replacement,
    )
    original = plan["source_layout"]["observed"]["original_text"]
    if original != expected_text:
        raise ValueError("The original paragraph differs from the expected text")
    profile = plan["source_layout"]["inferred"]
    result = reflow_with_following_text(
        src, dest, page=page,
        start_operator_index=start_operator_index,
        expected_text=expected_text,
        replacement=replacement,
        width_pt=profile["max_line_width_pt"],
        height_pt=profile["available_vertical_height_pt"],
    )
    result["operation"] = "reflow-according-to-original-pdf-layout-evidence"
    result["original_layout"] = plan
    result["output_sha256"] = _hash(dest)
    return result
