"""Actual minimalist Qt workflow: few first-screen controls, all tools retained."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.qt_desktop import PdfStudioQt, STYLESHEET
from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document


@pytest.fixture
def ui():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    window = PdfStudioQt()
    window.show()
    app.processEvents()
    yield window, app
    window.close()


@pytest.mark.parametrize("width,height", [(1180, 720), (1510, 910)])
def test_first_screen_shows_only_daily_actions(ui, width, height):
    window, app = ui
    window.resize(width, height)
    app.processEvents()
    assert [window.workspace_tabs.tabText(i) for i in range(2)] == [
        "内容编辑", "签名与证书"
    ]
    visible = [b.text() for b in window.findChildren(QPushButton) if b.isVisible()]
    assert "打开 PDF" in visible and "文件对比" in visible
    assert "搜索定位" not in visible
    assert "高级工具" not in visible
    assert "替换已有文字" not in visible
    assert "段落重排" not in visible
    assert "完整解析导出" not in visible
    assert "导出专业验签报告 JSON" not in visible
    daily = {
        "打开 PDF", "文件对比", "详情", "完成编辑 → 签名与证书",
        "选择 PDF 文件…", "导入对比文件",
    }
    extra = [text for text in visible if text not in daily]
    if width < 1350:
        assert extra == []
    else:
        assert set(extra) == {"01  导入原稿", "02  内容编辑", "03  签名与证书", "04  导出与对比"}
    assert window.studio_shell.stage.currentIndex() == 0
    assert window.content_edit_menu.isVisible()
    assert not hasattr(window, "advanced_menu_button")
    assert not window.inspector.isVisible()
    assert not window.previews["A"].isVisible()
    assert not window.results.isVisible()
    assert [a.text() for a in window.content_edit_menu.menu().actions()] == [
        "替换已有文字", "段落重排", "点击添加正文", "直接编辑图片/图形"
    ]
    window.workspace_tabs.setCurrentIndex(1)
    app.processEvents()
    assert not window.signer_output_button.isVisible()
    assert not window.signing_tools.isVisible()
    assert window.studio_shell.certificate_tools.isVisible()
    step_buttons = {b.text() for b in window.findChildren(QPushButton) if b.isVisible()}
    assert "① 读取 A 参数" in step_buttons and "⑤ 检查与对照" in step_buttons
    for tool in [window.content_edit_menu, window.signing_tools]:
        assert tool.minimumHeight() >= 30


def test_editor_modes_and_single_page_to_dual_document(ui, tmp_path: Path):
    window, app = ui
    original = tmp_path / "source.pdf"
    changed = tmp_path / "edited.pdf"
    for file, text in ((original, "Original text"), (changed, "Edited text")):
        canvas = Canvas(str(file), pagesize=(612, 792))
        canvas.drawString(72, 700, text)
        canvas.save()
    window.load_path("A", original)
    app.processEvents()
    assert "source.pdf" in window.workflow_context.text()
    assert window.previews["A"].image.pixmap().width() <= (
        window.previews["A"].scroll.viewport().width() + 6
    )
    assert window.previews["B"].isVisible()
    assert window.selection_editor.isVisible()
    window.content_edit_menu.menu().actions()[0].trigger()
    assert window._replacing_existing_text
    assert "文字替换" in window.workflow_context.text()
    window.content_edit_menu.menu().actions()[1].trigger()
    assert window._reflowing_paragraph and not window._replacing_existing_text
    window.workspace_tabs.setCurrentIndex(1)
    assert window._edit_mode == "none"
    window.load_path("B", changed)
    app.processEvents()
    assert window.previews["B"].isVisible()
    assert window.previews["B"].image.pixmap() is not None
    assert window.previews["A"].image.pixmap().width() <= (
        window.previews["A"].scroll.viewport().width() + 6
    )
    window.show_advanced_inspector(0)
    assert window.inspector.isVisible()
    assert window.right_tabs.currentIndex() == 0
    window._json_output("测试报告", {"changed": True})
    assert window.results.isVisible()


def test_signed_file_still_reports_signature_restriction(ui, tmp_path: Path):
    window, app = ui
    original = tmp_path / "source.pdf"
    canvas = Canvas(str(original))
    canvas.drawString(75, 700, "Signed document")
    canvas.save()
    folder = tmp_path / "keys"
    password = "Synthetic personal certificate test password!"
    generate_personal_credentials(
        folder, label="Personal Test", password=password,
        algorithm="ECDSA-P256"
    )
    signed = sign_document(
        original, tmp_path / "signed.pdf",
        pfx=folder / "personal-signing.p12",
        password=password.encode()
    )
    window.load_path("B", signed)
    assert "检测到签名" in window.workflow_context.text()
    assert "编辑受限" in window.workflow_context.text()
    assert window._signature_audit is None
