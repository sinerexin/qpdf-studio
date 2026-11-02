"""Real Qt click on original PDF -> contextual editor -> actual PDF B output."""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pikepdf
import pytest
import pypdfium2 as pdfium
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader

from pdf_studio.qt_desktop import PdfStudioQt, STYLESHEET
from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document
from pdf_studio.object_editor import list_editable_objects


def _page_click(window: PdfStudioQt, x: float, y: float) -> None:
    window.render_previews()
    document = pdfium.PdfDocument(str(window.docs["A"]))
    try:
        page = document[window._current_page]
        try:
            width, height = page.get_size()
        finally:
            page.close()
    finally:
        document.close()
    image = window.previews["A"].image
    pixels = image.pixmap()
    QTest.mouseClick(
        image, Qt.LeftButton,
        pos=QPoint(round(x * pixels.width() / width),
                   round((height - y) * pixels.height() / height)),
    )


def _wait(window: PdfStudioQt, app: QApplication) -> None:
    deadline = time.monotonic() + 12
    while window._busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.025)
    app.processEvents()
    assert not window._busy, window.status.text()


@pytest.fixture
def ui():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    window = PdfStudioQt()
    window.resize(1510, 910)
    window.show()
    app.processEvents()
    yield window, app
    window.close()


def test_click_original_text_edit_middle_preview_right_and_save(ui, tmp_path: Path, monkeypatch):
    window, app = ui
    source = tmp_path / "original.pdf"
    output = tmp_path / "changed.pdf"
    c = Canvas(str(source), pagesize=(612, 792))
    c.setFont("Helvetica", 17)
    c.drawString(80, 690, "Original Text Label")
    c.save()
    original_bytes = source.read_bytes()
    window.load_path("A", source)
    window.active_slot.setCurrentText("A")
    _page_click(window, 98, 693)
    editor = window.selection_editor
    assert editor._selection, editor.message.text()
    assert editor._selection[0]["kind"] == "run"
    assert editor.fields.currentIndex() == 1
    assert editor.text_value.toPlainText() == "Original Text Label"
    editor.text_value.setPlainText("Modified Text Label")
    editor.preview_button.click()
    _wait(window, app)
    assert window.docs["B"] and window.docs["B"].is_file()
    assert "Modified Text Label" in PdfReader(str(window.docs["B"])).pages[0].extract_text()
    assert window.previews["B"].image.pixmap() is not None
    assert source.read_bytes() == original_bytes
    with monkeypatch.context() as m:
        m.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: (str(output), "PDF"))
        editor.save_button.click()
        _wait(window, app)
    assert output.is_file()
    assert window.docs["B"] == output.resolve()
    assert "Original Text Label" not in PdfReader(str(output)).pages[0].extract_text()
    assert "Modified Text Label" in PdfReader(str(output)).pages[0].extract_text()
    assert not (tmp_path / "changed.pdf.changes.json").is_file()
    assert source.read_bytes() == original_bytes


def test_click_original_image_edit_middle_and_save(ui, tmp_path: Path, monkeypatch):
    window, app = ui
    source = tmp_path / "original.pdf"
    output = tmp_path / "new-image.pdf"
    image_path = tmp_path / "new.png"
    c = Canvas(str(source), pagesize=(612, 792))
    c.drawImage(ImageReader(Image.new("RGB", (24, 18), (180, 40, 40))),
                100, 460, width=140, height=105)
    c.save()
    Image.new("RGB", (24, 18), (16, 155, 205)).save(image_path)
    original_bytes = source.read_bytes()
    window.load_path("A", source)
    _page_click(window, 125, 510)
    editor = window.selection_editor
    assert editor._selection and editor._selection[0]["kind"] == "image"
    assert editor.fields.currentIndex() == 2
    with monkeypatch.context() as m:
        m.setattr(QFileDialog, "getOpenFileName", lambda *args, **kw: (str(image_path), "PNG"))
        image_picker = next(btn for btn in editor.findChildren(QPushButton)
                            if btn.text() == "选择图片")
        image_picker.click()
        m.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: (str(output), "PDF"))
        editor.save_button.click()
        _wait(window, app)
    assert output.is_file()
    inventory = list_editable_objects(source, 1)
    name = next(row["resource"] for row in inventory["objects"]
                if row["kind"] == "image")
    with pikepdf.Pdf.open(output) as pdf:
        resource = pdf.pages[0].resources["/XObject"][name]
        assert resource.read_bytes() == Image.open(image_path).tobytes()
    assert source.read_bytes() == original_bytes
    assert window.previews["B"].image.pixmap() is not None


def test_signed_original_can_be_inspected_without_blocking_modal(ui, tmp_path: Path, monkeypatch):
    window, app = ui
    unsigned = tmp_path / "unsigned.pdf"
    c = Canvas(str(unsigned))
    c.drawString(80, 690, "Signed Source Text")
    c.save()
    folder = tmp_path / "keys"
    password = "Synthetic user-owned signing password"
    generate_personal_credentials(folder, label="Synthetic User",
                                  password=password, algorithm="ECDSA-P256")
    signed = sign_document(unsigned, tmp_path / "signed.pdf",
                           pfx=folder / "personal-signing.p12",
                           password=password.encode())
    window.load_path("A", signed)
    with monkeypatch.context() as m:
        m.setattr(QMessageBox, "warning",
                  lambda *args, **kw: pytest.fail("Signed source must not open a blocking warning"))
        _page_click(window, 88, 692)
    editor = window.selection_editor
    assert editor._selection
    assert editor.save_button.isEnabled()
    assert editor.preview_button.isEnabled()
    assert not editor.text_value.isReadOnly()


def test_unsupported_replacement_uses_inline_failure_and_preserves_original(
    ui, tmp_path: Path, monkeypatch,
):
    window, app = ui
    source = tmp_path / "source.pdf"
    c = Canvas(str(source), pagesize=(612, 792))
    c.drawString(80, 690, "Original Text Label")
    c.save()
    original = source.read_bytes()
    window.load_path("A", source)
    _page_click(window, 93, 692)
    editor = window.selection_editor
    assert editor._selection
    editor.text_value.setPlainText("未嵌入的中文")
    with monkeypatch.context() as m:
        m.setattr(QMessageBox, "warning",
                  lambda *a, **kw: pytest.fail("Unsupported glyph should be an inline status"))
        editor.preview_button.click()
        _wait(window, app)
    assert "未修改原稿" in editor.message.text()
    assert editor.save_button.isEnabled()
    assert editor.preview_button.isEnabled()
    assert window.docs["B"] is None
    assert source.read_bytes() == original
