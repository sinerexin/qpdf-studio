"""Append vector text operators directly to the PDF page content stream.

This operation never draws a cover over existing text and never invokes the
page-overlay editor API. It appends new text operators to the page /Contents,
using safely renamed font resources imported from a temporary font-generator
PDF. Actual replacement of existing glyphs is handled by replace_text.py.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont



def add_body_text(source: Path, target: Path, *, page: int,
                  x: float, y: float, text: str, size: float = 14,
                  color: str = "#18385C", font_path: Path | None = None) -> dict:
    """Place new visible text at PDF user-space (x,y) on page (1-indexed).

    This is a deliberate new content addition. It is not a replacement of text
    already painted by existing content streams. For non-Latin text the caller
    may supply an owned TrueType font path and its resource is copied into
    the page font dictionary without installing a page-sized overlay.
    """
    src = Path(source).resolve(strict=True)
    dst = Path(target).resolve()
    if src == dst or dst.exists():
        raise ValueError("Output must be a new unused filename, not the source")  # 限制：正文必须另存为新文件，不能覆盖原稿
    if not text.strip() or len(text) > 5000 or len(text.splitlines()) > 50:
        raise ValueError("Text must contain 1–5000 characters and at most 50 lines")
    if not 4 <= size <= 96:
        raise ValueError("Font size must be 4–96 PDF points")
    if not (0 <= x <= 100000 and 0 <= y <= 100000):
        raise ValueError("Position must use non-negative PDF coordinates")
    if len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
        raise ValueError("Color requires six-digit #RRGGBB notation")
    font_name = "Helvetica"
    if font_path:
        actual_font = Path(font_path).resolve(strict=True)
        if actual_font.suffix.lower() not in {".ttf", ".otf"}:
            raise ValueError("Only supported local TrueType/OpenType fonts can be embedded")  # 限制：只能嵌入本机受支持的 TrueType 或 OpenType 字体
        font_name = "PdfStudioEmbeddedFont"
        pdfmetrics.registerFont(TTFont(font_name, str(actual_font)))
    elif any(ord(char) > 255 for char in text):
        raise ValueError("For Unicode beyond Latin-1 select a TTF/OTF font to embed")

    rgb = tuple(int(color[pos:pos + 2], 16) / 255 for pos in (1, 3, 5))
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted document cannot be edited")  # 限制：加密文档不能编辑
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page index outside document")
        current_page = pdf.pages[page - 1]
        box = tuple(float(v) for v in current_page.MediaBox)
        if not box[0] <= x <= box[2] or not box[1] <= y <= box[3]:
            raise ValueError("Text insertion position is outside PDF MediaBox")
        if y - (len(text.splitlines()) - 1) * size * 1.28 < box[1]:
            raise ValueError("Multi-line text runs outside the page bottom")
        # ReportLab supplies bona fide font resources and PDF text operators.
        # The qpdf layer imports ONLY the needed font resources and appends
        # the drawing instructions directly to the target page /Contents.
        layer_bytes = io.BytesIO()
        layer = Canvas(layer_bytes, pagesize=(box[2] - box[0], box[3] - box[1]))
        layer.setFillColorRGB(*rgb)
        layer.setFont(font_name, size)
        for number, line in enumerate(text.splitlines()):
            layer.drawString(x - box[0], y - box[1] - number * size * 1.28, line)
        layer.save()
        layer_bytes.seek(0)
        with pikepdf.Pdf.open(layer_bytes) as generated:
            generated_page = generated.pages[0]
            source_fonts = generated_page.resources.get("/Font")
            if not source_fonts:
                raise ValueError("Temporary text generator did not provide a font resource")
            # A page can inherit its /Resources from an ancestor. Clone the
            # dictionaries so we do not mutate another page's shared fonts.
            page_resources = pikepdf.Dictionary({
                str(key): value for key, value in current_page.resources.items()
            })
            existing_fonts = page_resources.get("/Font")
            fonts = pikepdf.Dictionary({
                str(key): value for key, value in existing_fonts.items()
            }) if existing_fonts is not None else pikepdf.Dictionary()
            mapping: dict[str, str] = {}
            for old_name, font_obj in source_fonts.items():
                counter = 1
                while True:
                    candidate = f"/PdfStudioTextFont{counter}"
                    if candidate not in fonts:
                        break
                    counter += 1
                fonts[candidate] = pdf.copy_foreign(font_obj)
                mapping[str(old_name)] = candidate
            page_resources["/Font"] = fonts
            # Keep the generated drawing stream's original ordering; only
            # replace its Tf resource names with collision-free imported ones.
            commands = []
            for instruction in pikepdf.parse_content_stream(generated_page):
                if str(instruction.operator) == "Tf":
                    arguments = list(instruction.operands)
                    old_font = str(arguments[0])
                    if old_font not in mapping:
                        raise ValueError(f"Unmapped temporary font resource {old_font}")
                    arguments[0] = pikepdf.Name(mapping[old_font])
                    instruction = pikepdf.ContentStreamInstruction(
                        arguments, instruction.operator
                    )
                commands.append(instruction)
            # q ... Q isolates graphics/text state from surrounding content.
            stream_bytes = b"q\n" + pikepdf.unparse_content_stream(commands) + b"\nQ\n"
            current_page.obj["/Resources"] = page_resources
            current_page.contents_add(stream_bytes)
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                pdf.save(dst)
            except Exception:
                dst.unlink(missing_ok=True)
                raise
    def digest(path: Path) -> str:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    report = {
        "operation": "add-visible-body-text",
        "source": src.name,
        "source_sha256": digest(src),
        "output": dst.name,
        "output_sha256": digest(dst),
        "page": page,
        "insertion_point": [x, y],
        "font_size_pt": size,
        "color": color,
        "font": font_name,
        "text_added": text,
        "editor_note": "Appended PDF text operators directly to /Contents; no cover or page overlay; original text remains unchanged",
        "editing_method": "direct-page-content-stream-append",
        "signature_note": "Existing signature presence does not block editing",
    }
    return report
