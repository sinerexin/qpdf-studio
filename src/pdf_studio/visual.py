"""PDFium page renderer and visual comparison, independent of qpdf parsing."""
from __future__ import annotations

import json
from pathlib import Path
from PIL import Image, ImageChops
import pypdfium2 as pdfium


def render_page(document: Path, page_index: int = 0, scale: float = 1.5) -> Image.Image:
    if not 0.5 <= scale <= 4:  # 限制：渲染倍率只允许 0.5 到 4
        raise ValueError("Scale must be between 0.5 and 4")  # 限制：超出倍率范围就拒绝渲染
    pdf = pdfium.PdfDocument(str(document.resolve(strict=True)))
    try:
        if page_index < 0 or page_index >= len(pdf):
            raise IndexError("Page index out of range")
        page = pdf[page_index]
        try:
            return page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        pdf.close()


def render_document(document: Path, destination: Path, scale: float = 1.5, max_pages: int = 100) -> dict:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    destination.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(document.resolve(strict=True)))
    pages = min(len(pdf), max_pages)  # 限制：整份渲染默认最多 100 页
    pdf.close()
    names = []
    for index in range(pages):
        name = f"page_{index + 1:04d}.png"
        render_page(document, index, scale).save(destination / name)
        names.append(name)
    return {"rendered": names, "rendered_page_count": pages, "scale": scale}


def visual_diff(document_a: Path, document_b: Path, destination: Path,
                scale: float = 1.5, threshold: int = 18, max_pages: int = 100) -> dict:
    if not 0 <= threshold <= 255 or max_pages < 1:
        raise ValueError("Invalid threshold or max_pages")
    a = pdfium.PdfDocument(str(document_a.resolve(strict=True)))
    b = pdfium.PdfDocument(str(document_b.resolve(strict=True)))
    count_a, count_b = len(a), len(b)
    a.close()
    b.close()
    destination.mkdir(parents=True, exist_ok=True)
    page_results = []
    for index in range(min(max(count_a, count_b), max_pages)):
        first = render_page(document_a, index, scale) if index < count_a else None
        second = render_page(document_b, index, scale) if index < count_b else None
        width = max(first.width if first else 0, second.width if second else 0)
        height = max(first.height if first else 0, second.height if second else 0)
        if width * height > 40_000_000:
            raise ValueError("Page too large for pixel comparison")
        background_a = Image.new("RGB", (width, height), "white")
        background_b = Image.new("RGB", (width, height), "white")
        if first:
            background_a.paste(first, (0, 0))
        if second:
            background_b.paste(second, (0, 0))
        channels = ImageChops.difference(background_a, background_b).split()
        intensity = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        changed = intensity.point(
            lambda intensity: 255 if intensity > threshold else 0
        )
        area = changed.getbbox()
        # Diagnostic PNG visualization only. Never save it back into either
        # PDF as a cover, page layer, or replacement text.
        marked_image = background_b.convert("RGBA")
        mark = Image.new("RGBA", marked_image.size, (220, 30, 40, 140))
        marked_image.paste(mark, (0, 0), changed)
        filename = f"diff_{index + 1:04d}.png"
        marked_image.convert("RGB").save(destination / filename)
        pixels = changed.histogram()[255]
        page_results.append({
            "page": index + 1, "file": filename, "changed_pixels": pixels,
            "total_pixels": width * height,
            "changed_fraction": round(pixels / (width * height), 6),
            "changed_bbox_px": list(area) if area else None,
            "only_in": ("B" if first is None else "A" if second is None else None),
        })
    result = {
        "page_count_a": count_a, "page_count_b": count_b,
        "threshold": threshold, "scale": scale,
        "pages": page_results,
        "note": "A visual difference does not by itself prove unauthorized modification.",
    }
    (destination / "visual_diff.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
