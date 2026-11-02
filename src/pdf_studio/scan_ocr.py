"""Read scanned-page pixels at a click and write real PDF text operators.

OCR is optional: Tesseract if present. No covering rectangle is painted.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .body_text import add_body_text
from .font_replacement import default_chinese_font


def _tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def recognize_at(source: Path, page: int, x: float, y: float) -> dict:
    """OCR a small window around a PDF user-space click."""
    engine = _tesseract()
    if engine is None:
        raise ValueError("扫描图需要 OCR。本机未找到 Tesseract，无法识别像素文字。")
    src = Path(source).resolve(strict=True)
    document = pdfium.PdfDocument(str(src))
    try:
        if not 1 <= page <= len(document):
            raise ValueError("页码超出文档范围。")
        pdf_page = document[page - 1]
        scale = 2.0
        height = pdf_page.get_height()
        bitmap = pdf_page.render(scale=scale)
        try:
            picture = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
        px = int(round(x * scale))
        py = int(round((height - y) * scale))
        pad_x, pad_y = int(90 * scale), int(28 * scale)
        box = (
            max(0, px - pad_x),
            max(0, py - pad_y),
            min(picture.width, px + pad_x),
            min(picture.height, py + pad_y),
        )
        crop = picture.crop(box)
        folder = Path(tempfile.mkdtemp(prefix="qpdf-ocr-"))
        image = folder / "crop.png"
        crop.save(image)
        languages = "chi_sim+eng"
        try:
            raw = subprocess.check_output(
                [engine, str(image), "stdout", "--psm", "6", "-l", languages],
                stderr=subprocess.DEVNULL, timeout=20,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raw = subprocess.check_output(
                [engine, str(image), "stdout", "--psm", "6", "-l", "eng"],
                stderr=subprocess.DEVNULL, timeout=20,
            )
        text = raw.decode("utf-8", errors="ignore").strip().replace("\n", " ")
        if not text:
            raise ValueError("OCR 没有在点击位置识别到文字。")
        size = max(8.0, min(48.0, (box[3] - box[1]) / scale * 0.7))
        samples = []
        for sx in range(0, crop.width, max(1, crop.width // 8)):
            for sy in range(0, crop.height, max(1, crop.height // 8)):
                samples.append(crop.getpixel((sx, sy)))
        dark = min(samples, key=lambda rgb: rgb[0] + rgb[1] + rgb[2]) if samples else (20, 20, 20)
        color = "#{:02X}{:02X}{:02X}".format(*dark)
        left = box[0] / scale
        bottom = height - box[3] / scale
        return {
            "text": text,
            "page": page,
            "x": left,
            "y": bottom + size * 0.2,
            "font_size": size,
            "color": color,
            "kind": "ocr",
            "label": "扫描识别",
            "editable": True,
        }
    finally:
        document.close()


def place_ocr_text(source: Path, destination: Path, *, page: int,
                   x: float, y: float, text: str, size: float, color: str) -> dict:
    from os import environ
    fonts = Path(environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font = fonts / "simhei.ttf"
    if not font.is_file():
        font = default_chinese_font("regular", "hei")
    return add_body_text(
        source, destination, page=page, x=x, y=y, text=text, size=size,
        color=color, font_path=font if font.suffix.lower() in {".ttf", ".otf"} else None,
    )
