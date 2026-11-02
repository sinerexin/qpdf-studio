"""Qt direct PDF paragraph text-content editor with real rendered preview."""
from __future__ import annotations

import uuid
from pathlib import Path

import pikepdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout,
)

from .paragraph_reflow import list_reflow_blocks, reflow_text_object
from .flow_layout import reflow_with_following_text
from .original_layout import read_original_layout, reflow_like_original
from .replace_text import glyph_near_pdf_point
from .visual import render_page


def open_paragraph_dialog(workbench, slot: str, page: int, x: float, y: float) -> None:
    source = workbench.docs.get(slot)
    if source is None:
        return
    try:
        hint = glyph_near_pdf_point(source, page=page, x=x, y=y)
        blocks = list_reflow_blocks(source, page)
    except Exception as error:
        workbench._error(f"无法解析段落文本对象：{error}")
        return
    if hint is None:
        QMessageBox.information(
            workbench, "没有可识别的文字",
            "点击位置没有实际 PDF 文本字符。扫描图像请先 OCR。",  # 限制：扫描图没有 PDF 文字对象，不能做段落重排
        )
        return
    clue = hint["text_hint"].strip()
    matches = [item for item in blocks if clue and clue in item["text"]]
    if not matches:
        workbench._error(
            f"文字「{clue[:50]}」不在可安全重排的独立 BT..ET 文本对象中。"
            "存在复杂字体、定位、文本状态或多层结构时不进行覆盖式替换。"
        )
        return
    with pikepdf.Pdf.open(source) as pdf:
        sheet = pdf.pages[page - 1]
        bottom = float(sheet.MediaBox[1])
        right = float(sheet.MediaBox[2])

    dialog = QDialog(workbench)
    dialog.resize(730, 570)
    dialog.setWindowTitle("直接编辑 PDF 文字段落 · 自动换行")
    layout = QVBoxLayout(dialog)
    info = QLabel(
        "只支持独立 BT/ET、单字体、普通水平文本矩阵的段落。"
        "默认直接读取原稿中的字体、字号、坐标和行距，并从原稿字形宽度"
        "推断段落范围，再重建 Tj/T* 内容流。不会涂白或使用图像覆盖。"
        "原 PDF 未存储的排版规则会明确标记为推断。"
    )
    info.setWordWrap(True)
    layout.addWidget(info)
    form = QFormLayout()
    selected = QComboBox()
    for item in matches:
        summary = item["text"][:90].replace("\n", " / ")
        selected.addItem(
            f"#{item['start_operator_index']} · {item['font_resource']} · "
            f"{item['font_size_pt']:g} pt · {summary}",
            item,
        )
    form.addRow("原有段落", selected)
    replacement = QPlainTextEdit()
    replacement.setPlaceholderText("输入不同长度的正文；显式换行会保留。")
    replacement.setMinimumHeight(180)
    form.addRow("修改后的正文", replacement)
    use_original = QCheckBox("使用原稿的字体、行距、坐标和推断宽度自动排版（默认）")
    use_original.setChecked(True)
    form.addRow("原稿排版规则", use_original)
    evidence = QLabel("读取原稿排版资料...")
    evidence.setWordWrap(True)
    form.addRow("来源与规则", evidence)
    width = QDoubleSpinBox()
    width.setDecimals(1)
    width.setRange(20, 3000)
    width.setSuffix(" pt")
    form.addRow("最大行宽", width)
    height = QDoubleSpinBox()
    height.setDecimals(1)
    height.setRange(20, 3000)
    height.setSuffix(" pt")
    form.addRow("可用高度", height)
    follow = QCheckBox("同步调整下方同列的独立文本对象（严格检查后执行）")
    follow.setChecked(False)
    follow.setToolTip(
        "仅限完全安全的文字型 PDF；包含图片、图形、旋转、"
        "其他栏、复杂文字状态或页面空间不足时拒绝操作。"
    )
    form.addRow("可选布局联动", follow)
    layout.addLayout(form)

    def mode_changed() -> None:
        advanced = not use_original.isChecked()
        width.setEnabled(advanced)
        height.setEnabled(advanced)
        follow.setEnabled(advanced)
        if use_original.isChecked():
            follow.setChecked(False)

    use_original.toggled.connect(lambda _: mode_changed())

    def selection_changed() -> None:
        item = selected.currentData()
        if item is None:
            return
        replacement.setPlainText(item["text"])
        try:
            profile = read_original_layout(
                source, page=page,
                start_operator_index=item["start_operator_index"],
            )
            observed = profile["observed"]
            inferred = profile["inferred"]
            width.setValue(max(20, min(3000, inferred["max_line_width_pt"])))
            height.setValue(max(20, min(3000, inferred["available_vertical_height_pt"])))
            evidence.setText(
                f"【原文实际指令】字体 {observed['font_resource']}，"
                f"字号 {observed['font_size_pt']:g} pt，"
                f"行距 {observed['line_leading_pt']:g} pt，"
                f"起点 {observed['origin_pdf']}，"
                f"原文 {observed['original_line_count']} 行。\n"
                f"【从原稿推断】行宽 {inferred['max_line_width_pt']:g} pt，"
                f"置信度 {inferred['layout_confidence']}。"
                "PDF 未必保存作者的排版模板，复杂页面会拒绝自动重排。"
            )
        except Exception as err:
            width.setValue(max(20, min(330, right - item["start_x_pt"] - 20)))
            height.setValue(max(20, min(240, item["start_y_pt"] - bottom - 20)))
            evidence.setText(
                f"原稿排版规则无法可靠恢复：{err}。"
                "自动模式将拒绝保存；只有明确关闭自动模式才能手动设置。"
            )

    selected.currentIndexChanged.connect(lambda _: selection_changed())
    selection_changed()
    mode_changed()

    def edit(target: Path) -> dict:
        item = selected.currentData()
        if use_original.isChecked():
            return reflow_like_original(
                source, target, page=page,
                start_operator_index=item["start_operator_index"],
                expected_text=item["text"],
                replacement=replacement.toPlainText(),
            )
        operation = reflow_with_following_text if follow.isChecked() else reflow_text_object
        return operation(
            source, target, page=page,
            start_operator_index=item["start_operator_index"],
            expected_text=item["text"],
            replacement=replacement.toPlainText(),
            width_pt=width.value(), height_pt=height.value(),
        )

    def preview() -> None:
        target = Path(workbench._scratch.name) / f"reflow-preview-{uuid.uuid4().hex[:12]}.pdf"
        try:
            result = edit(target)
            image = render_page(target, page - 1, 1.2)
            rgb = image.convert("RGB")
            qimg = QImage(rgb.tobytes(), rgb.width, rgb.height,
                          rgb.width * 3, QImage.Format_RGB888).copy()
            window = QDialog(dialog)
            window.resize(830, 780)
            window.setWindowTitle("真正重排后的 PDF 页面预览")
            panel = QVBoxLayout(window)
            picture = QLabel()
            pixmap = QPixmap.fromImage(qimg)
            picture.setPixmap(pixmap)
            picture.resize(pixmap.size())
            viewport = QScrollArea()
            viewport.setWidget(picture)
            panel.addWidget(viewport)
            panel.addWidget(QLabel(
                f"原文 {result['before_line_count']} 行 → "
                f"新文 {result['after_line_count']} 行；"
                f"文字高度 {result['height_used_pt']} pt。"
                "请检查与页面其他内容的间距。"
            ))
            if result.get("flow_plan"):
                moved = result["flow_plan"]["following"]
                panel.addWidget(QLabel(
                    f"本次直接修改 {len(moved)} 个后续文字对象的 Tm，"
                    f"移动量：{result['flow_plan']['shift_pt']:+g} pt。"
                ))
            if result.get("original_layout"):
                profile = result["original_layout"]["source_layout"]
                panel.addWidget(QLabel(
                    "此次使用原稿格式：字号、行距、原始起点来自底层指令；"
                    "段落宽度为基于原稿的估计值，不是 PDF 中显式保存的模板。"
                ))
            window.exec()
        except Exception as error:
            QMessageBox.warning(dialog, "无法安全重排段落", str(error))

    footer = QHBoxLayout()
    preview_button = QPushButton("预览实际 PDF")
    preview_button.clicked.connect(preview)
    footer.addWidget(preview_button)
    footer.addStretch()
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    footer.addWidget(buttons)
    layout.addLayout(footer)
    if dialog.exec() != QDialog.Accepted:
        return
    item = selected.currentData()
    new_text = replacement.toPlainText()
    if new_text == item["text"]:
        workbench._notify("段落没有变化，不需要保存")
        return
    output, _ = QFileDialog.getSaveFileName(
        workbench, "将重排后的正文另存为新 PDF",
        str(source.with_name(source.stem + "-reflowed.pdf")),
        "PDF 文件 (*.pdf)",
    )
    if not output:
        return
    target = Path(output)
    start_index = item["start_operator_index"]
    previous = item["text"]
    max_width, max_height = width.value(), height.value()
    shift_following = follow.isChecked()
    original_mode = use_original.isChecked()
    def completed(result: dict) -> None:
        workbench._json_output(
            f"段落已直接重排：{result['before_line_count']} → "
            f"{result['after_line_count']} 行", result,
        )
        if slot == "B" and workbench.docs["A"] is None:
            workbench.load_path("A", source)
        workbench.load_path("B", target)
        workbench._notify("请检查 A/B 对比；新段落仍可能与独立的其他内容重叠。")

    workbench._task(
        "重写 PDF 段落文字操作数",
        lambda: (
            reflow_like_original(
                source, target, page=page,
                start_operator_index=start_index,
                expected_text=previous, replacement=new_text,
            ) if original_mode
            else (reflow_with_following_text if shift_following else reflow_text_object)(
                source, target, page=page,
                start_operator_index=start_index,
                expected_text=previous, replacement=new_text,
                width_pt=max_width, height_pt=max_height,
            )
        ),
        completed,
    )
