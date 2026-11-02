from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.scan_ocr import place_ocr_text, recognize_at


def test_recognize_without_tesseract_explains(monkeypatch, tmp_path):
    source = tmp_path / 'blank.pdf'
    Canvas(str(source)).save()
    monkeypatch.setattr('pdf_studio.scan_ocr._tesseract', lambda: None)
    with pytest.raises(ValueError, match='Tesseract'):
        recognize_at(source, 1, 72, 72)


def test_place_ocr_writes_real_text_operators(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'out.pdf'
    canvas = Canvas(str(source))
    canvas.drawString(72, 720, 'keep')
    canvas.save()
    place_ocr_text(source, target, page=1, x=80, y=500, text='扫描识别', size=14, color='#112233')
    assert '扫描识别' in (PdfReader(str(target)).pages[0].extract_text() or '')
