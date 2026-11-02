"""Native information cards and simultaneous A/B verification; no PDF writes."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .certificate_names import NAME_FIELDS, display_name_fields, format_name
from .signature_templates import template_section
from .qt_evidence_section import EvidenceSection
from .qt_signature_evidence import SignatureEvidencePane, TABS
from .signature_card_data import SECTIONS, display_value, revocation_description, summary_rows
from .signature_presentation import STATUS_TITLES


class SummaryValue(QLabel):
    """Keep long fingerprints and subjects from expanding the application width."""
    def __init__(self):
        super().__init__()
        self.full_value = ''
        self._is_name = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def set_value(self, value, *, is_name=False):
        # A/B 使用同一个控件：名称完整分行显示；原值仍可选择并在提示中查看。
        self.full_value = display_value(value)
        self._is_name = is_name
        self.setWordWrap(is_name)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setToolTip(self.full_value)
        self._name_text = format_name(value) if is_name else self.full_value
        self._elide()
        self.updateGeometry()

    def _elide(self):
        # 只对普通摘要保留省略行为；姓名、机构和签发者不再被截成一行省略号。
        self.setText(self._name_text if self._is_name else
                     self.fontMetrics().elidedText(self.full_value, Qt.ElideRight, max(1, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class InformationCard(QFrame):
    def __init__(self, title, callback):
        super().__init__()
        self.setObjectName('SignatureInformationCard')
        self.setMinimumWidth(0)
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName('Heading')
        body.addWidget(heading)
        self.fields = QGridLayout()
        self.fields.setHorizontalSpacing(8)
        self.fields.setVerticalSpacing(3)
        self.fields.setColumnStretch(1, 1)
        self.names, self.values = [], []
        for row in range(4):
            name, value = QLabel(), SummaryValue()
            name.setObjectName('CertificateName')
            self.names.append(name)
            self.values.append(value)
            self.fields.addWidget(name, row, 0)
            self.fields.addWidget(value, row, 1)
        body.addLayout(self.fields)
        self.note = QLabel()
        self.note.setObjectName('WorkflowHint')
        self.note.setWordWrap(True)
        self.note.hide()
        body.addWidget(self.note)
        self.details = QPushButton('查看完整信息 →')
        self.details.setObjectName('SignatureDetailLink')
        self.details.clicked.connect(callback)
        body.addWidget(self.details, 0, Qt.AlignLeft)

    def set_rows(self, rows, note):
        # 公钥卡片增加持有人后可能超过四行；动态增行，不能丢掉最后的指纹字段。
        while len(self.names) < len(rows):
            index = len(self.names)
            name, value = QLabel(), SummaryValue()
            name.setObjectName('CertificateName')
            self.names.append(name)
            self.values.append(value)
            self.fields.addWidget(name, index, 0)
            self.fields.addWidget(value, index, 1)
        for index, (name, value) in enumerate(zip(self.names, self.values)):
            active = index < len(rows)
            name.setVisible(active)
            value.setVisible(active)
            if active:
                label, raw = rows[index]
                name.setText(label)
                name.setAlignment(Qt.AlignTop)
                value.set_value(raw, is_name=label in NAME_FIELDS)
        self.note.setText(note)
        self.note.setVisible(bool(note))


class CardEvidencePane(SignatureEvidencePane):
    """Reuse the existing evidence/cache/export API with a new visible projection."""
    def __init__(self, slot, source_paths, *, information_only):
        self._card_ready = False
        self.detail_dialog = None
        super().__init__(slot, source_paths, information_only=information_only)
        # Keep inherited data controls alive but do not expose its old tabs.
        self._legacy_controls = QWidget(self)
        self._legacy_controls.setLayout(self.layout())
        self._legacy_controls.hide()  # 限制：旧版验签控件保留但不显示，避免两套界面同时操作
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(5)
        heading = QLabel(f'{slot} · ' + ('原稿信息' if slot == 'A' else '工作副本信息')
                         if information_only else f'{slot} · ' + ('原稿验证结果' if slot == 'A' else '副本验证结果'))
        heading.setObjectName('Heading')
        body.addWidget(heading)
        self.file_label.setWordWrap(False)
        body.addWidget(self.file_label)
        self.state.setObjectName('WorkflowHint')
        self.state.setMaximumHeight(52)
        body.addWidget(self.state)
        self.signatures.setMinimumContentsLength(10)
        self.signatures.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.signatures.setMinimumWidth(0)
        body.addWidget(self.signatures)
        self.footer = QWidget(self._legacy_controls)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.export_button)
        footer.addStretch()

    def show_details(self, section=0):
        if self.detail_dialog is None:
            self.detail_dialog = QDialog(self.window())
            self.detail_dialog.setWindowTitle(f'{self.slot} · 完整签名与证书数据')
            self.detail_dialog.resize(800, 650)
            layout = QVBoxLayout(self.detail_dialog)
            self.detail_category = QComboBox()
            self.detail_category.addItems(('签名信息',) + TABS[1:] if self.information_only else TABS)
            layout.addWidget(self.detail_category)
            self.detail_section = EvidenceSection()
            layout.addWidget(self.detail_section, 1)
            self.detail_category.currentIndexChanged.connect(self._refresh_detail)
            close = QPushButton('关闭')
            close.clicked.connect(self.detail_dialog.hide)
            layout.addWidget(close, 0, Qt.AlignRight)
        self.detail_category.setCurrentIndex(section)
        self._refresh_detail()
        self.detail_dialog.show()
        self.detail_dialog.raise_()
        self.detail_dialog.activateWindow()

    def _section(self, index):
        data = super()._section(index)
        if index == 1 and self.generated_credentials:
            data = dict(data)
            data['待使用的新证书_不是PDF现有签名'] = self.generated_credentials  # 限制：新准备的证书单独标出，不当成 PDF 里已有的签名
        # 详情同样分段显示名称；导出仍使用原报告，不改动原始证书字符串。
        return display_name_fields(data)

    def _refresh_detail(self, *_):
        if self.detail_dialog is not None:
            self.detail_section.set_payload(self._section(self.detail_category.currentIndex()))

    def show_display_error(self, error):
        super().show_display_error(error)
        if self._card_ready:
            self._clear_summary()
        if self.detail_dialog is not None:
            self.detail_section.set_payload({'显示错误': f'{type(error).__name__}: {error}'})

    def _clear_summary(self):
        pass


class SignatureInformationPane(CardEvidencePane):
    def __init__(self, slot, source_paths):
        super().__init__(slot, source_paths, information_only=True)
        self.reference_template = None
        self.cards = {}
        self.rows = {}
        for section, title in SECTIONS:
            card = InformationCard(title, lambda _checked=False, s=section: self.show_details(s))
            self.cards[section] = card
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(7)
            layout.addWidget(card, 1)
            self.rows[section] = row
        self.operation_note = QLabel()
        self.operation_note.setObjectName('WorkflowHint')
        self.operation_note.setWordWrap(True)
        self._card_ready = True
        self._render_tab(0)

    def set_action(self, section, widget):
        widget.setFixedWidth(84)
        widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.rows[section].layout().addWidget(widget)

    def set_reference_template(self, template):
        # 模板只存在于内存中的信息展示区，绝不替换 B 的真实读取/验证报告。
        self.reference_template = template
        self._render_tab(0)

    def _section(self, index):
        template = getattr(self, 'reference_template', None)
        # 详情先展示 B 实际数据；A 参考模板仅附加在独立的“参考”节点内。
        data = super()._section(index)
        if template:
            data = dict(data)
            data['A 参考模板'] = display_name_fields(template_section(template, index))
        return data

    def _render_tab(self, _index):
        if not self._card_ready:
            return
        entry, audit = self._selected(), self._audit()
        # 信息卡只显示当前 PDF 或单独标注的待用凭据，不能用 A 模板填充 B 的
        # 签名值、ByteRange、证书、公钥，从而让中间信息和右侧实际检查相互矛盾。
        checked_empty = (not entry and not self.generated_credentials
                         and getattr(self, 'report', None) is not None
                         and str(getattr(self, '_base_state', '') or '').startswith('读取完成'))
        for section, card in self.cards.items():
            rows, note = summary_rows(section, entry, audit, self.generated_credentials)
            if checked_empty:
                rows = [(label, '已检查 · 未发现数字签名' if value is None else value) for label, value in rows]
                note = '已直接检查当前文件；未发现数字签名。'
            card.set_rows(rows, note)
        template = self.reference_template
        # 生成后的卡片说明指向实际文件，不能继续把“待生成模板”当成当前产物。
        # 检查没有形成报告时，也不能退回“B 尚未包含签名”的模板说明。
        if entry:
            note = '本栏签名、证书和公钥来自当前实际 PDF；检查结果见右侧。'
        elif self.generated_credentials:
            note = '新凭据已生成，是否已写入当前 PDF 以生成后读取为准。'
        elif str(getattr(self, '_base_state', '') or '').startswith('读取失败'):
            note = self._base_state
        elif checked_empty:
            note = '已直接检查当前文件；未发现数字签名。'
        elif template:
            note = '生成配置参考 A；不代表 B 已包含 A 的签名、证书或公钥。\n待生成名称：' + template['label']
        else:
            note = ''
        self.operation_note.setText(note)
        self.state.setToolTip(self._base_state)
        self._refresh_detail()

    def _clear_summary(self):
        for card in self.cards.values():
            card.set_rows([('显示状态', '显示失败，详见上方异常'), ('', ''), ('', ''), ('', '')], '')


class SignatureVerificationPane(CardEvidencePane):
    def __init__(self, slot, source_paths):
        super().__init__(slot, source_paths, information_only=False)
        self.setObjectName('SignatureVerificationCard')
        grid = QGridLayout()
        grid.setSpacing(5)
        for index, card in enumerate(self.status_cards):
            grid.addWidget(card, index // 2, index % 2)
            card.show()
        self.layout().addLayout(grid)
        self.revocation = QLabel()
        self.revocation.setObjectName('WorkflowHint')
        self.revocation.setWordWrap(True)
        self.layout().addWidget(self.revocation)
        self.detail_button = QPushButton(f'查看 {slot} 的详细验证依据 →')
        self.detail_button.setObjectName('SignatureDetailLink')
        self.detail_button.clicked.connect(lambda _checked=False: self.show_details(0))
        self.layout().addWidget(self.detail_button)
        self._card_ready = True
        self._render_tab(0)

    def _render_tab(self, _index):
        if not self._card_ready:
            return
        self.signatures.setVisible(len(self._entries()) > 1)
        if self.report is None:
            # 尚未读取时也保持两侧相同的五项标题，不回退到旧版四项布局。
            # 读取已经失败时不能仍写“尚未读取”，那会看起来像还没开始检查。
            pending = '检查未完成' if str(getattr(self, '_base_state', '') or '').startswith('读取失败') else '尚未读取'
            for card, title in zip(self.status_cards, STATUS_TITLES):
                card.setText(title + '\n' + pending)
                card.setProperty('tone', 'neutral')
                card.style().unpolish(card)
                card.style().polish(card)
        self.revocation.setText(revocation_description(self._audit()))
        self.state.setToolTip(self._base_state)
        self._refresh_detail()

    def _clear_summary(self):
        self.revocation.setText('验证信息显示失败，详见上方异常。')


def select_signature_field(pane, field):
    index = next((i for i, row in enumerate(pane._entries()) if row.get('field') == field), -1)
    if index >= 0 and pane.signatures.currentIndex() != index:
        pane.signatures.blockSignals(True)
        pane.signatures.setCurrentIndex(index)
        pane.signatures.blockSignals(False)
        pane._signature_changed()


class SignatureVerificationWorkspace(QWidget):
    """Two visible result panels. Selecting A never hides B or vice versa."""
    slot_changed = Signal(str)
    check_requested = Signal()
    next_requested = Signal()

    def __init__(self, source_paths):
        super().__init__()
        self.setObjectName('SignatureInspector')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        heading = QLabel('验证区 · A / B 同时显示')
        heading.setObjectName('Heading')
        layout.addWidget(heading)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.panes = {slot: SignatureVerificationPane(slot, source_paths) for slot in ('A', 'B')}
        for slot, pane in self.panes.items():
            pane.signatures.currentIndexChanged.connect(lambda _index, s=slot: self.slot_changed.emit(s))
            self.splitter.addWidget(pane)
        self.splitter.setSizes([300, 300])
        layout.addWidget(self.splitter, 1)
        # 操作栏固定在可伸缩结果区下方，A/B 详情再长也不会把按钮挤到滚动内容外。
        actions = QHBoxLayout()
        self.check_button = QPushButton('检测 A / B')
        self.check_button.setObjectName('WorkspaceCheckSignatures')
        self.check_button.setToolTip('重新读取 A、B 实际 PDF 并更新检查结果；不生成签名、不联网')
        self.check_button.clicked.connect(lambda: self.check_requested.emit())
        self.next_button = QPushButton('进入第 4 步 →')
        self.next_button.setObjectName('Primary')
        self.next_button.setToolTip('进入导出与对比；只跳转，不自动签署或导出')
        self.next_button.clicked.connect(lambda: self.next_requested.emit())
        for button in (self.check_button, self.next_button):
            button.setMinimumHeight(34)
            actions.addWidget(button, 1)
        layout.addLayout(actions)

    def select_slot(self, slot):
        for name, pane in self.panes.items():
            pane.setProperty('activeSlot', name == slot)
            pane.style().unpolish(pane)
            pane.style().polish(pane)

    def show_slot(self, slot):
        self.select_slot(slot)

    def select_signature_field(self, slot, field):
        select_signature_field(self.panes[slot], field)

    def update_slot(self, slot, path, cached, identity, preferred_field=None):
        self.panes[slot].show_report(path, cached, identity, preferred_field)
