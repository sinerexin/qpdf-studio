"""PySide6 professional PDF workbench, reusing the mature qpdf-based backend.

Functional GUI: file previews, structural tree, property editing, comparison,
PDFium visual diff, signed-document validation, page tools and local exports.
Never edits an existing original PDF in place.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

import pikepdf
from PySide6.QtCore import QEventLoop, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSlider, QSplitter, QTabWidget, QToolButton, QMenu, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget, QDialogButtonBox, QDoubleSpinBox,
    QTextEdit,
)

from .bundle import analyze_complete
from .core import inspect_pdf, compare
from .deep import semantic_inventory
from .editing import EDITABLE_PROPERTIES, _signed_or_certified, document_properties, edit_properties
from .signing import sign_document
from .visual import render_page, visual_diff
from .revisions import inspect_revisions
from .paint_diagnostics import inspect_paint_operations
from .qt_keygen_dialog import open_personal_certificate_editor
from .body_text import add_body_text
from .text_search import find_text
from .signature_report import save_signature_html
from .qt_replace_dialog import open_replace_dialog
from .qt_paragraph_dialog import open_paragraph_dialog
from .qt_object_dialog import open_object_edit_dialog
from .qt_signature_comparison import show_signature_comparison
from .signature_profile import inspect_signature_profile
from .qt_signer_output_dialog import open_signer_output_dialog
from .qt_context_editor import ContextSelectionEditor
from . import APP_NAME


STYLESHEET = """
/* Light, quiet desktop surface: white cards, hairline borders, one blue accent. */
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: 12px;
          color: #1d1d1f; }
QMainWindow, QWidget#Root { background: #f5f5f7; }
QFrame#Sidebar { background: #ffffff; border-right: 1px solid #e5e5ea; }
QFrame#Inspector { background: #ffffff; border: 1px solid #e5e5ea;
                   border-radius: 12px; }
QFrame#Toolbar { background: #ffffff; border: 1px solid #e5e5ea;
                 border-radius: 12px; }
QFrame#Panel { background: #ffffff; border: 1px solid #e5e5ea;
               border-radius: 12px; }
QLabel#Brand { color: #1d1d1f; font-size: 21px; font-weight: 600; }
QLabel#Heading { color: #1d1d1f; font-size: 14px; font-weight: 600; }
QLabel#Dim { color: #6e6e73; }
QLabel#WorkflowContext { color: #6e6e73; font-size: 12px; }
QLabel#WorkflowHint { color: #86868b; font-size: 11px; }
QPushButton { background: #ffffff; color: #1d1d1f; border: 1px solid #d2d2d7;
              padding: 6px 12px; border-radius: 8px; }
QPushButton:hover { background: #f5f5f7; }
QPushButton:pressed { background: #e8e8ed; }
QPushButton:disabled { color: #aeaeb2; background: #f5f5f7; border-color: #e5e5ea; }
QPushButton#Primary { background: #0071e3; color: white; border: 1px solid #0071e3;
                      font-weight: 600; }
QPushButton#Primary:hover { background: #0077ed; border-color: #0077ed; }
QPushButton#Primary:pressed { background: #006edb; }
QPushButton#EditMode:checked { background: #e8f2fc; color: #0071e3;
                              border: 1px solid #0071e3; font-weight: 600; }
QPushButton:focus { border: 1px solid #0071e3; }
QListWidget { background: #ffffff; border: 0; color: #1d1d1f; }
QListWidget::item { padding: 10px 9px; margin: 2px 2px; border-radius: 8px; }
QListWidget::item:hover { background: #f5f5f7; }
QListWidget::item:selected { background: #e8e8ed; color: #1d1d1f; }
QTreeWidget, QPlainTextEdit, QTextEdit {
    background: #ffffff; color: #1d1d1f; border: 1px solid #e5e5ea;
    border-radius: 8px; selection-background-color: #d6e8fa;
}
QTreeWidget::item { height: 25px; }
QTreeWidget::item:selected { color: #1d1d1f; background: #e8f2fc; }
QHeaderView::section { background: #f5f5f7; color: #6e6e73;
                       padding: 5px; border: 0; border-bottom: 1px solid #e5e5ea; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff; color: #1d1d1f; border: 1px solid #d2d2d7;
    padding: 5px 8px; border-radius: 8px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #0071e3;
}
QTabWidget::pane { background: #ffffff; border: 1px solid #e5e5ea;
                   border-radius: 10px; }
QTabBar::tab { background: transparent; color: #6e6e73; border: 0;
               padding: 7px 15px; border-radius: 8px; }
QTabBar::tab:selected { background: #ffffff; color: #1d1d1f; font-weight: 600; }
QTabBar::tab:hover:!selected { background: #f5f5f7; }
QTabWidget#WorkspaceTabs::pane { background: transparent; border: 0; }
QTabWidget#WorkspaceTabs QTabBar { background: #e8e8ed; border-radius: 10px; }
QTabWidget#WorkspaceTabs QTabBar::tab { min-width: 110px; padding: 7px 18px; margin: 2px; }
QTabWidget#ActionTabs::pane { background: #ffffff; border: 0; border-radius: 0; }
QTabWidget#ActionTabs QTabBar::tab { padding: 5px 15px; }
QTabWidget#RightTabs::pane { background: #ffffff; border: 0; }
QTabWidget#RightTabs QTabBar::tab { padding: 6px 9px; }
QScrollArea { background: #f5f5f7; border: 0; }
QSplitter::handle { background: #f5f5f7; }
QProgressBar { background: #e8e8ed; border: 0; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #0071e3; border-radius: 3px; }
QSlider::groove:horizontal { background: #e8e8ed; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffffff; border: 1px solid #d2d2d7; width: 14px;
                             margin: -6px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { border-color: #0071e3; }
QToolButton { background: #ffffff; color: #1d1d1f; border: 1px solid #d2d2d7;
              padding: 6px 14px; border-radius: 8px; }
QToolButton:hover { background: #f5f5f7; }
QToolButton#PrimaryMenu { background: #0071e3; color: #ffffff; border-color: #0071e3;
                          font-weight: 600; }
QToolButton#PrimaryMenu:hover { background: #0077ed; border-color: #0077ed; }
QMenu { background: #ffffff; color: #1d1d1f; border: 1px solid #d2d2d7;
        padding: 4px; border-radius: 10px; }
QMenu::item { padding: 7px 18px; border-radius: 6px; }
QMenu::item:selected { background: #e8f2fc; color: #1d1d1f; }
QFrame#SelectionEditor { background: #ffffff; border: 0; border-radius: 12px; }
"""


class TaskSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, call: Callable[[], Any]):
        super().__init__()
        self.call = call
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            self.signals.completed.emit(self.call())
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def _json(value: Any, max_chars: int = 100_000) -> str:
    body = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return body if len(body) <= max_chars else body[:max_chars] + "\n…（请导出完整结果）"


class ClickablePage(QLabel):
    clicked = Signal(float, float)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.pixmap() is not None:
            self.clicked.emit(float(event.position().x()), float(event.position().y()))
        super().mousePressEvent(event)


class PagePreview(QFrame):
    clicked = Signal(str, float, float)

    def __init__(self, slot: str):
        super().__init__()
        self.setObjectName("Panel")
        self.slot = slot
        self.selection_box_pdf: list[float] | None = None
        self.filename = QLabel("未选择 PDF")
        self.filename.setObjectName("Dim")
        self.filename.setWordWrap(True)
        self.image = ClickablePage(
            "选择右上角「打开原稿」开始\n所有修改将另存为新 PDF"
            if slot == "A" else "新 PDF 结果将在这里显示"
        )
        self.image.setAlignment(Qt.AlignCenter)
        self.image.clicked.connect(lambda x, y: self.clicked.emit(self.slot, x, y))
        self.image.setObjectName("PDFPreviewCanvas")
        self.image.setStyleSheet("color: #86868b; background: #f5f5f7; font-size: 14px;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.image)
        self._pixels: QPixmap | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        header = QHBoxLayout()
        tag = QLabel("① 原稿 · 点击选取" if slot == "A" else "③ 修改结果 · PDF 预览")
        tag.setObjectName("Heading")
        header.addWidget(tag)
        header.addStretch()
        layout.addLayout(header)
        layout.addWidget(self.filename)
        layout.addWidget(self.scroll, 1)

    def show_image(self, image, *, boxes: list | None = None,
                   pdf_page_height: float = 0, scale: float = 1.0) -> None:
        rgb = image.convert("RGB")
        if boxes and pdf_page_height > 0:
            from PIL import ImageDraw
            brush = ImageDraw.Draw(rgb, "RGBA")
            for left, bottom, right, top in boxes[:200]:
                rectangle = (
                    left * scale, (pdf_page_height - top) * scale,
                    right * scale, (pdf_page_height - bottom) * scale,
                )
                brush.rectangle(rectangle, fill=(0, 113, 227, 56), outline=(0, 113, 227, 180), width=2)
        if self.selection_box_pdf:
            from PIL import ImageDraw
            left, bottom, right, top = self.selection_box_pdf
            if pdf_page_height <= 0:
                pdf_page_height = rgb.height / scale
            brush = ImageDraw.Draw(rgb, "RGBA")
            brush.rectangle(
                (left * scale, (pdf_page_height - top) * scale,
                 right * scale, (pdf_page_height - bottom) * scale),
                fill=(0, 113, 227, 40), outline=(0, 113, 227, 180), width=2,
            )
        raw = rgb.tobytes()
        qimg = QImage(raw, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()
        self._pixels = QPixmap.fromImage(qimg)
        self.image.setWordWrap(False)
        self.image.setMinimumSize(0, 0)
        self.image.setMaximumSize(16777215, 16777215)
        self.image.setText("")
        self.image.setPixmap(self._pixels)
        self.image.resize(self._pixels.size())

    def clear(self, message: str = "尚无内容") -> None:
        self._pixels = None
        self.image.setPixmap(QPixmap())
        self.image.setWordWrap(True)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setText(message)
        # 固定 350 宽会把长路径从中间裁开，第 4 步看起来像没有内容。
        width = self.scroll.viewport().width()
        height = self.scroll.viewport().height()
        self.image.setMinimumSize(0, 0)
        self.image.setMaximumSize(16777215, 16777215)
        self.image.resize(max(280, width - 12), max(220, height - 12))


class PdfStudioQt(QMainWindow):
    def __init__(self):
        super().__init__()
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 680)
        # Fit the real Windows work area. The old 1560px initial width could
        # exceed a 1520px display and clip the brand/sidebar on the left.
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1420, max(1100, available.width() - 40))
            height = min(880, max(680, available.height() - 60))
            self.resize(width, height)
            if available.width() >= width and available.height() >= height:
                self.move(
                    available.x() + (available.width() - width) // 2,
                    available.y() + (available.height() - height) // 2,
                )
        else:
            self.resize(1400, 860)
        self._scratch = tempfile.TemporaryDirectory(prefix="qpdf-studio-qt-")
        self.pool = QThreadPool(self)
        self._jobs: set[Task] = set()
        self._busy = False
        self.docs: dict[str, Path | None] = {"A": None, "B": None}
        self.document_protection: dict[str, str] = {"A": "未打开", "B": "未打开"}
        self.pages_count = {"A": 0, "B": 0}
        self.manifests: dict[str, dict] = {}
        self.properties: dict[str, str] = {}
        self._diff_files: list[Path] = []
        self._diff_page = 0
        self.report: dict | None = None
        self.revision_report: dict | None = None
        self._placing_body_text = False
        self._replacing_existing_text = False
        self._reflowing_paragraph = False
        self._editing_original_object = False
        self._edit_mode = "none"
        self._mode_buttons: dict[str, QPushButton] = {}
        self._search_hits: dict[str, list[dict]] = {}
        self._search_slot: str | None = None
        self._signature_audit: dict | None = None
        self._render_scales: dict[str, float] = {"A": 1.0, "B": 1.0}
        self._build()
        from .qt_workbench_shell import WorkbenchShell
        self.studio_shell = WorkbenchShell(self)
        self._bind_shortcuts()
        icon = Path(__file__).resolve().parents[2] / "assets" / "qpdf-studio.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

    def _button(self, label: str, action: Callable, *, primary: bool = False) -> QPushButton:
        button = QPushButton(label)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(action)
        return button

    def _bind_shortcuts(self) -> None:
        commands = (
            ("Ctrl+O", lambda: self.open_file("A")),
            ("Ctrl+Shift+O", lambda: self.open_file("B")),
            ("Ctrl+F", self.search_document),
            ("Ctrl+1", lambda: self.workspace_tabs.setCurrentIndex(0)),
            ("Ctrl+2", lambda: self.workspace_tabs.setCurrentIndex(1)),
            ("Ctrl+3", self.show_advanced_inspector),
            ("Ctrl+4", self.show_comparison),
            ("Escape", lambda: self._set_edit_mode("none")),
        )
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in commands:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _tool_menu(self, text: str, entries: list[tuple], *, primary: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text + "  ▾")
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.InstantPopup)
        if primary:
            button.setObjectName("PrimaryMenu")
        menu = QMenu(button)
        for label, callback, tooltip in entries:
            action = menu.addAction(label)
            action.setToolTip(tooltip)
            action.triggered.connect(callback)
        button.setMenu(menu)
        button.setMinimumHeight(36)
        button.setToolTip("展开选择具体操作")
        return button

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(18, 13, 18, 11)
        main_layout.setSpacing(8)
        top = QFrame()
        top.setObjectName("Toolbar")
        top_layout = QHBoxLayout(top)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        top_layout.addWidget(brand)
        title = QLabel("  ·  PDF 工作台")
        title.setObjectName("Dim")
        top_layout.addWidget(title)
        top_layout.addStretch()
        button_a = self._button("打开原稿", lambda: self.open_file("A"), primary=True)
        button_a.setToolTip("Ctrl+O · 加载要研究或修改的原稿")
        top_layout.addWidget(button_a)
        compare_button = self._button("对比结果", self.show_comparison)
        compare_button.setToolTip("Ctrl+4 · 没有 B 时先导入对比文件；已有 B 时直接对比")
        top_layout.addWidget(compare_button)
        main_layout.addWidget(top)
        self.workflow_context = QLabel("打开原稿后，选择操作；修改始终另存新文件。")
        self.workflow_context.setObjectName("WorkflowContext")
        self.workflow_context.setWordWrap(True)
        main_layout.addWidget(self.workflow_context)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("WorkspaceTabs")
        self.workspace_tabs.setDocumentMode(True)
        content_page = QWidget()
        content_layout = QHBoxLayout(content_page)
        content_layout.setContentsMargins(12, 7, 12, 7)
        content_layout.setSpacing(9)
        self.content_edit_menu = self._tool_menu("编辑内容", [
            ("替换已有文字", self.start_replace_text, "点击原文字，直接修改底层操作数"),
            ("段落重排", self.start_paragraph_reflow, "以原 PDF 的文字样式为依据重新排版"),
            ("点击添加正文", self.start_body_editor, "新增真实文字内容流"),
            ("直接编辑图片/图形", self.start_object_editor, "修改可支持的原图片或图形对象"),
        ], primary=True)
        content_layout.addWidget(self.content_edit_menu)
        content_layout.addStretch()
        self.workspace_tabs.addTab(content_page, "内容编辑")
        signing_page = QWidget()
        signing_layout = QHBoxLayout(signing_page)
        signing_layout.setContentsMargins(12, 7, 12, 7)
        signing_layout.setSpacing(9)
        self.signer_output_button = self._button("修改签署者并产出", self.reissue_signer_and_output, primary=True)
        self.signer_output_button.setToolTip("使用自己持有的个人私钥重新签发后签署未签名 PDF")
        signing_layout.addWidget(self.signer_output_button)
        signing_layout.addWidget(self._button("签署 PDF", self.sign_active))
        signing_layout.addWidget(self._button("验签", self.verify_active))
        self.signing_tools = self._tool_menu("密钥与对照", [
            ("生成公钥 / 加密私钥", self.generate_key_pair, "创建独立个人密钥及加密 P12"),
            ("修改自签名证书", self.reissue_certificate, "使用自己的私钥重新签发证书"),
            ("A/B 签名身份对照", self.compare_signature_identities, "比较双方身份声明和信任状态"),
            ("按原件算法生成个人密钥", self.generate_compatible_personal_keys, "只匹配算法，不复制身份"),
        ])
        signing_layout.addWidget(self.signing_tools)
        signing_layout.addStretch()
        self.workspace_tabs.addTab(signing_page, "数字签名")
        self.workspace_tabs.currentChanged.connect(self._on_workspace_changed)
        self.workspace_tabs.setFixedHeight(108)
        main_layout.addWidget(self.workspace_tabs)

        self.page_controls = QWidget()
        controls = QHBoxLayout(self.page_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("当前操作文件"))
        self.active_slot = QComboBox()
        self.active_slot.addItems(["A", "B"])
        self.active_slot.setFixedWidth(76)
        self.active_slot.currentTextChanged.connect(self._on_slot_change)
        controls.addWidget(self.active_slot)
        controls.addSpacing(18)
        controls.addWidget(self._button("上一页", lambda: self.move_page(-1)))
        self.page_indicator = QLabel("第 1 页")
        self.page_indicator.setObjectName("Dim")
        controls.addWidget(self.page_indicator)
        controls.addWidget(self._button("下一页", lambda: self.move_page(1)))
        controls.addSpacing(18)
        controls.addWidget(QLabel("适宽缩放"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setMinimum(60)
        self.zoom.setMaximum(200)
        self.zoom.setValue(100)
        self.zoom.setFixedWidth(145)
        self.zoom.setToolTip("100% 为当前 PDF 预览栏的适宽比例；可放大查看细节")
        self.zoom.valueChanged.connect(lambda _: self._render_delayed())
        controls.addWidget(self.zoom)
        self.zoom_display = QLabel("100%")
        controls.addWidget(self.zoom_display)
        controls.addSpacing(12)
        self.results_toggle = self._button("查看结果", self.toggle_results)
        self.results_toggle.setToolTip("展开/收起差异预览和操作日志，为 PDF 页面留出更多空间")
        controls.addStretch()
        main_layout.addWidget(self.page_controls)
        self.page_controls.hide()

        horizontal = QSplitter(Qt.Horizontal)
        center = QSplitter(Qt.Vertical)
        self.center_splitter = center
        dual = QSplitter(Qt.Horizontal)
        self.preview_splitter = dual
        self.previews = {key: PagePreview(key) for key in ("A", "B")}
        for preview in self.previews.values():
            preview.clicked.connect(self._clicked_page)
        self.selection_editor = ContextSelectionEditor(self)
        dual.addWidget(self.previews["A"])
        dual.addWidget(self.selection_editor)
        dual.addWidget(self.previews["B"])
        dual.setChildrenCollapsible(False)
        dual.setSizes([530, 320, 530])
        dual.splitterMoved.connect(lambda *_: self._render_delayed())
        center.addWidget(dual)
        self.results = QTabWidget()
        self.diff_image = QLabel("对比完成后，将在这里显示差异图")
        self.diff_image.setAlignment(Qt.AlignCenter)
        self.diff_image.setStyleSheet("background:#f5f5f7;color:#86868b")
        self.diff_scroll = QScrollArea()
        self.diff_scroll.setWidget(self.diff_image)
        self.diff_scroll.setWidgetResizable(False)
        diff_panel = QWidget()
        diff_layout = QVBoxLayout(diff_panel)
        diff_nav = QHBoxLayout()
        diff_nav.addWidget(QLabel("红色区域：页面像素差异"))
        diff_nav.addStretch()
        diff_nav.addWidget(self._button("上一差异页", lambda: self.move_diff(-1)))
        self.diff_indicator = QLabel("0 / 0")
        diff_nav.addWidget(self.diff_indicator)
        diff_nav.addWidget(self._button("下一差异页", lambda: self.move_diff(1)))
        diff_layout.addLayout(diff_nav)
        diff_layout.addWidget(self.diff_scroll, 1)
        self.results.addTab(diff_panel, "视觉差异")
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.results.addTab(self.log_area, "操作与差异报告")
        center.addWidget(self.results)
        center.setStretchFactor(0, 4)
        center.setStretchFactor(1, 1)
        center.setSizes([710, 110])
        self.results.hide()  # Initially maximize A/B reading and editing space.
        horizontal.addWidget(center)

        inspector = QFrame()
        self.inspector = inspector
        inspector.setObjectName("Inspector")
        insp_layout = QVBoxLayout(inspector)
        insp_layout.setContentsMargins(12, 12, 12, 12)
        label = QLabel("高级详情")
        label.setObjectName("Heading")
        insp_layout.addWidget(label)
        self.right_tabs = QTabWidget()
        self.right_tabs.setObjectName("RightTabs")
        structure = QWidget()
        structure_layout = QVBoxLayout(structure)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["对象 / 资源", "类型"])
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        structure_layout.addWidget(self.tree, 2)
        self.selected_json = QPlainTextEdit()
        self.selected_json.setReadOnly(True)
        self.selected_json.setPlaceholderText("选择对象查看字典、引用和属性")
        structure_layout.addWidget(self.selected_json, 1)
        self.right_tabs.addTab(structure, "对象")
        properties = QWidget()
        props_layout = QVBoxLayout(properties)
        props_layout.addWidget(QLabel("编辑标准文档属性（不覆盖原 PDF）"))
        form = QFormLayout()
        self.property_fields = {}
        for key in ("Title", "Author", "Subject", "Keywords", "Creator"):
            line = QLineEdit()
            line.setPlaceholderText(f"/{key}")
            self.property_fields[key] = line
            form.addRow(key, line)
        props_layout.addLayout(form)
        props_layout.addWidget(self._button("另存修改后的 PDF", self.edit_metadata, primary=True))
        props_layout.addStretch()
        self.right_tabs.addTab(properties, "属性")
        signing = QWidget()
        signing_layout = QVBoxLayout(signing)
        self.signature_summary = QLabel("尚未核验 · 证书身份需单独建立信任")
        self.signature_summary.setWordWrap(True)
        signing_layout.addWidget(self.signature_summary)
        self.signature_list = QListWidget()
        self.signature_list.setMaximumHeight(155)
        self.signature_list.itemClicked.connect(self._selected_signature)
        signing_layout.addWidget(self.signature_list)
        self.signature_details = QPlainTextEdit()
        self.signature_details.setReadOnly(True)
        self.signature_details.setPlaceholderText("点击「验签」查看完整性、证书路径和修订覆盖")
        signing_layout.addWidget(self.signature_details, 1)
        signing_layout.addWidget(
            self._button("导出专业验签报告 JSON", self.export_signature_audit)
        )
        self.right_tabs.addTab(signing, "验签")
        self.signature_view = signing
        search_tab = QWidget()
        search_layout = QVBoxLayout(search_tab)
        search_layout.addWidget(QLabel("检索结果可点击定位到 PDF 页面。"))
        self.search_hits_list = QListWidget()
        self.search_hits_list.itemClicked.connect(self._select_search_hit)
        search_layout.addWidget(self.search_hits_list)
        self.right_tabs.addTab(search_tab, "搜索")
        revisions = QPlainTextEdit()
        revisions.setReadOnly(True)
        revisions.setPlaceholderText("点击上方「修订分析」查看 XRef 链、EOF 标记和签名 ByteRange")
        self.right_tabs.addTab(revisions, "修订")
        self.revision_details = revisions
        paint_report = QPlainTextEdit()
        paint_report.setReadOnly(True)
        paint_report.setPlaceholderText("点击「绘制分析」列出页面绘制指令及顺序、文字和图形操作")
        self.right_tabs.addTab(paint_report, "绘制")
        self.paint_details = paint_report
        insp_layout.addWidget(self.right_tabs)
        horizontal.addWidget(inspector)
        self.main_splitter = horizontal
        inspector.hide()
        horizontal.setSizes([1450, 0])
        main_layout.addWidget(horizontal, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("就绪 · 文件不会自动上传，编辑结果另存为新 PDF")
        self.status.setObjectName("Dim")
        bottom.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(145)
        self.progress.setRange(0, 0)
        self.progress.hide()
        bottom.addWidget(self.progress)
        main_layout.addLayout(bottom)
        outer.addWidget(main, 1)
        self._current_page = 0
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self.render_previews)
        self._refresh_workflow_context()

    def show_comparison(self) -> None:
        if self.docs["A"] is None:
            self.open_file("A")
            return
        if self.docs["B"] is None:
            self.open_file("B")
            if self.docs["B"] is None:
                return
        self.compare_files()

    def show_advanced_inspector(self, tab_index: int = 0) -> None:
        if not self.inspector.isVisible():
            self.inspector.show()
            self.main_splitter.setSizes([1000, 320])
        self.right_tabs.setCurrentIndex(tab_index)
        self._render_delayed()

    def _show_result_document(self) -> None:
        self._render_delayed()

    def _show_results(self) -> None:
        if not self.results.isVisible():
            self.results.show()
            self.center_splitter.setSizes([550, 190])
        self.results_toggle.setText("收起结果")

    def toggle_results(self) -> None:
        if self.results.isVisible():
            self.results.hide()
            self.results_toggle.setText("查看结果")
        else:
            self._show_results()

    def _on_workspace_changed(self, index: int) -> None:
        if hasattr(self, "studio_shell"):
            self.studio_shell.select_workspace(index)
            return
        if self._edit_mode != "none":
            self._set_edit_mode("none")
        self.selection_editor.setVisible(index == 0)
        self.preview_splitter.setSizes([530, 320, 530] if index == 0 else [530, 0, 530])
        self._render_delayed()
        self._refresh_workflow_context()

    def _refresh_workflow_context(self) -> None:
        if hasattr(self, "studio_shell"):
            self.studio_shell.refresh()
        if not any(self.docs.values()):
            self.workflow_context.setText("打开原稿开始。编辑会生成新 PDF，原文件不变。")
            return
        names = []
        for slot in ("A", "B"):
            path = self.docs.get(slot)
            if path is None:
                names.append(f"{slot} {'原稿' if slot == 'A' else '结果'}：未打开")
            else:
                filename = '修改预览.pdf' if path.name.startswith('selected-preview-') else path.name
                if len(filename) > 30:
                    filename = filename[:18] + '…' + filename[-9:]
                names.append(f"{slot} {'原稿' if slot == 'A' else '结果'}：{filename} · {self.pages_count[slot]} 页")
        active = self.active_slot.currentText()
        suffix = {
            "none": "选择上方功能，修改均会另存新文件",
            "replace": "已进入文字替换模式，点击原有文字 · Esc 退出",
            "reflow": "已进入原稿排版模式，点击原有段落 · Esc 退出",
            "add": "已进入正文添加模式，点击要插入的位置 · Esc 退出",
            "object": "已进入原图像/矢量对象编辑模式 · Esc 退出",
        }[self._edit_mode]
        self.workflow_context.setText(
            "   |   ".join(names) + f"   ·   {suffix}"
        )

    def _set_edit_mode(self, mode: str) -> None:
        if mode not in {"none", "replace", "reflow", "add", "object"}:
            raise ValueError("Unknown PDF editor interaction mode")
        self._edit_mode = mode
        self._placing_body_text = mode == "add"
        self._replacing_existing_text = mode == "replace"
        self._reflowing_paragraph = mode == "reflow"
        self._editing_original_object = mode == "object"
        labels = {
            "replace": "替换已有文字",
            "reflow": "段落重排",
            "add": "点击添加正文",
            "object": "直接编辑图片/图形",
        }
        for label, button in self._mode_buttons.items():
            button.setChecked(label == labels.get(mode))
        self._refresh_workflow_context()
        if mode == "none":
            self._notify("已退出编辑选择模式 · 原 PDF 保持不变")

    def _on_slot_change(self) -> None:
        if hasattr(self, "studio_shell"):
            self.studio_shell.signature_slot_changed()
        self._current_page = max(0, min(self._current_page, self.pages_count[self.active_slot.currentText()] - 1))
        self._update_indicator()
        self._load_properties()
        self._populate_structure()
        self._render_delayed()
        self._refresh_workflow_context()

    def _active(self) -> Path | None:
        return self.docs[self.active_slot.currentText()]

    def _notify(self, message: str) -> None:
        self.status.setText(message)
        self.log_area.appendPlainText(message)

    def _task(self, description: str, fn: Callable, callback: Callable[[Any], None],
              *, on_failed: Callable[[str], None] | None = None) -> None:
        if self._busy:
            self._notify("当前已有任务正在执行")
            return
        self._busy = True
        if hasattr(self, 'studio_shell'):
            self.studio_shell.refresh_signing_flow()
        self.progress.show()
        self.status.setText(description + " · 执行中…")
        app = QApplication.instance()
        if app is not None:
            # 先把“执行中”画出来，再把耗时读取交给后台。
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        worker = Task(fn)
        self._jobs.add(worker)

        def done(result: Any) -> None:
            self._jobs.discard(worker)
            self._busy = False
            self.progress.hide()
            try:
                callback(result)
            except Exception as exc:
                self._error(f"展示{description}结果失败：{type(exc).__name__}: {str(exc) or '该异常未提供说明'}")
            finally:
                if hasattr(self, 'studio_shell'):
                    self.studio_shell.refresh_signing_flow()

        def failed(error: str) -> None:
            self._jobs.discard(worker)
            self._busy = False
            self.progress.hide()
            if on_failed is not None:
                on_failed(error)
            else:
                self._error(description + "失败：" + error)
            if hasattr(self, 'studio_shell'):
                self.studio_shell.refresh_signing_flow()

        worker.signals.completed.connect(done)
        worker.signals.failed.connect(failed)
        self.pool.start(worker)

    def _error(self, error: str) -> None:
        self._notify(error)
        QMessageBox.warning(self, APP_NAME, error)

    def start_body_editor(self) -> None:
        if self._need_source() is None:
            return
        self._set_edit_mode("add")
        self._notify("正文添加：请在页面点击插入位置；新内容写入 PDF 内容流，不覆盖旧文字")

    def start_replace_text(self) -> None:
        if self._need_source() is None:
            return
        self._set_edit_mode("replace")
        self._notify("文字替换：请点击页面中已有的文字；仅修改真实文字操作数，不添加遮盖层")

    def start_paragraph_reflow(self) -> None:
        if self._need_source() is None:
            return
        self._set_edit_mode("reflow")
        self._notify("段落重排：请点击原有段落；将优先读取原稿字体、行距和版面位置")

    def start_object_editor(self) -> None:
        if self._need_source() is None:
            return
        self._set_edit_mode("object")
        self._notify("原对象编辑：请点击页面上的图片或矢量图形；不支持的对象会说明原因")

    def _clicked_page(self, slot: str, pixel_x: float, pixel_y: float) -> None:
        # 按用户要求：保存副本意味着本轮内容编辑已结束，再点击编辑页转到第三步。
        if self.workspace_tabs.currentIndex() == 0 and self.selection_editor._editing_finished:
            self._set_edit_mode('none')
            self.studio_shell.advance_to_signing()
            return
        source = self.docs.get(slot)
        if source is None:
            return
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(source))
        try:
            if self._current_page >= len(doc):
                return
            page = doc[self._current_page]
            try:
                width, height = page.get_size()
            finally:
                page.close()
        finally:
            doc.close()
        pixmap = self.previews[slot].image.pixmap()
        if pixmap is None or pixmap.width() <= 0 or pixmap.height() <= 0:
            return
        x = max(0.0, min(width, pixel_x * width / pixmap.width()))
        y = max(0.0, min(height, height - pixel_y * height / pixmap.height()))
        self._notify(f"{slot} · 第 {self._current_page + 1} 页坐标：{x:.1f}, {y:.1f} pt")
        if hasattr(self, 'studio_shell') and self.workspace_tabs.currentIndex() == 1:
            self.active_slot.setCurrentText(slot)
            self.studio_shell.signature_comparison.show_slot(slot)
            self.studio_shell.show_full_signature_evidence()
            return
        if self._editing_original_object:
            self._set_edit_mode("none")
            open_object_edit_dialog(
                self, slot, self._current_page + 1, round(x, 2), round(y, 2)
            )
            return
        if self._reflowing_paragraph:
            self._set_edit_mode("none")
            open_paragraph_dialog(
                self, slot, self._current_page + 1, round(x, 2), round(y, 2)
            )
            return
        if self._replacing_existing_text:
            self._set_edit_mode("none")
            self._open_replace_editor(slot, self._current_page + 1, round(x, 2), round(y, 2))
            return
        if (not self._placing_body_text and self.workspace_tabs.currentIndex() == 0
                and slot == "A"):
            if hasattr(self, "studio_shell"):
                self.show_advanced_inspector(self.studio_shell.editor_index)
            self.selection_editor.select_at(
                source, self._current_page + 1, round(x, 2), round(y, 2)
            )
            self._notify("已识别选中对象，请在右侧编辑区操作。")
            return
        if not self._placing_body_text:
            return
        self._set_edit_mode("none")
        self._open_body_editor(slot, self._current_page + 1, round(x, 2), round(y, 2))

    def _open_replace_editor(self, slot: str, page: int, x: float, y: float) -> None:
        open_replace_dialog(self, slot, page, x, y)

    def _open_body_editor(self, slot: str, page: int, x: float, y: float) -> None:
        source = self.docs[slot]
        if source is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("页面正文文字 · 新增文本块")
        form = QFormLayout(dialog)
        info = QLabel(
            f"{source.name} · 第 {page} 页 · 位置 ({x:.1f},{y:.1f}) pt\n"
            "新增真实页面文字内容，保留旧内容。"
        )
        info.setWordWrap(True)
        form.addRow(info)
        field = QTextEdit()
        field.setPlaceholderText("输入要写入页面的正文文字")
        field.setMinimumSize(440, 125)
        form.addRow("文本", field)
        size = QDoubleSpinBox()
        size.setRange(4, 96)
        size.setValue(14)
        size.setSuffix(" pt")
        form.addRow("文字大小", size)
        color = QLineEdit("#18385C")
        form.addRow("颜色 (#RRGGBB)", color)
        font_choice = QLineEdit()
        font_choice.setPlaceholderText("默认 Helvetica；中文等 Unicode 请选 TTF/OTF")
        choose_font = self._button("选择字体", lambda: self._choose_body_font(font_choice))
        font_row = QHBoxLayout()
        font_row.addWidget(font_choice)
        font_row.addWidget(choose_font)
        form.addRow("字体文件", font_row)

        preview_button = self._button("预览实际页面", lambda: self._preview_body_edit(
            source, page, x, y, field.toPlainText(), size.value(), color.text(),
            font_choice.text(), dialog
        ))
        controls = QHBoxLayout()
        controls.addWidget(preview_button)
        controls.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        controls.addWidget(buttons)
        form.addRow(controls)
        if dialog.exec() != QDialog.Accepted:
            return
        text = field.toPlainText()
        if not text.strip():
            self._error("请先输入需要添加的正文内容")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "将正文变化另存为新 PDF",
            str(source.with_name(source.stem + "-text-added.pdf")), "PDF (*.pdf)"
        )
        if not target:
            return
        font = Path(font_choice.text()) if font_choice.text().strip() else None

        def completed(report: dict) -> None:
            self._json_output(f"正文文本已写入新 PDF：{report['output']}", report)
            if slot == "B" and self.docs["A"] is None:
                self.load_path("A", source)
            self.load_path("B", Path(target))
        self._task("PDF 正文文字添加", lambda: add_body_text(
            source, Path(target), page=page, x=x, y=y, text=text,
            size=size.value(), color=color.text(), font_path=font,
        ), completed)

    def _choose_body_font(self, input_field: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "选择合法可使用的字体", "", "Fonts (*.ttf *.otf)")
        if selected:
            input_field.setText(selected)

    def _preview_body_edit(self, source: Path, page: int, x: float, y: float,
                           text: str, size: float, color: str,
                           font_text: str, parent: QDialog) -> None:
        import uuid
        temp = Path(self._scratch.name) / f"body-preview-{uuid.uuid4().hex[:10]}.pdf"
        try:
            add_body_text(source, temp, page=page, x=x, y=y, text=text,
                          size=size, color=color,
                          font_path=Path(font_text) if font_text.strip() else None)
            preview_image = render_page(temp, page - 1, 1.15)
            window = QDialog(parent)
            window.setWindowTitle("文字输出预览 · 按原 PDF 渲染")
            window.resize(760, 720)
            layout = QVBoxLayout(window)
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            rgb = preview_image.convert("RGB")
            bitmap = QImage(rgb.tobytes(), rgb.width, rgb.height,
                            rgb.width * 3, QImage.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(bitmap)
            label.setPixmap(pixmap)
            label.resize(pixmap.size())
            area = QScrollArea()
            area.setWidget(label)
            layout.addWidget(area)
            layout.addWidget(QLabel("此预览是临时 PDF 的实际渲染，尚未保存最终文件"))
            window.exec()
        except Exception as exc:
            QMessageBox.warning(parent, "正文预览不可用", str(exc))

    def search_document(self) -> None:
        source = self._need_source()
        if source is None:
            return
        phrase, ok = QInputDialog.getText(
            self, "定位 PDF 正文", "输入要搜索的文字（PDFium 字符映射，不含 OCR）："
        )
        if not ok or not phrase.strip():
            return
        slot = self.active_slot.currentText()

        def completed(report: dict) -> None:
            if self.docs[slot] != source:
                self._notify("已更换 PDF，未加载旧文件的搜索结果")
                return
            self._search_hits[slot] = report["matches"]
            self._search_slot = slot
            self.search_hits_list.clear()
            for index, match in enumerate(report["matches"]):
                from PySide6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(f"第 {match['page']} 页 · 字符 {match['char_start']} · {match['text'][:40]}")
                item.setData(Qt.UserRole, {"slot": slot, "index": index})
                self.search_hits_list.addItem(item)
            self.right_tabs.setCurrentWidget(self.search_hits_list.parentWidget())
            self.show_advanced_inspector(
                self.right_tabs.indexOf(self.search_hits_list.parentWidget())
            )
            if report["matches"]:
                self._select_search_hit(self.search_hits_list.item(0))
            else:
                self._render_delayed()
            self._notify(f"找到 {report['count']} 处「{phrase}」；单击条目可跳转并高亮")
        self._task("搜索 PDF 正文", lambda: find_text(source, phrase), completed)

    def _select_search_hit(self, item) -> None:
        metadata = item.data(Qt.UserRole)
        if not metadata:
            return
        slot = metadata["slot"]
        hits = self._search_hits.get(slot, [])
        index = metadata["index"]
        if index >= len(hits):
            return
        self.active_slot.setCurrentText(slot)
        hit = hits[index]
        self._current_page = hit["page"] - 1
        self._update_indicator()
        self.render_previews()
        if hit["boxes_pdf"]:
            left, bottom, right, top = hit["boxes_pdf"][0]
            zoom_scale = self._render_scales.get(slot, 1.0)
            height = hit["page_size_pdf"][1]
            self.previews[slot].scroll.ensureVisible(
                int((left + right) / 2 * zoom_scale),
                int((height - (top + bottom) / 2) * zoom_scale),
                70, 70,
            )

    def _selected_signature(self, item) -> None:
        if self._signature_audit is None:
            return
        number = item.data(Qt.UserRole)
        signatures = self._signature_audit.get("signatures", [])
        if isinstance(number, int) and 0 <= number < len(signatures):
            self.signature_details.setPlainText(_json(signatures[number], 130_000))
            if hasattr(self, "studio_shell"):
                self.studio_shell.update_signature(signatures[number])

    def export_signature_audit(self) -> None:
        if self._signature_audit is None:
            self._notify("请先运行专业验签")
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "导出验签报告（可能含证书主体资料）",
            "pdf-signature-audit.html", "HTML 报告 (*.html);;JSON 数据 (*.json)"
        )
        if output:
            path = Path(output)
            if path.exists():
                self._error("报告文件已存在，请使用未占用的文件名")
                return
            if path.suffix.lower() in (".html", ".htm"):
                save_signature_html(self._signature_audit, path)
            else:
                path.write_text(
                    json.dumps(self._signature_audit, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            self._notify(f"验签报告已导出：{path}")

    def _need_source(self) -> Path | None:
        selected = self._active()
        if selected is None:
            QMessageBox.information(self, "请选择 PDF", "先点击「打开 A」或「打开 B」，再选择工作对象。")
        return selected

    def open_file(self, slot: str) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, f"打开 PDF {slot}", "", "PDF 文件 (*.pdf)")
        if filename:
            self.load_path(slot, Path(filename))

    def load_path(self, slot: str, path: Path) -> None:
        import pypdfium2 as pdfium
        source = path.resolve(strict=True)
        doc = pdfium.PdfDocument(str(source))
        try:
            count = len(doc)
        finally:
            doc.close()
        # Surface a cautious pre-edit safety state. A signature's actual
        # cryptographic trust is assessed only by the separate Verify action.
        try:
            with pikepdf.Pdf.open(source) as structure:
                if structure.is_encrypted:
                    protection = "加密文档（编辑受限）"
                elif _signed_or_certified(structure):  # 限制：打开时检测到已签名或已认证
                    protection = "检测到签名（可编辑另存）"  # 限制：打开已签名 PDF 时只标记状态，编辑必须另存
                else:
                    protection = "未发现签名对象"
        except (pikepdf.PdfError, ValueError, OSError):
            protection = "保护状态待进一步核查"
        self.docs[slot] = source
        if hasattr(self, "studio_shell"):
            # 先绑定实际新文件，再清除此侧报告和显示，不能让刷新继续指向旧 B。
            self.studio_shell.invalidate_signature(slot)
        self.document_protection[slot] = protection
        if slot == "A":
            self.selection_editor.clear()
            self.previews["A"].selection_box_pdf = None
        self.page_controls.show()
        if slot == "B":
            self._show_result_document()
        self._set_edit_mode("none")
        self.pages_count[slot] = count
        self.manifests.pop(slot, None)
        self._search_hits.pop(slot, None)
        self.report = None
        display_name = '修改预览.pdf' if source.name.startswith('selected-preview-') else source.name
        if len(display_name) > 34:
            display_name = display_name[:20] + '…' + display_name[-10:]
        self.previews[slot].filename.setText(f"{display_name}   ·   {count} 页")
        self.previews[slot].filename.setToolTip(str(source))
        self._current_page = 0
        self.active_slot.setCurrentText(slot)
        self._on_slot_change()
        self._notify(f"已打开 {slot}：{source.name}（{count} 页）")

    def _render_delayed(self) -> None:
        self.zoom_display.setText(f"{self.zoom.value()}%")
        self._render_timer.start(160)

    def _preview_page_width(self, source, page_index: int) -> float:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(source))
        try:
            page = document[page_index]
            try:
                page_width, _ = page.get_size()
            finally:
                page.close()
        finally:
            document.close()
        return float(page_width)

    def render_previews(self) -> None:
        import pypdfium2 as pdfium
        pending = []
        for slot, source in self.docs.items():
            if source is None:
                continue
            if not Path(source).is_file():
                self.previews[slot].clear(
                    f"{slot} 这一侧的 PDF 已经不在原位置，页面无法显示。\n{source}")
                continue
            if self._current_page >= self.pages_count[slot]:
                self.previews[slot].clear("该文件没有这一页")
                continue
            try:
                page_width = self._preview_page_width(source, self._current_page)
            except Exception as exc:
                self.previews[slot].clear(f"{slot} 的页面没有画出来。\n{exc}")
                continue
            # 先记下每栏能放下的倍率，后面取更小的那个，左右画成一样大。
            viewport_width = self.previews[slot].scroll.viewport().width()
            available = max(180, viewport_width - 18)
            fit_width_scale = min(1.35, available / page_width) if page_width else 1.0
            pending.append((slot, source, fit_width_scale))
        if not pending:
            return
        shared_fit = min(item[2] for item in pending)
        scale = min(4.0, max(0.25, shared_fit * self.zoom.value() / 100))
        for slot, source, _fit in pending:
            try:
                self._render_scales[slot] = scale
                if scale < 0.5:
                    # The standalone analysis renderer has a 0.5 minimum.
                    # The interactive dual viewer must fit narrow screens too.
                    document = pdfium.PdfDocument(str(source))
                    try:
                        page = document[self._current_page]
                        try:
                            image = page.render(scale=scale).to_pil().convert("RGB")
                        finally:
                            page.close()
                    finally:
                        document.close()
                else:
                    image = render_page(source, self._current_page, scale)
                rows = [hit for hit in self._search_hits.get(slot, [])
                        if hit["page"] == self._current_page + 1]
                highlight = [box for hit in rows for box in hit["boxes_pdf"]]
                height = rows[0]["page_size_pdf"][1] if rows else 0
                self.previews[slot].show_image(image, boxes=highlight,
                                               pdf_page_height=height, scale=scale)
            except Exception as exc:
                self.previews[slot].clear(f"{slot} 的页面没有画出来。\n{exc}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "studio_shell"):
            self.studio_shell.resize()
        # Refit the A/B page widths after Windows DPI or window-size changes.
        if hasattr(self, "_render_timer") and any(self.docs.values()):
            self._render_delayed()

    def _update_indicator(self) -> None:
        count = self.pages_count[self.active_slot.currentText()]
        self.page_indicator.setText(f"{self._current_page + 1} / {count or 0}")

    def move_page(self, step: int) -> None:
        count = self.pages_count[self.active_slot.currentText()]
        if count:
            self._current_page = max(0, min(count - 1, self._current_page + step))
            self._update_indicator()
            self._render_delayed()

    def _load_properties(self) -> None:
        path = self._active()
        try:
            self.properties = document_properties(path) if path else {}
        except Exception as exc:
            self.properties = {}
            self._notify(f"文档属性读取失败：{exc}")
        for key, widget in self.property_fields.items():
            widget.setText(self.properties.get(key, ""))

    def inspect_active(self) -> None:
        path = self._need_source()
        if not path:
            return
        slot = self.active_slot.currentText()
        if slot in self.manifests:
            self._populate_structure()
            self._notify(f"已加载 {slot} 对象树")
            return
        dest = Path(self._scratch.name) / f"inspect_{slot}"
        self._task("解析 PDF 对象",
                   lambda: inspect_pdf(path, dest, operators=True, text_geometry=True),
                   lambda result: self._inspected(slot, path, result))

    def _inspected(self, slot: str, path: Path, result: dict) -> None:
        if self.docs[slot] != path:
            self._notify("源文件已更换，忽略过期解析结果")
            return
        self.manifests[slot] = result
        self._populate_structure()
        self.show_advanced_inspector(0)
        self._notify(f"{slot} 结构解析完成：{result['page_count']} 页 / {result['object_count']} 对象")

    def _populate_structure(self) -> None:
        self.tree.clear()
        slot = self.active_slot.currentText()
        result = self.manifests.get(slot)
        if not result:
            self.selected_json.setPlainText("点击「分析结构」生成对象树")
            return
        groups = (
            ("Trailer", [("Trailer", result["trailer"])]),
            ("Pages", [(f"Page {p['number']} · {p.get('reference')}", p) for p in result["pages"]]),
            ("Objects", [(o.get("reference") or "Direct", o) for o in result["objects"]]),
            ("Signatures", [(f"Signature {i+1}", sig) for i, sig in enumerate(result["signatures"])]),
        )
        for name, elements in groups:
            group = QTreeWidgetItem([f"{name} ({len(elements)})", "group"])
            self.tree.addTopLevelItem(group)
            for title, value in elements[:6000]:
                typename = value.get("type", "") if isinstance(value, dict) else ""
                item = QTreeWidgetItem([str(title), str(typename)])
                item.setData(0, Qt.UserRole, value)
                group.addChild(item)
        self.tree.expandToDepth(0)

    def _tree_selected(self) -> None:
        selected = self.tree.selectedItems()
        if selected:
            value = selected[0].data(0, Qt.UserRole)
            if value is not None:
                self.selected_json.setPlainText(_json(value, max_chars=50_000))

    def edit_metadata(self) -> None:
        path = self._need_source()
        if not path:
            return
        changes = {key: line.text() for key, line in self.property_fields.items()
                   if line.text() != self.properties.get(key, "")}
        if not changes:
            self._notify("文档属性未发生变化")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "文档属性另存为", str(path.with_name(path.stem + "-edited.pdf")), "PDF (*.pdf)")
        if filename:
            self._task("保存文档属性", lambda: edit_properties(path, Path(filename), changes),
                       lambda result: self._edited(result))

    def _edited(self, result: dict) -> None:
        self._notify(f"文档属性修改已保存：{result['output_filename']}；未自动生成修改记录文件")

    def compare_files(self) -> None:
        a, b = self.docs["A"], self.docs["B"]
        if not a or not b:
            QMessageBox.information(self, "文件对比", "请先加载 A 和 B 两份 PDF")
            return
        folder = Path(self._scratch.name)
        def process() -> tuple[dict, dict]:
            ma = self.manifests.get("A") or inspect_pdf(a, folder / "inspect_A")
            mb = self.manifests.get("B") or inspect_pdf(b, folder / "inspect_B")
            return {"A": ma, "B": mb}, {
                "structure": compare(ma, mb),
                "visual": visual_diff(a, b, folder / "visual"),
            }
        self._task("对比 PDF", process, lambda result: self._compared(a, b, result))

    def _compared(self, a: Path, b: Path, results: tuple[dict, dict]) -> None:
        if self.docs["A"] != a or self.docs["B"] != b:
            self._notify("文件已变化，未应用旧对比")
            return
        self.manifests.update(results[0])
        self._populate_structure()
        self.report = results[1]
        self._diff_files = [Path(self._scratch.name) / "visual" / p["file"]
                            for p in self.report["visual"]["pages"]]
        self._diff_page = 0
        self.show_diff()
        self.log_area.setPlainText(_json(self.report, 30_000))
        self._show_results()
        self.results.setCurrentIndex(0)
        affected = sum(bool(p["changed_pixels"]) for p in self.report["visual"]["pages"])
        self._notify(f"对比完成：{affected} 页发现像素差异，详情在下方「视觉差异」")

    def move_diff(self, step: int) -> None:
        if self._diff_files:
            self._diff_page = max(0, min(len(self._diff_files) - 1, self._diff_page + step))
            self.show_diff()

    def show_diff(self) -> None:
        if not self._diff_files:
            return
        from PIL import Image
        with Image.open(self._diff_files[self._diff_page]) as image:
            rgb = image.convert("RGB")
            raw = rgb.tobytes()
            bitmap = QImage(raw, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()
        px = QPixmap.fromImage(bitmap)
        self.diff_image.setPixmap(px)
        self.diff_image.resize(px.size())
        self.diff_indicator.setText(f"{self._diff_page + 1} / {len(self._diff_files)}")

    def _json_output(self, label: str, data: dict) -> None:
        self.log_area.setPlainText(_json(data, 50_000))
        self._show_results()
        self.results.setCurrentIndex(1)
        self._notify(label)

    def deep_analysis(self) -> None:
        path = self._need_source()
        if path:
            self._task("深度解析", lambda: semantic_inventory(path, max_pages=200),
                       lambda result: self._json_output(
                           f"深度解析完成：{len(result['pages'])} 页", result))

    def analyze_revisions(self) -> None:
        source = self._need_source()
        if not source:
            return

        def completed(result: dict) -> None:
            if source != self._active():
                self._notify("文件已切换，修订报告已生成但未覆盖当前面板")
                return
            self.revision_report = result
            self.revision_details.setPlainText(_json(result, 200_000))
            self.show_advanced_inspector(self.right_tabs.indexOf(self.revision_details))
            self._notify(f"修订分析完成：{result['chain_status']}，"
                         f"{len(result['byte_ranges'])} 个 ByteRange 对象")
        self._task("PDF 修订与签名范围", lambda: inspect_revisions(source), completed)

    def analyze_paint(self) -> None:
        source = self._need_source()
        if not source:
            return
        def completed(result: dict) -> None:
            if source != self._active():
                self._notify("文档已切换，绘制分析完成但未覆盖当前查看器")
                return
            self.paint_details.setPlainText(_json(result, 160_000))
            self.show_advanced_inspector(self.right_tabs.indexOf(self.paint_details))
            operations = sum(sum(p["operator_counts"].values()) for p in result["pages"])
            self._notify(f"页面绘制指令分析完成：{len(result['pages'])} 页、{operations} 条操作")
        self._task("页面绘制指令", lambda: inspect_paint_operations(source), completed)

    def export_bundle(self) -> None:
        path = self._need_source()
        if not path:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择解析结果保存目录")
        if not folder:
            return
        base = Path(folder) / f"{path.stem}-qpdf-analysis"
        dest = base
        i = 2
        while dest.exists():
            dest = Path(f"{base}-{i}")
            i += 1
        self._task("完整解析导出", lambda: analyze_complete(path, dest),
                   lambda result: self._json_output(f"解析包已生成：{dest}", result))

    def verify_active(self) -> None:
        path = self._need_source()
        if not path:
            return
        roots = []
        allow_network = False
        strict = False
        if QMessageBox.question(self, "证书信任", "是否导入已从独立来源核实的可信根证书？\n"  # 限制：可信根必须由用户从独立来源导入
                               "不导入将显示身份信任尚未建立。") == QMessageBox.Yes:  # 限制：不导入时显示身份信任尚未建立
            name, _ = QFileDialog.getOpenFileName(self, "可信证书", "", "Certificate (*.pem *.cer *.crt)")
            if name:
                roots = [Path(name)]
                mode, ok = QInputDialog.getItem(
                    self, "吊销检查与网络隐私",
                    "请选择证书吊销信息的检查方式。联网会向证书服务提供方发出请求：",
                    ["离线验证（默认）", "允许联网 / 软失败", "允许联网 / 严格吊销检查"],
                    0, False,
                )
                if not ok:
                    return
                allow_network = mode != "离线验证（默认）"
                strict = mode == "允许联网 / 严格吊销检查"

        audit_slot = self.active_slot.currentText()
        audit_identity = self.studio_shell._signature_identity(audit_slot) if hasattr(self, "studio_shell") else None
        def completed(result: dict) -> None:
            if hasattr(self, "studio_shell"):
                if self.studio_shell._signature_identity(audit_slot) != audit_identity:
                    self._notify("验签已结束，但文件已变化；旧结果未覆盖新文档")
                    return
                self.studio_shell._signature_cache[audit_slot] = (audit_identity, result, None)
                self.studio_shell._refresh_signature_sources()
            if self.active_slot.currentText() != audit_slot or self._active() != path:
                # 即使 A/B 打开同一路径，也按任务开始时的栏位归属保存结果。
                self._notify("验签结果已保留在原文件栏，未覆盖当前文档")
                return
            self._signature_audit = result
            self.signature_list.clear()
            statuses = {
                "signature_valid_and_certificate_path_trusted": "签名有效 · 本地信任路径成立",
                "cryptographically_valid_identity_unverified": "签名完整 · 身份未建立信任",  # 限制：数学有效不等于身份已验证
                "cryptographically_valid_but_certificate_not_trusted": "签名完整 · 证书未通过信任检查",  # 限制：证书未通过可信根时，不显示为身份已验证
                "cryptographic_integrity_or_signature_failed": "签名完整性或数学验证失败",
                "inconclusive_validation_error": "验证结果不确定",
            }
            for index, item in enumerate(result["signatures"]):
                from PySide6.QtWidgets import QListWidgetItem
                title = statuses.get(item.get("overall_technical_result"), "验证不确定")
                label = QListWidgetItem(f"#{index + 1}  {title}  ·  {item.get('field', '')}")
                label.setData(Qt.UserRole, index)
                self.signature_list.addItem(label)
            self.signature_summary.setText(
                f"{path.name}  ·  {result['count']} 个数字签名\n"
                f"信任根：{result['trust_roots_supplied']} 个；"
                f"{'允许联网' if result['network_fetching'] else '仅离线'}；"
                "签名完整不等于签署方身份真实性"  # 限制：签名完整不等于签署方身份真实
            )
            self.signature_details.setPlainText(
                _json(result["signatures"][0], 100_000) if result["signatures"]
                else "验证未完成：" + result["validation_error"] if result.get("validation_error") else "没有发现数字签名。结构层也不应将普通图章图片认定为签名。"  # 限制：普通图章图片不能当成数字签名
            )
            if hasattr(self, "studio_shell"):
                self.studio_shell.update_signature()
            if hasattr(self, 'studio_shell'):
                self.studio_shell.show_signature_evidence(audit_slot)
            else:
                self.right_tabs.setCurrentWidget(self.signature_view)
                self.show_advanced_inspector(self.right_tabs.indexOf(self.signature_view))
            self._notify(f"专业验签完成：{result['count']} 个签名；详细结论见签名面板")

        from .signature_evidence import read_signature_audit
        self._task("专业数字签名核验",
                   lambda: read_signature_audit(path, roots, allow_network=allow_network,
                                               require_revocation=strict),
                   completed)

    def sign_active(self) -> None:
        path = self.studio_shell.choose_signing_source() if hasattr(self, "studio_shell") else self._need_source()
        if not path:
            return
        shell = getattr(self, 'studio_shell', None)
        context = shell._signing_context_identity() if shell else None
        prepared = shell.selected_signing_credential() if shell else None
        cert = str(prepared) if prepared else ''
        if not cert:
            cert, _ = QFileDialog.getOpenFileName(self, "选择自己的证书，为 B 生成数字签名", "", "Certificate (*.pfx *.p12)")
        if not cert:
            return
        default_output = self.studio_shell.default_signed_output(path) if hasattr(self, "studio_shell") else path.with_name(path.stem + "-signed.pdf")
        selected = shell.selected_signing_output() if shell else None
        dest = str(selected) if selected else ''
        if not dest:
            dest, _ = QFileDialog.getSaveFileName(self, "签署后另存为独立文件（不覆盖 A / B）", str(default_output), "PDF (*.pdf)")
        if not dest:
            return
        target = Path(dest)
        from .workflow_policy import signing_output_block_reason
        reason = signing_output_block_reason(target, self.docs.values())
        if reason:
            self._error(reason)
            return
        from .credential_passwords import load_managed_password
        try:
            managed_password = load_managed_password(Path(cert))
        except (OSError, ValueError, RuntimeError) as exc:
            self._error(f'无法读取本机保护的凭据密码：{exc}')
            return
        password, ok = (None, True) if managed_password is not None else QInputDialog.getText(
            self, "证书密码", "输入您的 P12/PFX 密码", QLineEdit.Password)
        # Read again in the worker; do not retain an automatic secret in a UI callback.
        managed = managed_password is not None
        managed_password = None
        if ok:
            if shell and (shell._signing_context_identity() != context or not shell.b_signing_ready()):
                self._notify('文档状态已变化，请重新确认 B 和签署选项。')
                return
            original = self.docs.get("A")
            def completed(signed_path: Path) -> None:
                if self.docs.get("A") != original or self.docs.get("B") != path or (shell and shell._signing_context_identity() != context):
                    self._notify(f"签署文件已保存：{signed_path}；当前文档已变化，未替换当前预览。")
                    return
                self.load_path("B", signed_path)
                self.active_slot.setCurrentText("B")
                if hasattr(self, "studio_shell"):
                    # 载入新 B 会清掉旧文件的选择状态；将实际使用的凭据绑定到新 B 供继续操作。
                    self.studio_shell._select_signing_value('credential', Path(cert))
                    self.studio_shell.signed_output_completed(path, signed_path)
                self._notify(
                    f"已签署并在 B 预览中打开：{signed_path.name}。"
                    "点击「验签」检查签名数学完整性和证书信任状态。"
                )
            def sign_task():
                secret = load_managed_password(Path(cert)) if managed else password
                if secret is None:
                    raise ValueError('本机密码记录已变化，请重新选择凭据。')
                try:
                    return sign_document(path, Path(dest), pfx=Path(cert), password=secret.encode('utf-8'))
                finally:
                    secret = None
            self._task("数字签名", sign_task, completed)

    def generate_key_pair(self) -> None:
        open_personal_certificate_editor(self, reissue=False)

    def reissue_signer_and_output(self) -> None:
        if hasattr(self, "studio_shell") and self.studio_shell.choose_signing_source() is None:
            return
        open_signer_output_dialog(self)

    def generate_compatible_personal_keys(self) -> None:
        original = self.docs.get("A")
        if original is None:
            self._notify("请先在 A 打开带数字签名的原始 PDF")
            return

        def completed(result: dict) -> None:
            if self.docs.get("A") != original:
                self._notify("原件已更换，不再沿用旧文件的算法规格")
                return
            items = result["signatures"]
            if not items:
                self._error("原始 PDF 没有可提取的数字签名，无法匹配算法参数")
                return
            algorithm = items[0].get("compatible_personal_key_algorithm")
            if algorithm is None:
                self._error(
                    "原签名的算法不受支持或验证未通过；不会猜测原签署人的密钥类型"
                )
                return
            open_personal_certificate_editor(
                self, reissue=False, preferred_algorithm=algorithm,
            )
        self._task(
            "读取原件签名的算法规格",
            lambda: inspect_signature_profile(original), completed,
        )

    def compare_signature_identities(self) -> None:
        show_signature_comparison(self)

    def reissue_certificate(self) -> None:
        open_personal_certificate_editor(self, reissue=True)

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            QMessageBox.information(self, "后台任务", "当前正在处理 PDF，请完成后关闭窗口。")
            return
        self.pool.waitForDone(1000)
        self._scratch.cleanup()
        event.accept()


def launch() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    icon = Path(__file__).resolve().parents[2] / "assets" / "qpdf-studio.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    from .qt_workbench_theme import WORKBENCH_STYLES
    app.setStyleSheet(STYLESHEET + WORKBENCH_STYLES)
    window = PdfStudioQt()
    window.show()
    app.exec()
