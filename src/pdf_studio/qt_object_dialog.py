"""Click existing image/vector object -> direct operator edit -> real PDF preview."""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout,
)

from .object_editor import list_editable_objects, edit_existing_object
from .visual import render_page


def open_object_edit_dialog(workbench, slot: str, page: int,
                            x: float, y: float) -> None:
    source = workbench.docs.get(slot)
    if source is None:
        return
    try:
        info = list_editable_objects(source, page)
    except Exception as error:
        workbench._error(f"无法分析现有图片/矢量对象：{error}")
        return
    candidates = [
        item for item in info["objects"]
        if item["rect_pdf"][0] - 1 <= x <= item["rect_pdf"][2] + 1
        and item["rect_pdf"][1] - 1 <= y <= item["rect_pdf"][3] + 1
    ]
    if not candidates:
        QMessageBox.information(
            workbench, "点击处未识别到可选对象",
            "仅支持页面上可定位的单次使用 RGB Image XObject 和"
            "原始 q/rg/re/f/Q 矢量矩形。其他对象请使用「绘制分析」。"
        )
        return
    dialog = QDialog(workbench)
    dialog.setWindowTitle("直接编辑 PDF 原有图片 / 矢量对象")
    dialog.resize(660, 380)
    layout = QVBoxLayout(dialog)
    note = QLabel(
        "只修改当前 PDF 中真实存在的图片 Stream 或绘制指令，不添加白色遮盖、"  # 限制：对象编辑不能用白色遮盖或新图层假装改过
        "额外覆盖层或新的 Do 操作。图片尺寸必须保持原像素大小；"
        "共用资源和复杂透明度不能安全修改。"
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    form = QFormLayout()
    choice = QComboBox()
    for item in candidates:
        label = (
            f"{'原图片' if item['kind']=='image' else '原矢量填充'} "
            f"#{item['operator_index']} · {item['rect_pdf']} "
            f"{'可修改' if item['editable'] else '不支持：'+item['reason']}"
        )
        choice.addItem(label, item)
    form.addRow("选中的原对象", choice)
    fill = QLineEdit("#2277BB")
    fill.setPlaceholderText("#RRGGBB")
    form.addRow("修改已有填充颜色", fill)
    selected_file = QLineEdit()
    selected_file.setReadOnly(True)
    selected_file.setPlaceholderText("选择与原图片像素尺寸相同的 RGB PNG/JPEG")
    pick_row = QHBoxLayout()
    pick_row.addWidget(selected_file)
    choose = QPushButton("选择图片")
    def choose_image() -> None:
        filename, _ = QFileDialog.getOpenFileName(
            dialog, "选择新的 RGB 图片像素", "",
            "Image files (*.png *.jpg *.jpeg)"
        )
        if filename:
            selected_file.setText(filename)
    choose.clicked.connect(choose_image)
    pick_row.addWidget(choose)
    form.addRow("更新原 Image XObject", pick_row)
    explanation = QLabel("")
    explanation.setWordWrap(True)
    form.addRow("原始对象证据", explanation)
    layout.addLayout(form)

    def choose_changed() -> None:
        item = choice.currentData()
        if item is None:
            return
        editable = item["editable"]
        is_image = item["kind"] == "image"
        fill.setVisible(not is_image)
        selected_file.setVisible(is_image)
        choose.setVisible(is_image and editable)
        explanation.setText(
            f"操作数 #{item['operator_index']}；"
            + (f"原图资源 {item['resource']}，像素尺寸 {item['image_pixels']}；"
               f"对象引用 {item['xobject_ref']}；"
               if is_image else
               f"原色 RGB {item['color_rgb']}；")
            + ("本对象可直接修改。" if editable else f"拒绝修改原因：{item['reason']}")
        )
        if not is_image:
            rgb = item["color_rgb"]
            fill.setText("#" + "".join(
                f"{min(255,max(0,round(part*255))):02X}" for part in rgb
            ))
    choice.currentIndexChanged.connect(lambda _: choose_changed())
    choose_changed()

    def edit(target: Path) -> dict:
        item = choice.currentData()
        if not item or not item["editable"]:
            raise ValueError("目标对象是不可修改的共用资源或复杂图形")
        return edit_existing_object(
            source, target, page=page,
            operator_index=item["operator_index"], kind=item["kind"],
            replacement_image=Path(selected_file.text()) if item["kind"] == "image" else None,
            fill_hex=fill.text() if item["kind"] == "vector-fill" else None,
        )

    def preview() -> None:
        target = Path(workbench._scratch.name) / f"object-edit-preview-{uuid.uuid4().hex[:12]}.pdf"
        try:
            edit(target)
            rgb = render_page(target, page-1, 1.2).convert("RGB")
            qimg = QImage(rgb.tobytes(), rgb.width, rgb.height,
                          rgb.width * 3, QImage.Format_RGB888).copy()
            window = QDialog(dialog)
            window.setWindowTitle("真实 PDF 原对象修改后的渲染预览")
            window.resize(825, 760)
            preview_layout = QVBoxLayout(window)
            label = QLabel()
            image = QPixmap.fromImage(qimg)
            label.setPixmap(image)
            label.resize(image.size())
            scroll = QScrollArea()
            scroll.setWidget(label)
            preview_layout.addWidget(scroll)
            preview_layout.addWidget(QLabel(
                "本预览从实际修改后的 PDF 渲染；原资源和绘制坐标保持对应。"
            ))
            window.exec()
        except Exception as error:
            QMessageBox.warning(dialog, "原对象修改失败", str(error))

    toolbar = QHBoxLayout()
    preview_button = QPushButton("预览真实对象修改结果")
    preview_button.clicked.connect(preview)
    toolbar.addWidget(preview_button)
    toolbar.addStretch()
    actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    toolbar.addWidget(actions)
    layout.addLayout(toolbar)
    if dialog.exec() != QDialog.Accepted:
        return
    item = choice.currentData()
    if not item or not item["editable"]:
        workbench._error("对象不可安全修改，拒绝保存。")
        return
    name, _ = QFileDialog.getSaveFileName(
        workbench, "保存对象修改后的新 PDF",
        str(source.with_name(source.stem + "-object-edited.pdf")),
        "PDF 文件 (*.pdf)",
    )
    if not name:
        return
    dst = Path(name)
    kind = item["kind"]
    operator_index = item["operator_index"]
    asset = Path(selected_file.text()) if kind == "image" else None
    color = fill.text() if kind == "vector-fill" else None
    def complete(report: dict) -> None:
        workbench._json_output(
            f"原 PDF 图片/矢量对象已直接修改：{report['saved']}", report,
        )
        if slot == "B" and workbench.docs["A"] is None:
            workbench.load_path("A", source)
        workbench.load_path("B", dst)
    workbench._task(
        "修改原有 PDF 绘制对象",
        lambda: edit_existing_object(
            source, dst, page=page, operator_index=operator_index,
            kind=kind, replacement_image=asset, fill_hex=color,
        ),
        complete,
    )
