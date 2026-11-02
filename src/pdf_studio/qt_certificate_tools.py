"""第三步同一签名流程的五个分项入口，不再显示互相重叠的证书工具。"""
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout


TOOL_DEFINITIONS = (
    ('parameters', '① 读取 A 参数', '读取实际 A 并开始新一轮参数准备；原稿不修改'),
    ('keys', '② 生成 B 密钥', '按本轮 A 参数生成密钥，暂存当前会话；缺少的前置步骤自动完成'),
    ('certificate', '③ 生成 B 证书', '沿用第二环节同一密钥生成证书、P12，并加密保存密钥'),
    ('sign', '④ 签署 B', '此按钮不写 PDF。请先检测 A / B，再进入第 4 步，由导出写出签署文件'),
    ('check', '⑤ 检查与对照', '重新读取当前打开的 A 和 B；不生成签名，也不写出 PDF'),
)


class CertificateToolsBar(QFrame):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.phase = 0
        self.check_failed = False
        self.setObjectName('SigningStepCard')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.hint = QLabel('“生成新签名”只保存签名数据。检测 A / B 后进入第 4 步，再点导出写出 PDF')
        self.hint.setObjectName('WorkflowHint')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(5)
        layout.addLayout(grid)
        self.callbacks = {
            key: (lambda target=index + 1: shell.run_signature_stage(target))
            for index, (key, _title, _description) in enumerate(TOOL_DEFINITIONS)
        }
        self.buttons = {}
        for index, (key, title, description) in enumerate(TOOL_DEFINITIONS):
            button = QPushButton(title)
            button.setObjectName('CertificateTool_' + key)
            button.setToolTip(description)
            # 冻结循环键，不让五个按钮都错误地调用最后一个工具。
            button.clicked.connect(lambda checked=False, action=key: self._invoke(action))
            grid.addWidget(button, 0, index)
            self.buttons[key] = button
        for column in range(5):
            grid.setColumnStretch(column, 1)

    def _invoke(self, key):
        """所有入口显示真实异常，避免 pythonw 下点击后没有可见反馈。"""
        w = self.shell.w
        if w._busy:
            w._notify('后台任务处理中，完成后可使用证书工具。')
            return
        title = next(title for action, title, _ in TOOL_DEFINITIONS if action == key)
        try:
            # 打开模态工具前暂停尚未开始的自动读取，避免用户确认时执行器被抢占。
            self.shell._signature_timer.stop()
            w._notify('签名流程：' + title)
            self.callbacks[key]()
        except Exception as exc:
            w._error(f'{title}打开失败：{type(exc).__name__}: {exc}')
        finally:
            self.shell.request_signature_reads()

    def set_busy(self, busy):
        for button in self.buttons.values():
            button.setEnabled(not busy)
        self.hint.setText('后台任务处理中 · 完成后可继续当前流程' if busy else self._idle_hint())

    def set_phase(self, phase, *, check_failed=False):
        self.phase = phase
        # 检查抛错后阶段号仍到 5，按钮不能因此打勾；任务结束后的刷新会再写同一句提示。
        self.check_failed = bool(check_failed) and phase >= 5
        for index, (key, title, _description) in enumerate(TOOL_DEFINITIONS):
            failed = self.check_failed and index == len(TOOL_DEFINITIONS) - 1
            mark = '✗ ' if failed else ('✓ ' if index < phase else '')
            self.buttons[key].setText(mark + title)
        self.hint.setText(self._idle_hint())

    def _idle_hint(self):
        if self.check_failed:
            return '本轮已签署，第 5 环节检查未完成 · 产物和凭据已保留，可再次点击「⑤ 检查与对照」'
        if self.phase == 3:
            return '签名数据已保存，尚未写 PDF · 请检测 A / B，进入第 4 步后点导出'
        return f'本轮已完成 {self.phase} / 5 环节 · “生成新签名”只保存数据，不写出 PDF'
