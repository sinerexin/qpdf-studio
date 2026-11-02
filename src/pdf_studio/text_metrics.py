"""Read-only PDFium text bounds before and after low-level stream editing.

This does not reflow, reposition, hide, cover or rewrite any page content.
Bounds are intentionally marked unavailable for ambiguous multiple matches
or PDFium mapping limitations.
"""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium


def unique_text_bounds(pdf: Path, page_number: int, phrase: str) -> list[float] | None:
    if not phrase or len(phrase) > 3000:
        return None
    document = pdfium.PdfDocument(str(pdf))
    try:
        if not 1 <= page_number <= len(document):
            return None
        page = document[page_number - 1]
        try:
            text = page.get_textpage()
            try:
                searcher = text.search(phrase, match_case=True)
                try:
                    first = searcher.get_next()
                    if first is None or searcher.get_next() is not None:
                        return None
                finally:
                    searcher.close()
                index, length = first
                if length <= 0 or length > 3000:
                    return None
                glyph_boxes = []
                for ch in range(index, index + length):
                    try:
                        box = text.get_charbox(ch)
                    except (IndexError, ValueError, RuntimeError):
                        return None
                    if box and box[2] >= box[0] and box[3] >= box[1]:
                        glyph_boxes.append(box)
                if not glyph_boxes:
                    return None
                return [
                    round(min(rect[0] for rect in glyph_boxes), 3),
                    round(min(rect[1] for rect in glyph_boxes), 3),
                    round(max(rect[2] for rect in glyph_boxes), 3),
                    round(max(rect[3] for rect in glyph_boxes), 3),
                ]
            finally:
                text.close()
        finally:
            page.close()
    finally:
        document.close()


def describe_layout_change(source: Path, output: Path, *, page: int,
                           original: str, replacement: str) -> dict:
    """Report horizontal extent change where exactly one occurrence maps."""
    try:
        before = unique_text_bounds(source, page, original)
        after = unique_text_bounds(output, page, replacement)
    except (OSError, ValueError, RuntimeError, pdfium.PdfiumError) as error:
        # The content-stream rewrite is already committed to a new file.
        # A diagnostic rendering limitation must not erase the audit trail.
        return {
            "status": "unavailable",
            "reason": f"{type(error).__name__}: {error}",
            "note": "PDFium could not measure the glyph bounds; manually verify saved PDF layout",
        }
    result = {
        "before_pdf_bounds": before,
        "after_pdf_bounds": after,
        "status": "measured" if before and after else "unavailable_or_ambiguous",
        "note": "PDFium glyph bounds are evidence, not an automatic paragraph reflow.",
    }
    if before and after:
        old_width = before[2] - before[0]
        new_width = after[2] - after[0]
        difference = round(new_width - old_width, 3)
        result.update({
            "before_width_pt": round(old_width, 3),
            "after_width_pt": round(new_width, 3),
            "width_change_pt": difference,
            "layout_review_recommended": abs(difference) > 1.0,
        })
    return result
