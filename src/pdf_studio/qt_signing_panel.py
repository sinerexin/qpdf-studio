"""Step 3 preparation controls. No generation/signing/file write on navigation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class SigningPanel(QWidget):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        w = shell.w
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        title = QLabel('为 B 准备并生成数字签名')
        title.setObjectName('ModeHeading')
        title.setWordWrap(True)
        layout.addWidget(title)
        self.state = self.label('可直接使用已导入的 B；无需先由本应用生成预览。')
        self.state.setObjectName('SigningState')
        layout.addWidget(self.state)
        first = self.card(layout, '01', '待签文件 B')
        self.file = self.label('尚未导入 B')
        first.addWidget(self.file)
        self.import_button = w._button('导入已有工作副本 B…', lambda: w.open_file('B'))
        first.addWidget(self.import_button)
        second = self.card(layout, '02', '准备证书与公钥')
        self.credential = self.label('可选择已有证书，或生成独立的个人自签证书。')
        second.addWidget(self.credential)
        self.choose_button = w._button('选择我的 P12 / PFX…', shell.select_b_signing_certificate)
        second.addWidget(self.choose_button)
        self.generate_button = w._button('生成新公钥与自签证书…', shell.generate_b_credentials)
        second.addWidget(self.generate_button)
        self.generated_note = self.label('新生成的公钥显示在中间 B 的“公钥信息”卡片；私钥仅存于所选独立目录。')
        self.generated_note.setObjectName('WorkflowHint')
        second.addWidget(self.generated_note)
        third = self.card(layout, '03', '选择签名 PDF 输出位置')
        self.output = self.label('尚未选择；不会自动覆盖 A 或 B。')
        third.addWidget(self.output)
        self.output_button = w._button('选择另存位置…', shell.select_b_signing_output)
        third.addWidget(self.output_button)
        # 设置页必须使用已选凭据与输出位置，不能误调一键重建凭据流程。
        self.sign_button = w._button('使用所选凭据签署 B…', shell.sign_b_with_selected_credentials, primary=True)
        layout.addWidget(self.sign_button)
        note = self.label('此按钮使用上方选择的证书与输出位置，不重新生成凭据。本程序托管的密码自动读取；外部 P12/PFX 按需输入密码。')  # 限制：自签名称不等于机构认证
        note.setObjectName('WorkflowHint')
        layout.addWidget(note)
        self.finish_button = w._button('暂不签署 → 导出与对比', shell.finish_signing)
        layout.addWidget(self.finish_button)
        layout.addWidget(w._button('返回内容编辑', lambda: w.workspace_tabs.setCurrentIndex(0)))
        advanced = self.card(layout, '按需', '验证与工具')
        advanced.addWidget(w._button('重新离线读取 A / B', shell.reload_signature_evidence))
        advanced.addWidget(w._button('导入可信根并重新验签…', w.verify_active))
        layout.addStretch(1)

    @staticmethod
    def label(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def card(self, layout, number, title):
        frame = QFrame()
        frame.setObjectName('SigningStepCard')
        body = QVBoxLayout(frame)
        body.setContentsMargins(10, 10, 10, 10)
        heading = self.label(number + '  ' + title)
        heading.setObjectName('Heading')
        body.addWidget(heading)
        layout.addWidget(frame)
        return body
