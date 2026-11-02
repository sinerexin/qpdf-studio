"""Rich read-only PDF semantics that the low-level qpdf API does not provide."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.generic import IndirectObject


def _plain(value: Any) -> Any:
    """Convert pypdf objects to JSON-ready scalar representations without cycles."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, IndirectObject):
        return {"ref": f"{value.idnum} {value.generation} R"}
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value[:100]]  # 限制：语义清单里数组最多展开 100 项
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in list(value.items())[:100]  # 限制：语义清单里字典最多展开 100 项
                if not str(k).startswith("/AP")}  # 限制：外观流 /AP 不展开，避免把外观当成可编辑正文
    return str(value)


def _bookmarks(outline: Any, page_count: int, limit: int = 1000) -> list[dict]:
    result: list[dict] = []

    def walk(items: Any, depth: int) -> None:
        if depth > 30 or len(result) >= limit:  # 限制：书签最多 30 层、1000 条，超出不再展开
            return
        for item in items:
            if len(result) >= limit:
                break
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            row = {"level": depth, "title": str(getattr(item, "title", item))}
            try:
                page = getattr(item, "page", None)
                if hasattr(page, "idnum"):
                    row["target_page_ref"] = f"{page.idnum} {page.generation} R"
            except Exception:
                pass
            result.append(row)

    walk(outline, 0)
    return result


def semantic_inventory(path: Path, *, max_glyphs_per_page: int = 25000,
                       max_pages: int = 300) -> dict:
    """Inspect attachments, annotations, forms, metadata, outlines and glyph boxes.

    Character coordinates use PDF page coordinate space. This is not a complete
    guarantee of perfect visual/semantic reconstruction for every font.
    """
    src = path.resolve(strict=True)
    if not 0 < max_pages <= 10000 or not 0 < max_glyphs_per_page <= 100000:
        raise ValueError("Invalid processing limits")  # 限制：处理页数或对象上限不合法
    reader = PdfReader(str(src), strict=False)
    result: dict[str, Any] = {
        "schema": "qpdf-studio-semantics/0.1",
        "source": src.name,
        "metadata": _plain(reader.metadata or {}),
        "bookmarks": [],
        "form_fields": [],
        "attachments": [],
        "pages": [],
        "warnings": [],
    }
    try:
        result["bookmarks"] = _bookmarks(reader.outline, len(reader.pages))
    except Exception as exc:
        result["warnings"].append(f"Bookmarks unavailable: {type(exc).__name__}: {exc}")
    try:
        fields = reader.get_fields() or {}
        for key, field in list(fields.items())[:5000]:
            result["form_fields"].append({
                "name": str(key), "type": str(field.get("/FT", "")),
                "value": _plain(field.get("/V")),
                "flags": _plain(field.get("/Ff")),
                "tooltip": _plain(field.get("/TU")),
            })
    except Exception as exc:
        result["warnings"].append(f"Forms unavailable: {type(exc).__name__}: {exc}")
    try:
        for name, attachments in list(reader.attachments.items())[:500]:
            values = attachments if isinstance(attachments, list) else [attachments]
            for binary in values:
                if not isinstance(binary, bytes):
                    result["attachments"].append({"name": name, "warning": "unreadable bytes"})
                else:
                    result["attachments"].append({
                        "name": name, "bytes": len(binary),
                        "sha256": hashlib.sha256(binary).hexdigest(),
                    })
    except Exception as exc:
        result["warnings"].append(f"Attachments unavailable: {type(exc).__name__}: {exc}")

    render_doc = pdfium.PdfDocument(str(src))
    try:
        total = min(len(render_doc), len(reader.pages), max_pages)
        if total < len(reader.pages):
            result["warnings"].append(f"Page scan limited to {max_pages} pages")
        for index in range(total):
            item: dict[str, Any] = {
                "page": index + 1, "glyphs": [], "annotations": [], "links": [],
            }
            pdf_page = reader.pages[index]
            for ann_ref in list(pdf_page.get("/Annots", []))[:2000]:
                try:
                    ann = ann_ref.get_object()
                    row = {
                        "reference": _plain(ann_ref),
                        "subtype": str(ann.get("/Subtype", "")),
                        "rectangle": _plain(ann.get("/Rect")),
                        "contents": _plain(ann.get("/Contents")),
                        "author": _plain(ann.get("/T")),
                        "flags": _plain(ann.get("/F")),
                    }
                    item["annotations"].append(row)
                    if row["subtype"] == "/Link":
                        action = ann.get("/A", {})
                        item["links"].append({
                            "rectangle": row["rectangle"],
                            "uri": _plain(action.get("/URI")) if action else None,
                            "destination": _plain(ann.get("/Dest")),
                        })
                except Exception as exc:
                    item["annotations"].append({"error": f"{type(exc).__name__}: {exc}"})
            page = render_doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    count = textpage.count_chars()
                    item["glyph_count_total"] = count
                    item["glyph_count_exported"] = min(count, max_glyphs_per_page)
                    if count > max_glyphs_per_page:
                        item["glyphs_truncated"] = True
                    for char_index in range(min(count, max_glyphs_per_page)):
                        try:
                            char = textpage.get_text_range(char_index, 1)
                            bbox = textpage.get_charbox(char_index)
                            item["glyphs"].append({
                                "index": char_index,
                                "char": char,
                                "box": [round(float(pos), 3) for pos in bbox],
                            })
                        except Exception:
                            # Broken font mappings can have missing individual glyphs.
                            item["glyphs"].append({"index": char_index, "unavailable": True})
                finally:
                    textpage.close()
            finally:
                page.close()
            result["pages"].append(item)
    finally:
        render_doc.close()
    return result
