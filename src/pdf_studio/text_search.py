"""Read-only text search with PDFium glyph coordinates for Qt page highlighting."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


def find_text(source: Path, query: str, *, max_pages: int = 300,
              max_hits: int = 500, match_case: bool = False) -> dict[str, Any]:
    """Return text occurrences with per-character bounding boxes in PDF user space.

    Glyph coordinates come from PDFium, not OCR. Rotated pages, unusual fonts,
    and ligatures can require viewer-specific transforms for perfect overlays.
    """
    src = Path(source).resolve(strict=True)
    if not query.strip() or len(query) > 300 or not 1 <= max_pages <= 1000 or not 1 <= max_hits <= 5000:
        raise ValueError("Search term or search limits are invalid")  # 限制：搜索词或搜索上限不合法
    if src.stat().st_size > 150_000_000:
        raise ValueError("Document exceeds text-search file size limit")  # 限制：文档超过文字搜索的文件大小上限
    doc = pdfium.PdfDocument(str(src))
    found: list[dict] = []
    try:
        for index in range(min(len(doc), max_pages)):
            if len(found) >= max_hits:
                break
            page = doc[index]
            try:
                width, height = page.get_size()
                text = page.get_textpage()
                try:
                    searcher = text.search(query, match_case=match_case)
                    try:
                        while len(found) < max_hits:
                            hit = searcher.get_next()
                            if hit is None:
                                break
                            char_start, char_count = hit
                            boxes = []
                            for number in range(char_start, char_start + char_count):
                                try:
                                    rect = text.get_charbox(number)
                                    if rect:
                                        boxes.append([round(float(v), 3) for v in rect])
                                except (IndexError, ValueError, RuntimeError):
                                    pass
                            found.append({
                                "page": index + 1,
                                "char_start": char_start,
                                "char_count": char_count,
                                "text": text.get_text_range(char_start, char_count),
                                "boxes_pdf": boxes,
                                "page_size_pdf": [float(width), float(height)],
                            })
                    finally:
                        searcher.close()
                finally:
                    text.close()
            finally:
                page.close()
    finally:
        total_pages = len(doc)
        doc.close()
    return {
        "query": query, "match_case": match_case,
        "count": len(found), "matches": found,
        "total_pages": total_pages,
        "pages_scanned": min(total_pages, max_pages),
        "truncated": len(found) >= max_hits or total_pages > max_pages,
        "note": "Text search is based on PDFium glyph mapping, not OCR or document authenticity.",
    }
