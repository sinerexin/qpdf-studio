"""Bounded, copyable field browser with explicit raw JSON fallback. No file IO."""
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

MAX_TREE_NODES = 1800
MAX_TEXT = 300000
_MISSING = object()


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)


class EvidenceSection(QWidget):
    def __init__(self):
        super().__init__()
        self.payload = None
        self._values = {}
        self._nodes = 0
        self._limited = False
        self._raw_ready = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(0)
        self.search.setPlaceholderText('筛选当前分类字段…')
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        self.mode = QComboBox()
        self.mode.addItems(['字段列表', 'JSON 表示'])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode)
        layout.addLayout(row)
        self.stack = QStackedWidget()
        self.splitter = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['字段', '值 / 摘要'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 130)
        self.tree.currentItemChanged.connect(self._select)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('选择字段，可查看完整值或复制公钥 / 证书 PEM。')
        self.detail.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.detail.setMinimumHeight(70)
        self.detail.hide()
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.detail)
        self.splitter.setSizes([340, 110])
        self.stack.addWidget(self.splitter)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.stack.addWidget(self.raw)
        layout.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        self.limit_note = QLabel()
        self.limit_note.setWordWrap(True)
        self.limit_note.setObjectName('WorkflowHint')
        footer.addWidget(self.limit_note, 1)
        self.copy = QPushButton('复制选中字段')
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self.copy_selected)
        footer.addWidget(self.copy)
        layout.addLayout(footer)

    def set_payload(self, payload):
        self.payload = payload
        self._nodes = 0
        self._limited = False
        self._raw_ready = False
        self.tree.clear()
        self._values.clear()
        self.detail.clear()
        self.detail.hide()
        self.raw.clear()
        self.copy.setEnabled(False)
        self.limit_note.clear()
        items = payload.items() if isinstance(payload, dict) else [('值', payload)]
        for key, value in items:
            if not self._add(self.tree.invisibleRootItem(), str(key), value):
                break
        self.tree.expandToDepth(0)
        if self._limited:
            self.limit_note.setText('字段树已达展开上限；未展开内容请看 JSON 或主动导出。')  # 限制：证据字段树达到展开上限后不再展开
        self._filter()
        self._mode_changed(self.mode.currentIndex())

    def clear(self):
        self.payload = None
        self.tree.clear()
        self._values.clear()
        self.detail.clear()
        self.detail.hide()
        self.raw.clear()
        self.copy.setEnabled(False)
        self._raw_ready = False
        self.limit_note.clear()

    def _add(self, parent, key, value, depth=0):
        if self._nodes >= MAX_TREE_NODES:
            self._limited = True
            QTreeWidgetItem(parent, ['未展开', '字段数量达到上限；请看 JSON / 导出'])
            return False
        self._nodes += 1
        container = isinstance(value, (dict, list, tuple))
        summary = (f'{len(value)} 个字段' if isinstance(value, dict) else f'{len(value)} 项') if container else (
            '未提供 / 不适用' if value is None else str(value))
        summary = summary.replace('\r', ' ').replace('\n', ' ')
        item = QTreeWidgetItem(parent, [key, summary[:130] + ('…（选中查看）' if len(summary) > 130 else '')])
        # Qt QVariant converts dict/list contents recursively and cannot hold
        # arbitrary-size ASN.1 integers. Keep original values in Python; only
        # this small node ID crosses the Qt boundary. No values are truncated.
        node_id = self._nodes
        self._values[node_id] = value
        item.setData(0, Qt.UserRole, node_id)
        item.setToolTip(0, key)
        if container:
            if depth >= 14:
                self._limited = True
                QTreeWidgetItem(item, ['未展开', '层数上限；选中父字段查看 JSON'])
            else:
                children = value.items() if isinstance(value, dict) else enumerate(value)
                for name, child in children:
                    if not self._add(item, str(name), child, depth + 1):
                        break
        return True

    def _filter(self, *_):
        query = self.search.text().strip().casefold()
        def visit(item):
            own = query in (item.text(0) + ' ' + item.text(1)).casefold()
            child_match = False
            for index in range(item.childCount()):
                child_match = visit(item.child(index)) or child_match
            visible = not query or own or child_match
            item.setHidden(not visible)
            if query and child_match:
                item.setExpanded(True)
            return visible
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            visit(root.child(index))

    def _item_value(self, item):
        if item is None:
            return _MISSING
        return self._values.get(item.data(0, Qt.UserRole), _MISSING)

    def _select(self, item, _previous=None):
        value = self._item_value(item)
        self.copy.setEnabled(value is not _MISSING)
        if value is _MISSING:
            self.detail.clear()
            self.detail.hide()
            return
        self.detail.show()
        text = _text(value)
        self.detail.setPlainText(text[:MAX_TEXT] + ('\n【界面仅预览前 300000 字符；完整数据请主动导出。】' if len(text) > MAX_TEXT else ''))

    def _mode_changed(self, index):
        self.stack.setCurrentIndex(index)
        self.search.setEnabled(index == 0)
        if index == 1 and not self._raw_ready:
            text = json.dumps(self.payload, ensure_ascii=False, indent=2, default=str)
            self.raw.setPlainText(text[:MAX_TEXT] + ('\n【JSON 预览超长已截断；完整数据请主动导出。】' if len(text) > MAX_TEXT else ''))
            self._raw_ready = True
        self.copy.setEnabled(index == 0 and self._item_value(self.tree.currentItem()) is not _MISSING)

    def copy_selected(self):
        item = self.tree.currentItem()
        value = self._item_value(item)
        if value is _MISSING or self.mode.currentIndex() != 0:
            return
        text = _text(value)
        if len(text) > 2 * 1024 * 1024:
            self.limit_note.setText('字段超过 2 MiB，未复制；请使用主动导出。')  # 限制：超过 2 MiB 的字段不复制到剪贴板
            return
        QGuiApplication.clipboard().setText(text)
        self.limit_note.setText('已复制所选字段；未写入文件。')
