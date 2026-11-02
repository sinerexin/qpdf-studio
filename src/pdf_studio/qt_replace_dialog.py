"""Interactive direct text-content replacement for selected PDF text operands.

This dialog reads a selected text-showing operator, changes the actual operand,
and previews a generated PDF. It never covers previous text with paint.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout,
)

from .replace_text import (
    glyph_near_pdf_point, list_text_runs, list_tj_spans,
    replace_text_run, replace_tj_span,
)
from .adjacent_text import list_adjacent_text_spans, replace_adjacent_text_span
from .visual import render_page


def open_replace_dialog(workbench, slot: str, page: int, x: float, y: float) -> None:
    source = workbench.docs.get(slot)
    if source is None:
        return
    try:
        hint = glyph_near_pdf_point(source, page=page, x=x, y=y)
        inventory = list_text_runs(source, page)
    except Exception as exc:
        workbench._error(f"无法读取旧文字操作数：{exc}")
        return
    if hint is None:
        QMessageBox.information(
            workbench, "点击处未找到文字", "该位置没有可识别的 PDF 文本字符。"
            "若是扫描件，需要先执行 OCR。"  # 限制：扫描图没有 PDF 文字操作数，不能直接替换
        )
        return
    clue = hint["text_hint"].strip()
    available = [row for row in inventory["runs"] if row["editable"]]
    matches = [row for row in available if clue and clue in row["text"]]
    for span in list_tj_spans(source, page):
        if clue and clue in span["text"]:
            matches.append({
                **span,
                "tj_span": True,
                "array_index": None,
            })
    for span in list_adjacent_text_spans(source, page):
        if clue and clue in span["text"]:
            matches.append({
                **span,
                "adjacent_span": True,
                "operator_index": span["start_operator_index"],
                "array_index": None,
            })
    if not matches:
        workbench._error(
            f"点击线索「{clue[:45]}」未对应到支持的原文内容流。"
            "未能可靠映射的字体、未出现过的子集字形及跨操作数文字不会猜测性修改。"
        )
        return

    dialog = QDialog(workbench)
    dialog.setWindowTitle("替换 PDF 已有文字 · 直接修改文字操作数")
    dialog.resize(660, 420)
    layout = QVBoxLayout(dialog)
    explanation = QLabel(
        f"{source.name} · 第 {page} 页 · 点击文字「{clue[:60]}」\n"
        "这里直接修改 PDF 字符串操作数（Tj 等），不会涂白或覆盖原文本。"  # 限制：文字替换不能涂白或覆盖原文本
        "选择「完整 TJ」可修改同一指令的多个字符串，"
        "「连续指令」可修改相邻的多个 Tj/TJ 指令（均要求字符数量相同）。"  # 限制：完整 TJ 和连续指令替换都要求字符数相同
        "本版本不自动重新排版，确认前请核对真实渲染效果。"
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    form = QFormLayout()
    choice = QComboBox()
    for row in matches[:300]:  # 限制：替换对话框最多列出 300 个匹配片段
        choice.addItem(
            f"{'连续指令' if row.get('adjacent_span') else '完整 TJ' if row.get('tj_span') else '单个片段'} "
            f"指令 #{row['operator_index']} · {row['font_resource']} "
            f"· {row.get('font_encoding', '')} · {row['text'][:100]}",
            row
        )
    original = QPlainTextEdit()
    original.setReadOnly(True)
    original.setMaximumHeight(85)
    replacement = QLineEdit()
    replacement.setMaxLength(3000)
    form.addRow("原文操作数", choice)
    form.addRow("操作数完整文字", original)
    form.addRow("替换为", replacement)
    layout.addLayout(form)

    def update_fields() -> None:
        selected = choice.currentData()
        if selected is not None:
            original.setPlainText(selected["text"])
            replacement.setText(selected["text"])

    choice.currentIndexChanged.connect(lambda _: update_fields())
    update_fields()

    def apply_direct_edit(target: Path) -> dict:
        selected = choice.currentData()
        if selected.get("adjacent_span"):
            return replace_adjacent_text_span(
                source, target, page=page,
                start_operator_index=selected["start_operator_index"],
                expected_text=selected["text"], replacement=replacement.text(),
            )
        if selected.get("tj_span"):
            return replace_tj_span(
                source, target, page=page,
                operator_index=selected["operator_index"],
                expected_text=selected["text"], replacement=replacement.text(),
            )
        return replace_text_run(
            source, target, page=page,
            operator_index=selected["operator_index"],
            array_index=selected["array_index"],
            expected_text=selected["text"], replacement=replacement.text(),
        )

    def preview() -> None:
        selected = choice.currentData()
        if selected is None:
            return
        target = Path(workbench._scratch.name) / f"real-replace-preview-{uuid.uuid4().hex[:12]}.pdf"
        try:
            apply_direct_edit(target)
            image = render_page(target, page - 1, 1.2)
            rgb = image.convert("RGB")
            qimg = QImage(rgb.tobytes(), rgb.width, rgb.height,
                          rgb.width * 3, QImage.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(qimg)
            window = QDialog(dialog)
            window.setWindowTitle("实际 PDF 内容流修改后的页面渲染预览")
            window.resize(810, 750)
            preview_layout = QVBoxLayout(window)
            image_label = QLabel()
            image_label.setPixmap(pixmap)
            image_label.resize(pixmap.size())
            scroll = QScrollArea()
            scroll.setWidget(image_label)
            preview_layout.addWidget(scroll)
            preview_layout.addWidget(QLabel("真实文件预览：请检查文字长度、对齐和相邻排版是否受影响。"))
            window.exec()
        except Exception as exc:
            QMessageBox.warning(dialog, "无法安全生成替换预览", str(exc))

    bar = QHBoxLayout()
    preview_button = QPushButton("预览直接替换结果")
    preview_button.clicked.connect(preview)
    bar.addWidget(preview_button)
    bar.addStretch()
    actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    bar.addWidget(actions)
    layout.addLayout(bar)
    if dialog.exec() != QDialog.Accepted:
        return

    selected = choice.currentData()
    if selected is None:
        return
    new_value = replacement.text()
    if new_value == selected["text"]:
        workbench._notify("新旧内容相同，不需要重保存")
        return
    target_name, _ = QFileDialog.getSaveFileName(
        workbench, "另存实际内容流修改后的 PDF",
        str(source.with_name(source.stem + "-replaced.pdf")), "PDF 文件 (*.pdf)"
    )
    if not target_name:
        return
    dst = Path(target_name)
    def complete(report: dict) -> None:
        workbench._json_output(f"PDF 原文字已在内容流中直接替换：{report['output']}", report)
        dimensions = report.get("layout_diagnostics", {})
        if dimensions.get("layout_review_recommended"):
            workbench._notify(
                "排版提醒：文字宽度变化约 "
                f"{dimensions.get('width_change_pt')} pt；请对照 A/B 页面确认位置和间距。"
            )
        elif dimensions.get("status") != "measured":
            workbench._notify("排版提醒：未能可靠测量字形边界，请人工查看实际 PDF 预览。")
        if slot == "B" and workbench.docs["A"] is None:
            workbench.load_path("A", source)
        workbench.load_path("B", dst)
    workbench._task(
        "替换已有 PDF 正文",
        lambda: apply_direct_edit(dst),
        complete,
    )
