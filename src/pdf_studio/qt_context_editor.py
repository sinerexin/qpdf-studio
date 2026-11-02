"""在 A 选中原位置，将每次预览累计到 B；确认累计副本后进入签名步骤，不在此按钮导出文件。"""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .adjacent_text import list_adjacent_text_spans, replace_adjacent_text_span
from .object_editor import list_editable_objects, edit_existing_object
from .original_layout import reflow_like_original
from .paragraph_reflow import list_reflow_blocks
from .replace_text import (
    glyph_near_pdf_point, list_text_runs, list_tj_spans, replace_text_run, replace_tj_span,
)
from .text_metrics import unique_text_bounds
from .font_replacement import default_chinese_font, replace_isolated_text_with_font
from .font_cmap import FontEmbeddingRequired
from .scan_ocr import place_ocr_text, recognize_at


class ContextSelectionEditor(QFrame):
    """Live panel representing the native object selected on the original PDF."""

    def __init__(self, workbench):
        super().__init__()
        self.setObjectName("SelectionEditor")
        self.workbench = workbench
        self._source: Path | None = None
        self._page = 1
        self._selection: list[dict] = []
        self._asset: Path | None = None
        self._selection_epoch = 0
        self._revision = 0
        self._dirty = False
        self._result_token = None
        self._generated_source = None
        self._generated_result = None
        self._loading_fields = False
        self._temporary_previews: set[Path] = set()
        # 当前选区的稳定基准与最新 B 分开：同一处反复输入不会重复叠加，新选区继承最新 B。
        self._original_source = None
        self._expected_b = None
        self._document_epoch = 0
        self._editing_finished = False
        self._preview_requested = False
        self._selection_request = None
        self._queued_selection = None
        self._finish_requested = False
        self._selection_slot = 'A'
        self._ignored_b_on_reset = None
        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._continue_actions)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(700)
        self._preview_timer.timeout.connect(self._auto_preview)
        self.setMinimumWidth(235)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(9)
        head = QLabel("② 编辑选中内容")
        head.setObjectName("Heading")
        layout.addWidget(head)
        self.help = QLabel("始终在 A 点击要修改的位置。\n点击“预览修改”累计到 B；确认副本后进入第三步。")
        self.help.setWordWrap(True)
        self.help.setObjectName("WorkflowHint")
        layout.addWidget(self.help)
        self.info = QLabel("尚未选择对象")
        self.info.setWordWrap(True)
        self.info.setObjectName("WorkflowContext")
        layout.addWidget(self.info)
        self.choice = QComboBox()
        self.choice.setObjectName("SelectedPdfObject")
        self.choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.choice.setMinimumContentsLength(12)
        self.choice.currentIndexChanged.connect(self._choice_changed)
        layout.addWidget(self.choice)
        self.choice.hide()
        self.fields = QStackedWidget()
        empty = QWidget()
        self.fields.addWidget(empty)

        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("新文字")
        text_layout.addWidget(label)
        self.text_value = QPlainTextEdit()
        self.text_value.setObjectName("ContextTextValue")
        self.text_value.setPlaceholderText("直接修改选中的原文字")
        self.text_value.setMaximumHeight(200)
        text_layout.addWidget(self.text_value)
        self.font_policy = QComboBox()
        self.font_policy.addItem("自动：原字体优先，中文缺字时嵌入黑体", "auto")
        self.font_policy.addItem("严格保留原字体（可能不支持中文）", "original")
        self.font_policy.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font_policy.setMinimumContentsLength(12)
        text_layout.addWidget(self.font_policy)
        self.font_hint = QLabel()
        self.font_hint.setWordWrap(True)
        text_layout.addWidget(self.font_hint)
        self.fields.addWidget(text_box)

        image_box = QWidget()
        image_layout = QVBoxLayout(image_box)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_hint = QLabel("替换原图像的像素数据（像素尺寸必须相同）")
        image_hint.setWordWrap(True)
        image_layout.addWidget(image_hint)
        self.asset_path = QLineEdit()
        self.asset_path.setObjectName("ContextImagePath")
        self.asset_path.setPlaceholderText("选择新 RGB 图片")
        self.asset_path.setReadOnly(True)
        image_layout.addWidget(self.asset_path)
        pick = QPushButton("选择图片")
        self.pick_image_button = pick
        pick.clicked.connect(self._pick_image)
        image_layout.addWidget(pick)
        self.fields.addWidget(image_box)

        vector_box = QWidget()
        vector_layout = QFormLayout(vector_box)
        vector_layout.setContentsMargins(0, 0, 0, 0)
        self.color_value = QLineEdit("#167C80")
        self.color_value.setObjectName("ContextVectorColor")
        vector_layout.addRow("填充色", self.color_value)
        self.fields.addWidget(vector_box)

        layout.addWidget(self.fields)
        self.message = QLabel("原稿不覆盖；本按钮确认累计结果并进入第三步，最终文件另行导出。")
        self.message.setObjectName("WorkflowHint")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Actions stay visible even when the selected object's form is tall.
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(13, 8, 13, 12)
        self.save_state = QLabel("未选中内容")
        self.save_state.setWordWrap(True)
        self.save_state.setObjectName("WorkflowHint")
        footer_layout.addWidget(self.save_state)
        actions = QHBoxLayout()
        self.preview_button = QPushButton("预览修改")
        self.preview_button.setObjectName("ContextPreview")
        self.preview_button.clicked.connect(self.preview_edit)
        self.save_button = QPushButton("保存修改副本")
        self.save_button.setObjectName("ContextSave")
        self.save_button.setToolTip("确认 B 的全部累计修改并直接进入第三步；此按钮不弹出文件保存窗口")
        self.save_button.clicked.connect(lambda: self.save_edit())
        actions.addWidget(self.preview_button)
        actions.addWidget(self.save_button)
        footer_layout.addLayout(actions)
        # 保留其他面板使用的按钮接口，但不再设置第二个重复的“下一步”按钮。
        self.next_step_button = QPushButton("确认副本后自动进入第三步")
        self.next_step_button.hide()
        outer.addWidget(footer)
        self.text_value.textChanged.connect(self._edited)
        self.color_value.textEdited.connect(self._edited)
        self.font_policy.currentIndexChanged.connect(self._edited)
        self.preview_status = QLabel()
        self.preview_status.setWordWrap(True)
        self.preview_status.setTextFormat(Qt.PlainText)
        self.workbench.previews["B"].layout().insertWidget(2, self.preview_status)
        self.preview_status.hide()
        self.clear()

    def clear(self, *, reset_document=True) -> None:
        self._preview_timer.stop()
        self._selection_epoch += 1
        self._dirty = False
        self._queue_timer.stop()
        self._queued_selection = None
        self._finish_requested = False
        self._preview_requested = False
        self._selection_request = None
        if reset_document:
            # 导入新的 A 才开启新一轮；识别选区失败不能清掉累计成果。
            self._document_epoch += 1
            self._editing_finished = False
            self.fields.setEnabled(True)
            self.choice.setEnabled(True)
            self._generated_source = None
            self._generated_result = None
            self._ignored_b_on_reset = self.workbench.docs.get('B')
        self._original_source = self.workbench.docs.get('A')
        self._expected_b = self.workbench.docs.get('B')
        self._source = None
        self._selection = []
        self._asset = None
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.blockSignals(False)
        self.choice.hide()
        self.fields.setCurrentIndex(0)
        self.info.setText("未选中内容")
        self.message.setText("点击左侧页面中的文字或图片开始。")
        self.preview_button.setEnabled(False)
        self.save_button.setEnabled(self.workbench.docs.get('B') is not None)
        self.pick_image_button.setEnabled(False)
        self.save_state.setText("请先在原稿选中可编辑对象")
        self.next_step_button.setEnabled(False)
        self.preview_status.hide()
        if hasattr(self.workbench, "previews"):
            self.workbench.previews["A"].selection_box_pdf = None

    def _pick_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "选择替换 RGB 图片", "", "图片 (*.png *.jpg *.jpeg)")
        if name:
            self._asset = Path(name)
            self.asset_path.setText(name)
            self._edited()

    def _choice_changed(self, index: int) -> None:
        if not 0 <= index < len(self._selection):
            return
        self._preview_timer.stop()
        self._selection_epoch += 1
        self._revision = 0
        self._dirty = False
        self._loading_fields = True
        entry = self._selection[index]
        kind = entry["kind"]
        if kind in {"run", "tj", "adjacent", "paragraph", "ocr"}:
            self.fields.setCurrentIndex(1)
            self.text_value.setPlainText(entry["text"])
            size = entry.get("font_size") or 0
            tf_size = entry.get("tf_size")
            weight = "粗体" if entry.get("font_weight") == "bold" else "常规"
            size_note = f" · {size:g} pt · {weight}" if size else ""
            if size and tf_size and abs(size - float(tf_size)) > 0.05:
                size_note = f" · {size:g} pt（Tf {float(tf_size):g}） · {weight}"
            self.info.setText(
                f"已选中原文字 · 第 {self._page} 页\n"
                f"字体：{entry.get('font_resource', '原字体')}"
                + size_note
                + f"\n操作数：#{entry['operator_index']} · {entry['label']}"
            )
        elif kind == "image":
            self.fields.setCurrentIndex(2)
            self.asset_path.clear()
            self._asset = None
            self.info.setText(
                f"原图片对象 · 第 {self._page} 页\n"
                f"对象：{entry.get('xobject_ref')}\n"
                f"像素：{entry.get('image_pixels')}"
            )
        else:
            self.fields.setCurrentIndex(3)
            self.color_value.setText(
                "#" + "".join(f"{round(v*255):02X}" for v in entry["color_rgb"])
            )
            self.info.setText(
                f"原矢量矩形 · 第 {self._page} 页\n"
                f"操作数：#{entry['operator_index']}"
            )
        editable = entry.get("editable", True)
        self.preview_button.setEnabled(editable)
        self.save_button.setEnabled(editable or self.workbench.docs.get('B') is not None)
        self.text_value.setReadOnly(not editable)
        self.color_value.setReadOnly(not editable)
        self.font_policy.setEnabled(editable)
        self.pick_image_button.setEnabled(editable)
        self._loading_fields = False
        self.save_state.setText("点击“预览修改”累计本次编辑；全部完成后点“保存修改副本”进入第三步" if editable
                                else "该对象不可直接编辑，原因见上方")
        if not entry.get("editable", True):
            self.message.setText("该对象暂不支持修改：" + entry.get("reason", "映射不确定"))  # 限制：当前对象格式暂不支持修改
        else:
            self.message.setText("直接修改选中对象的真实 PDF 数据。可先预览，再另存新文件。")
        self._update_font_hint()
        self._preview_note("尚未生成当前选区的修改预览；B 区如有内容，是已有副本。", "pending")

    def select_at(self, source: Path, page: int, x: float, y: float) -> None:
        """换选区前先完成当前输入；排队接续，不悄悄丢掉尚未生成的修改。"""
        if self._editing_finished:
            # 用户指定的流程收尾：确认累计结果后，结束本轮内容编辑并进入第三步。
            self.workbench.studio_shell.advance_to_signing()
            return
        if self.has_pending_edits() or self.workbench._busy:
            self._queued_selection = (source, page, x, y)
            self._queue_timer.start(1)
            return
        self._select_now(source, page, x, y)

    def _select_now(self, source: Path, page: int, x: float, y: float) -> None:
        """操作数重新从最新 B 读取，绝不把 A 的旧操作数编号直接套到已编辑 B。"""
        self._preview_timer.stop()
        self._selection_epoch += 1
        original, working = self.workbench.docs.get('A'), self.workbench.docs.get('B')
        self._selection_slot = 'B' if working is not None and Path(source) == working else 'A'
        use_working = working is not None and (working != self._ignored_b_on_reset or self._selection_slot == 'B')
        self._source = Path(working if use_working else original or source).resolve(strict=True)
        self._original_source, self._expected_b = original, working
        self._page = page
        self._selection_request = (source, page, x, y)
        self.workbench.previews["A"].selection_box_pdf = None
        self.workbench.previews['B'].selection_box_pdf = None
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.blockSignals(False)
        self._selection = []
        self._asset = None
        try:
            inventory = list_text_runs(self._source, page)
            hint = glyph_near_pdf_point(self._source, page=page, x=x, y=y)
            # A 始终保留原版面。若 B 的替换文字变短，A 字尾的位置可能已没有字，
            # 此时从 A 原文字框的起点查找 B，不要求用户改到 B 上点击。
            original_hint = glyph_near_pdf_point(original, page=page, x=x, y=y) if use_working else hint
            original_box = None
            if original_hint:
                original_box = unique_text_bounds(original or self._source, page, original_hint['text_hint'])
            if hint is None and use_working and original_box:
                left, bottom, right, top = original_box
                hint = glyph_near_pdf_point(self._source, page=page,
                                           x=left + min(1.0, (right-left)/2), y=(bottom+top)/2)
            clue = hint["text_hint"].strip() if hint else ""
            if clue:
                for run in inventory["runs"]:
                    if run["editable"] and run["text"] and clue in run["text"]:
                        self._selection.append({
                            **run, "kind": "run", "label": "原文字片段",
                        })
                for row in list_tj_spans(self._source, page):
                    if clue in row["text"]:
                        self._selection.append({
                            **row, "kind": "tj", "label": "完整 TJ",
                        })
                for row in list_adjacent_text_spans(self._source, page):
                    if clue in row["text"]:
                        self._selection.append({
                            **row, "kind": "adjacent", "label": "连续指令",
                            "operator_index": row["start_operator_index"],
                        })
                for row in list_reflow_blocks(self._source, page):
                    if clue in row["text"]:
                        self._selection.append({
                            **row, "kind": "paragraph", "label": "段落重排",
                            "operator_index": row["start_operator_index"],
                        })
            if not self._selection:
                objects = list_editable_objects(self._source, page)
                for item in objects["objects"]:
                    left, bottom, right, top = item["rect_pdf"]
                    if left <= x <= right and bottom <= y <= top:
                        self._selection.append({
                            **item, "label": "原图片" if item["kind"] == "image" else "原矢量图形"
                        })
        except Exception as exc:
            self.clear(reset_document=False)
            self.info.setText("无法识别选中内容")
            self.message.setText(str(exc))
            return
        if not self._selection:
            try:
                recognized = recognize_at(self._source, page, x, y)
            except Exception as exc:
                self.clear(reset_document=False)
                self.info.setText("点击位置没有可直接修改的原对象")
                self.message.setText(str(exc))
                return
            self._selection.append(recognized)
        if not self._selection:
            self.clear(reset_document=False)
            self.info.setText("点击位置没有可直接修改的原对象")
            self.message.setText("扫描图片中的文字需要 OCR；不支持的字体或图形不会使用遮盖替代。")
            return
        self.choice.blockSignals(True)
        for entry in self._selection:
            preview = str(entry.get("text") or entry.get("resource", ""))[:40].replace("\n", " / ")
            self.choice.addItem(f"{entry['label']} · {preview}")
        self.choice.blockSignals(False)
        self.choice.setVisible(len(self._selection) > 1)
        self._choice_changed(0)
        selected = self._selection[0]
        if selected["kind"] in {"image", "vector-fill"}:
            highlight = selected["rect_pdf"]
        else:
            try:
                highlight = unique_text_bounds(self._source, page, selected["text"])
            except Exception:
                highlight = None
            if highlight is None:
                # Visual selection aid only; does not paint into the PDF.
                highlight = [x - 5, y - 6, x + 8, y + 8]
        # 选中提示留在用户点击的 A；实际操作数从最新 B 重新读取，不复用旧编号。
        self.workbench.previews['A'].selection_box_pdf = original_box or highlight
        if use_working:
            self.info.setText(self.info.text().replace('原文字', 'B 当前文字') + '\n后续修改保留 B 中此前全部结果。')
        self._cleanup_previews()
        self.workbench._render_delayed()

    def _continue_actions(self) -> None:
        """串行处理明确提交的预览、进入第三步和换选区请求，避免后台忙时丢掉操作。"""
        if self._editing_finished:
            return
        if self.workbench._busy:
            self._queue_timer.start(100)
            return
        if self.has_pending_edits():
            if self._preview_requested or self._finish_requested:
                self.preview_edit()
            elif self._queued_selection is not None:
                self.save_state.setText('当前输入尚未累计；点击“预览修改”后继续刚才选中的位置')
            return
        self._preview_requested = False
        if self._finish_requested:
            self._finish_editing()
            return
        if self._queued_selection is not None:
            selection, self._queued_selection = self._queued_selection, None
            self._select_now(*selection)

    def _cleanup_previews(self) -> None:
        for path in tuple(self._temporary_previews):
            self._discard_preview(path)

    def _edited(self) -> None:
        if self._loading_fields or not self._selection or self._editing_finished:
            return
        entry = self._selection[self.choice.currentIndex()]
        if not entry.get("editable", True):
            return
        self._revision += 1
        self._dirty = True
        self.save_state.setText("有新输入 · 点击“预览修改”累计到 B")
        self._update_font_hint()
        self._preview_note("当前输入尚未生成预览；下方如有图像，是上一次结果。", "pending")
        # 不因输入停顿自动提交；由用户点击预览，或确认进入第三步时统一提交。
        if hasattr(self.workbench, "studio_shell"):
            self.workbench.studio_shell.refresh_signing_flow()

    def _update_font_hint(self) -> None:
        if not self._selection:
            return
        entry = self._selection[self.choice.currentIndex()]
        # 提示与真正接线保持一致：中文子集与西文字体走同一个自动换字体入口。
        if entry['kind'] == 'run' and self.font_policy.currentData() == 'auto':
            self.font_hint.setText('自动模式：从内容流读取字号、颜色、字距和旋转；完整字体可直接抽出；缺字再按宋体/黑体匹配。扫描图可走 OCR。')
        elif entry['kind'] == 'run':
            self.font_hint.setText('当前选择仅使用原字体；要输入原字体未包含的新字，请切换“自动字体”。')
        else:
            self.font_hint.setText('完整 TJ、连续指令和段落仍采用各自排版逻辑。需要自动嵌入新字形时，请选择“原文字片段”。')

    def _preview_note(self, text: str, state: str) -> None:
        background, foreground = {
            "pending": ("#fff6e5", "#9a6700"),
            "error": ("#fdecec", "#d70015"),
            "ready": ("#eaf8ee", "#1f7a3a"),
        }[state]
        self.preview_status.setStyleSheet(
            f"background:{background}; color:{foreground}; padding:8px; border-radius:8px;")
        self.preview_status.setText(text)
        self.preview_status.show()

    def _auto_preview(self) -> None:
        """后台空闲后继续已点击的预览，不把输入停顿视作提交。"""
        if self._preview_requested:
            self._queue_timer.start(1)

    def has_pending_edits(self) -> bool:
        """Whether current input differs from the last successfully generated result."""
        return self._dirty and self._result_token != (self._selection_epoch, self._revision)

    def current_working_copy_ready(self) -> bool:
        return (self._generated_source is not None
                and self._generated_source == self.workbench.docs.get("A")
                and self._generated_result == self.workbench.docs.get("B")
                and not self.has_pending_edits())

    def _discard_preview(self, path: Path) -> None:
        # Only remove this editor's temporary artifacts, never imported PDFs
        # or the file currently displayed as B.
        # 同一选区重复预览仍需要它的基准文件，不能生成 B 后立即删掉基准。
        if path not in self._temporary_previews or path in (self.workbench.docs.get('B'), self._source):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return  # The workbench scratch directory also cleans up on exit.
        self._temporary_previews.discard(path)

    def _capture(self) -> dict:
        """Read ALL Qt input on GUI thread before dispatching background work."""
        if not self._source or not self._selection:
            raise ValueError("请先在左侧 PDF 选中已有内容")
        entry = dict(self._selection[self.choice.currentIndex()])
        if not entry.get("editable", True):
            raise ValueError("原对象格式暂不支持修改")  # 限制：原对象格式暂不支持修改
        return {
            "source": self._source, "page": self._page, "entry": entry,
            "text": self.text_value.toPlainText(),
            "image": self._asset, "color": self.color_value.text(),
            "font_policy": self.font_policy.currentData(),
        }

    @staticmethod
    def _apply(target: Path, selection: dict) -> dict:
        entry = selection["entry"]
        source = selection["source"]
        page = selection["page"]
        text = selection["text"]
        kind = entry["kind"]
        if kind == "paragraph":
            return reflow_like_original(
                source, target, page=page,
                start_operator_index=entry["operator_index"],
                expected_text=entry["text"], replacement=text,
            )
        if kind == "adjacent":
            return replace_adjacent_text_span(
                source, target, page=page,
                start_operator_index=entry["operator_index"],
                expected_text=entry["text"], replacement=text,
            )
        if kind == "tj":
            return replace_tj_span(
                source, target, page=page,
                operator_index=entry["operator_index"],
                expected_text=entry["text"], replacement=text,
            )
        if kind == "ocr":
            return place_ocr_text(
                source, target, page=page, x=entry["x"], y=entry["y"],
                text=text, size=float(entry.get("font_size") or 12),
                color=selection.get("color") or entry.get("color") or "#202020",
            )
        if kind == "run":
            try:
                # 原字体能编码时继续原位替换，不无条件更换字体。
                return replace_text_run(
                    source, target, page=page,
                    operator_index=entry['operator_index'], array_index=entry['array_index'],
                    expected_text=entry['text'], replacement=text)
            except FontEmbeddingRequired:
                if selection.get('font_policy', 'auto') != 'auto':
                    raise  # 尊重用户主动选择的“仅原字体”，不是按文件身份限制编辑。
                # 原来的“缺字即失败”改为自动嵌入；中文子集和 WinAnsi 共用此分支。
                return replace_isolated_text_with_font(
                    source, target, page=page, operator_index=entry['operator_index'],
                    array_index=entry['array_index'], expected_text=entry['text'], replacement=text,
                    font_path=default_chinese_font())
        return edit_existing_object(
            source, target, page=page,
            operator_index=entry["operator_index"], kind=kind,
            replacement_image=selection["image"] if kind == "image" else None,
            fill_hex=selection["color"] if kind == "vector-fill" else None,
        )

    def _dispatch(self, target: Path, *, preview: bool) -> None:
        self._preview_timer.stop()
        if self.workbench._busy:
            self._discard_preview(target)
            self._preview_requested = True
            self._queue_timer.start(100)
            self.save_state.setText('已接收输入，后台完成后自动继续生成')
            return
        if target.exists():
            self.message.setText("目标文件已存在；请选择新的文件名。")
            self.save_state.setText("未保存 · 请选择新的文件名")
            return
        if self._expected_b != self.workbench.docs.get('B') and self._selection_request is not None:
            text, color, asset = self.text_value.toPlainText(), self.color_value.text(), self._asset
            kind = self._selection[self.choice.currentIndex()]['kind'] if self._selection else None
            request = self._selection_request
            self._select_now(*request)
            if self._selection:
                index = next((i for i, entry in enumerate(self._selection) if entry['kind'] == kind), 0)
                self.choice.setCurrentIndex(index)
                self.text_value.setPlainText(text)
                self.color_value.setText(color)
                self._asset = asset
                self._edited()
            else:
                # 已导入的新 B 中未找到此位置时，输入留在编辑框，不写回旧 B。
                self.text_value.setPlainText(text)
                self.message.setText('B 已切换，请在 A 重新选择位置；刚才输入保留在编辑框。')
                self._discard_preview(target)
                self._finish_requested = False
                return
        try:
            snapshot = self._capture()
        except ValueError as exc:
            self.message.setText(str(exc))
            self.save_state.setText("未生成结果 · 请查看上方原因")
            return
        source = snapshot["source"]
        original = self._original_source
        previous_b = self.workbench.docs.get('B')
        document_epoch = self._document_epoch
        self._preview_requested = False
        token = (self._selection_epoch, self._revision)
        self.preview_button.setEnabled(False)
        self.message.setText("正在验证原内容流并生成 PDF…")
        self.save_state.setText("正在生成临时预览…" if preview else "正在保存修改副本…")
        self._preview_note("正在处理当前输入；下方如有图像，仍是上一次结果。", "pending")

        def restore_actions() -> None:
            valid = bool(self._selection)
            if valid:
                valid = self._selection[self.choice.currentIndex()].get("editable", True)
            self.preview_button.setEnabled(valid)
            self.save_button.setEnabled(valid or self.workbench.docs.get('B') is not None)

        def complete(report: dict) -> None:
            if (self._document_epoch != document_epoch or self.workbench.docs.get('A') != original
                    or self.workbench.docs.get('B') != previous_b
                    or token[0] != self._selection_epoch):
                self.workbench._notify("已忽略过期预览" if preview else f"副本已保存：{target}；当前编辑已变化")
                if preview:
                    self._discard_preview(target)
                # 新文档可能已有自己的请求，旧回调不能清掉它们或改变新文档按钮。
                if self._document_epoch == document_epoch:
                    restore_actions()
                    self._preview_requested = False
                    self._finish_requested = False
                    self._queued_selection = None
                return
            self._dirty = True
            try:
                self.workbench.load_path("B", target)
                self.workbench.active_slot.setCurrentText('A')
                # Loading a result resets navigation to page 1; return to the
                # edited page so its change is visible in the A/B panes.
                self.workbench._current_page = snapshot["page"] - 1
                self.workbench._update_indicator()
                self.workbench._render_delayed()
            except Exception as exc:
                self.save_state.setText("结果已生成，但载入预览失败" if preview else "副本已保存，但载入预览失败")
                self.message.setText(f"结果路径：{target}\n预览载入错误：{exc}")
                self.workbench._notify(self.message.text())
                self._preview_note("当前结果已写出，但预览载入失败；请查看右侧原因。", "error")
                return
            finally:
                restore_actions()
            self._result_token = token
            self._generated_source = original
            self._generated_result = target
            self._expected_b = target
            self._ignored_b_on_reset = None
            self.next_step_button.setEnabled(True)
            if hasattr(self.workbench, "studio_shell"):
                self.workbench.studio_shell.edit_preview_completed(source, target)
            self.message.setText(
                "已生成实际 PDF 预览（尚未正式保存）。" if preview
                else f"新 PDF 已保存：{target}。原稿保持不变。"
            )
            self.save_state.setText('本次预览已累计到 B；继续在 A 修改，或点击“保存修改副本”进入第三步')
            font_note = (f" · 本次已嵌入 {report['embedded_font']}，字体外观有变化"
                         if report.get("embedded_font") else "")
            self._preview_note(("当前输入的临时预览 · 尚未正式保存" if preview else "当前输入已另存为新 PDF") + font_note, "ready")
            self.workbench._notify(self.message.text())
            if (token == (self._selection_epoch, self._revision) and self._queued_selection is None
                    and not self._finish_requested):
                request = self._selection_request
                if request is not None:
                    self._select_now(*request)
                    self._dirty = False
                    self.save_state.setText('本次修改已累计到 B；继续在 A 选择下一处，或点击“保存修改副本”进入第三步')
                    self._preview_note('B 已包含本次及此前全部预览修改', 'ready')
            if token[0] == self._selection_epoch and token[1] != self._revision:
                self.save_state.setText('此前点击的修改已累计；新输入仍待点击“预览修改”')
                self._preview_note('B 是已提交结果；最新输入尚未累计', 'pending')
            self._cleanup_previews()
            restore_actions()
            self._queue_timer.start(1)

        def run() -> dict:
            return self._apply(target, snapshot)

        def failed(error: str) -> None:
            if self._document_epoch != document_epoch:
                self._discard_preview(target)
                return
            self._preview_requested = False
            # 保留现有 B 和当前输入，显示真实错误；取消尚未执行的换选区，避免丢输入。
            self._queued_selection = None
            self._finish_requested = False
            self._queue_timer.stop()
            if preview:
                self._discard_preview(target)
            restore_actions()
            if token != (self._selection_epoch, self._revision):
                self.workbench._notify("上一项修改未完成：" + error)
                self.save_state.setText('保留最新输入，请点击“预览修改”重试')
                return
            readable = error
            if ("does not support the replacement glyphs" in error
                    or "Replacement glyph U+" in error):
                readable = "原字体缺少当前文字的字形。对于普通文字片段，请使用自动字体模式；复杂字体子集暂不能直接加入新字形。\n详细原因：" + error
            self.message.setText("未修改原稿，无法完成操作：" + readable)
            self.save_state.setText("生成失败 · 修改未保存，请查看上方原因")
            self._preview_note("当前输入生成失败 · 下方如有图像，是旧预览，不是本次结果。", "error")
            self.workbench._notify(self.message.text())

        self.workbench._task("修改选中 PDF 对象", run, complete, on_failed=failed)

    def preview_edit(self) -> None:
        """每次点击把当前输入累计进 B，尚未改动时重复点击不会重复应用。"""
        if self._editing_finished:
            return
        self._preview_requested = True
        if self.workbench._busy:
            self._queue_timer.start(100)
            return
        if not self.has_pending_edits():
            self._preview_requested = False
            self._queue_timer.start(1)
            return
        temporary = Path(self.workbench._scratch.name) / f"selected-preview-{uuid.uuid4().hex}.pdf"
        self._temporary_previews.add(temporary)
        self._dispatch(temporary, preview=True)

    def save_edit(self) -> None:
        """按钮实际职责是确认当前 B 并进入第三步，不选择路径或导出 PDF。"""
        self._preview_timer.stop()
        if self._editing_finished:
            self.workbench.studio_shell.advance_to_signing()
            return
        # 保留按钮点击；若还有输入或后台预览，则先完成累计，再自动进入第三步。
        self._finish_requested = True
        self._queued_selection = None
        self.save_state.setText('正在确认累计修改，随后进入第三步“签名与证书”')
        self._continue_actions()

    def _finish_editing(self) -> None:
        """只切换工作流程，B 沿用当前累计 PDF；不复制、不导出、不弹保存框。"""
        original = self.workbench.docs.get('A')
        working = self.workbench.docs.get('B')
        if working is None or working == self._ignored_b_on_reset:
            # 尚未做修改也可进入第三步：将原稿作为只读工作基准，不另写一个文件。
            if original is not None:
                page = self.workbench._current_page
                try:
                    self.workbench.load_path('B', original)
                except Exception as exc:
                    self._finish_requested = False
                    self.message.setText('载入第三步工作基准失败：' + str(exc))
                    return
                self.workbench._current_page = page
                self.workbench._update_indicator()
                self.workbench._render_delayed()
                working = self.workbench.docs.get('B')
        # 锁定仅表示用户已结束第二步，不依据签名身份、CA 或证书来源增加条件。
        self._generated_source, self._generated_result = original, working
        self._expected_b = working
        self._dirty = False
        self._editing_finished = True
        self._finish_requested = False
        self._queued_selection = None
        self._preview_requested = False
        self._queue_timer.stop()
        self.fields.setEnabled(False)
        self.choice.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.save_state.setText('累计修改已确认；本轮内容编辑结束，进入第三步')
        self._preview_note('B 为当前累计工作副本；此步骤不导出文件', 'ready')
        self._cleanup_previews()
        self.workbench.studio_shell.advance_to_signing()
