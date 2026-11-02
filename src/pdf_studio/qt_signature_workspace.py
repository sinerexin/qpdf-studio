"""Aligned A/B information rows; verification stays in the right workspace."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .qt_signature_cards import SignatureInformationPane, select_signature_field
from .signature_card_data import SECTIONS
from .signature_templates import build_reference_template


class SignatureIdentityWorkspace(QWidget):
    signature_selected = Signal(str, str)

    def __init__(self, source_paths):
        super().__init__()
        self.setObjectName('SignatureInformationWorkspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel('签名与证书 · A / B 信息逐行对照，右侧同时显示双方验证结果')
        hint.setObjectName('WorkflowHint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(0, 10)
        grid.setColumnStretch(1, 12)
        self.panes = {slot: SignatureInformationPane(slot, source_paths) for slot in ('A', 'B')}
        self._source_paths = source_paths
        self._template_token = None
        for column, (slot, pane) in enumerate(self.panes.items()):
            grid.addWidget(pane, 0, column, Qt.AlignTop)
            for row, (section, _title) in enumerate(SECTIONS, 1):
                grid.addWidget(pane.rows[section], row, column)
            grid.addWidget(pane.operation_note, 5, column)
            grid.addWidget(pane.footer, 6, column)
            pane.signatures.currentIndexChanged.connect(lambda _index, s=slot: self._signature_changed(s))
        for row in range(1, 5):
            grid.setRowStretch(row, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _signature_changed(self, slot):
        if slot == 'A':
            self._refresh_b_template()
        field = self.panes[slot]._selected().get('field')
        if field is not None:
            self.signature_selected.emit(slot, str(field))

    def select_signature_field(self, slot, field):
        select_signature_field(self.panes[slot], field)

    def update_slot(self, slot, path, cached, identity, preferred_field=None):
        self.panes[slot].show_report(path, cached, identity, preferred_field)
        self._refresh_b_template()

    def _refresh_b_template(self):
        # 切换 A 的签名或导入不同文件时才换模板，普通界面刷新不重新生成标识。
        a, b = self.panes['A'], self.panes['B']
        paths = self._source_paths()
        entry = a._selected()
        token = (str(paths.get('A')), str(paths.get('B')),
                 a._identity[1] if a._identity else None,
                 b._identity[1] if b._identity else None, entry.get('field'))
        if token == self._template_token:
            return
        try:
            template = build_reference_template(entry, a._audit()) if paths.get('B') and entry else None
        except (ValueError, TypeError) as exc:
            # 模板格式异常只影响 B 的参考预览，不把 A 的真实数据误报成读取失败。
            b.set_reference_template(None)
            b.operation_note.setText(f'B 名称模板暂未建立：{type(exc).__name__}: {exc}')
            return
        self._template_token = token
        b.set_reference_template(template)
