"""Qt click -> native XObject or vector operand edit -> real saved PDF."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton
from pypdf import PdfReader

from pdf_studio.qt_desktop import PdfStudioQt
from pdf_studio.object_editor import list_editable_objects
from test_object_editor import _image_document, _vector_document


@pytest.mark.parametrize("kind", ["image", "vector-fill"])
def test_qt_click_real_object_and_save_direct_stream(tmp_path: Path,kind: str):
    source=tmp_path/"source.pdf"
    target=tmp_path/"edited.pdf"
    if kind=="image":
        _image_document(source)
        asset=tmp_path/"new.png"
        Image.new("RGB",(30,26),(4,171,232)).save(asset)
        x,y=125,565
    else:
        _vector_document(source)
        asset=None
        x,y=125,530
    app=QApplication.instance() or QApplication([])
    ui=PdfStudioQt()
    ui.load_path("A",source)
    ui.show()
    ui.render_previews()
    inventory=list_editable_objects(source,1)
    assert any(entry["kind"]==kind and entry["editable"] for entry in inventory["objects"])
    assert any(a.text()=="直接编辑图片/图形"
               for a in ui.content_edit_menu.menu().actions())
    pixmap=ui.previews["A"].image.pixmap()
    px=x*pixmap.width()/612
    py=(792-y)*pixmap.height()/792

    def mock_dialog(dialog):
        if dialog.windowTitle().startswith("直接编辑 PDF 原有图片"):
            fields=dialog.findChildren(QLineEdit)
            if kind=="image":
                file_field=next(field for field in fields if field.isReadOnly())
                file_field.setText(str(asset))
            else:
                color_field=next(field for field in fields if field.text().startswith("#"))
                color_field.setText("#0088FF")
            return QDialog.Accepted
        return QDialog.Rejected

    try:
        with patch.object(QDialog,"exec",new=mock_dialog),patch.object(
            QFileDialog,"getSaveFileName",return_value=(str(target),"PDF")
        ),patch.object(QMessageBox,"warning",side_effect=lambda *args:pytest.fail(str(args))):
            next(a for a in ui.content_edit_menu.menu().actions()
                 if a.text()=="直接编辑图片/图形").trigger()
            assert ui._editing_original_object
            ui._clicked_page("A",px,py)
            end=time.monotonic()+12
            while ui._busy and time.monotonic()<end:
                app.processEvents()
                time.sleep(0.025)
            app.processEvents()
            assert not ui._busy,ui.status.text()
        assert target.exists(),ui.status.text()
        assert ui.docs["B"]==target.resolve()
        assert "Unchanged" in PdfReader(str(target)).pages[0].extract_text() if kind=="image" else True
        assert not (tmp_path/"edited.pdf.changes.json").exists()
    finally:
        ui.close()
