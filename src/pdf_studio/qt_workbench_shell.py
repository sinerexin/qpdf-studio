"""Native shell for the existing Qt application; all PDF commands remain owned by it.

No webview, simulated certificate values, PDF mutation on navigation, or automatic
network checks. Mode changes only rearrange already existing widgets.
"""
import shutil
from pathlib import Path
from .file_identity import file_identity
from .workflow_policy import signing_block_reason, signing_output_block_reason

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget, QListWidgetItem,
)


class CertificateSummary(QFrame):
    """Readable projection of the existing audit schema, not a new validator."""

    def __init__(self):
        super().__init__()
        self.setObjectName("CertificateSummary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)
        self.state = QLabel()
        self.state.setObjectName("SignatureStatus")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.values = {}
        for key, title in (
            ("subject", "持有人 / Subject（证书声明）"),
            ("issuer", "签发者 / Issuer（证书声明）"),
            ("serial", "证书序列号"), ("dates", "证书有效期"),
            ("fingerprint", "证书 SHA-256 指纹"),
            ("integrity", "字节完整性 / 数学签名"),
            ("trust", "独立证书信任 / 吊销检查"),
        ):
            name = QLabel(title)
            name.setObjectName("CertificateName")
            layout.addWidget(name)
            value = QLabel()
            value.setObjectName("CertificateValue")
            value.setTextFormat(Qt.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(value)
            self.values[key] = value
        self.clear()

    def clear(self):
        self.state.setText("尚未验签 · 字段存在不等于签名有效")
        for label in self.values.values():
            label.setText("尚未读取")

    def set_signature(self, row):
        cert = row.get("certificate") or {}
        dates = row.get("certificate_validity") or {}
        results = {
            "signature_valid_and_certificate_path_trusted": "签名有效 · 所提供本地信任路径成立",
            "cryptographically_valid_identity_unverified": "数学签名有效 · 身份信任未建立",  # 限制：数学有效不等于身份已验证
            "cryptographically_valid_but_certificate_not_trusted": "数学签名有效 · 证书未获独立信任",  # 限制：证书未通过可信根时，不显示为身份已验证
            "cryptographic_integrity_or_signature_failed": "签名字节完整性或数学验证失败",
            "inconclusive_validation_error": "验证结果不确定",
        }
        self.state.setText(results.get(row.get("overall_technical_result"), "无法确定验证结果"))
        def state(key):
            value = row.get(key)
            return "通过" if value is True else "未通过" if value is False else "无法判断"
        raw_fp = str(cert.get("sha256_fingerprint") or "尚未读取")
        fingerprint = "\n".join(raw_fp[i:i+30] for i in range(0, len(raw_fp), 30))
        values = {
            "subject": cert.get("claimed_subject") or "尚未读取",
            "issuer": cert.get("issuer") or "尚未读取",
            "serial": cert.get("serial_number") or "尚未读取",
            "dates": (f"{dates.get('not_before')}\n至 {dates.get('not_after')}"
                      if dates.get("not_before") and dates.get("not_after") else "尚未读取"),
            "fingerprint": fingerprint,
            "integrity": f"字节完整性：{state('intact')}\n数学签名：{state('signature_valid')}",
            "trust": ("本地信任路径成立" if row.get("signer_trusted") is True else "未建立独立信任")  # 限制：未通过时侧栏显示身份信任未建立
                     + "\n" + {
                         "offline_only_no_online_revocation": "仅离线；未联网检查吊销",
                         "strict_policy_requested": "已请求严格吊销策略，详见原始证据",
                         "online_fetch_opted_in_soft_fail": "已授权联网 / 软失败策略，详见原始证据",
                     }.get(row.get("revocation_assessment"), "吊销状态需查看详细证据"),
        }
        for key, value in values.items():
            self.values[key].setText(str(value))


class WorkbenchShell:
    """Rearrange the live desktop UI without replacing its functional backend."""

    def __init__(self, window):
        self.w = window
        self._mode = 0
        self._import_requested = False
        self._audit_owner = None
        self._signature_cache = {}
        self._signature_requests = set()
        self._signature_reading = {}
        self._manual_signature_reads = {}
        self._signed_output = None
        self._signed_original = None
        self._pending_b_signing = None
        self._b_generation_active = False
        self._signing_stage_session = None
        self._staged_check_pending_identity = None
        self._generated_credentials = None
        self._signing_selection = {}
        self._signature_timer = QTimer(window)
        self._signature_timer.setSingleShot(True)
        self._signature_timer.timeout.connect(self._read_next_signature)
        self._file_identity = None
        self._sidebar_visible = True
        self._inspector_requested = True
        window._workbench_shell = self
        old_root = window.takeCentralWidget()
        old_root.setObjectName("WorkspaceBody")
        outer = old_root.layout()
        main = outer.itemAt(0).widget()
        main_layout = main.layout()
        toolbar = main_layout.itemAt(0).widget()
        main_layout.removeWidget(toolbar)
        main_layout.removeWidget(window.workspace_tabs)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(9)

        self._build_header(toolbar)
        self.sidebar = self._build_sidebar()
        outer.insertWidget(0, self.sidebar)
        outer.setSpacing(12)
        self._move_editor_to_inspector()
        self._add_modes()
        from .qt_signature_cards import SignatureVerificationWorkspace
        self.signature_comparison = SignatureVerificationWorkspace(lambda: window.docs)
        self.document_stage = QStackedWidget()
        self.document_stage.addWidget(window.preview_splitter)
        from .qt_signature_workspace import SignatureIdentityWorkspace
        self.signature_workspace = SignatureIdentityWorkspace(lambda: window.docs)
        self.document_stage.addWidget(self.signature_workspace)
        window.center_splitter.insertWidget(0, self.document_stage)
        self.evidence_index = window.right_tabs.addTab(self.signature_comparison, 'A / B 验证结果')
        self.signing_index = self.evidence_index  # Compatibility for existing action routing.
        self.inspector_picker.addItem('A / B 验证结果', self.evidence_index)
        self.signature_comparison.slot_changed.connect(self.select_signature_slot)
        # 工作区按钮复用既有只读检查和第四步导航，不串入生成、签署或导出动作。
        self.signature_comparison.check_requested.connect(self.reload_signature_evidence)
        self.signature_comparison.next_requested.connect(self.finish_signing)
        self.signature_workspace.signature_selected.connect(self._central_signature_selected)
        for slot, pane in self.signature_comparison.panes.items():
            pane.signatures.currentIndexChanged.connect(
                lambda _index, s=slot: self.signature_workspace.select_signature_field(
                    s, self.signature_comparison.panes[s]._selected().get('field')))
        self._build_b_actions()

        # Replace two large empty PDF panes with a single purposeful welcome view.
        body_index = main_layout.indexOf(window.main_splitter)
        main_layout.removeWidget(window.main_splitter)
        self.stage = QStackedWidget()
        self.stage.addWidget(self._build_welcome())
        self.stage.addWidget(window.main_splitter)
        main_layout.insertWidget(body_index, self.stage, 1)

        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(toolbar)
        layout.addWidget(window.workspace_tabs)
        layout.addWidget(old_root, 1)
        window.setCentralWidget(root)
        window.workspace_tabs.setFixedHeight(116)
        for index in range(window.preview_splitter.count()):
            window.preview_splitter.setStretchFactor(index, 1)
        window.main_splitter.setChildrenCollapsible(False)
        window.main_splitter.setHandleWidth(9)
        window.inspector.setMinimumWidth(280)
        for slot, preview in window.previews.items():
            title = preview.layout().itemAt(0).layout().itemAt(0).widget()
            title.setText("A · 原始文档" if slot == "A" else "B · 修改 / 签署输出")
            badge = QLabel("原稿保留" if slot == "A" else "独立副本")
            badge.setObjectName("PreviewBadge")
            header = preview.layout().itemAt(0).layout()
            header.addWidget(badge)
            if slot == "B":
                self.export_pdf_button = window._button("导出 PDF", self.export_copy, primary=True)
                self.export_pdf_button.setToolTip(
                    "写出一份新的 PDF。签名数据已保存时，只有这一步会签署并导出，不覆盖原稿和当前 B。")
                header.addWidget(self.export_pdf_button)
        self.select_workspace(window.workspace_tabs.currentIndex())
        self.refresh()

    def _build_header(self, toolbar):
        w = self.w
        layout = toolbar.layout()
        self.header_file = layout.itemAt(1).widget()
        self.header_file.setMinimumWidth(0)
        self.header_file.setMaximumWidth(310)
        self.header_file.setText("PDF 工作台 · 本机处理")
        # 导出只留在 B 预览框右侧，避免和顶部再放一个相同动作。
        layout.itemAt(3).widget().setText("打开 PDF")
        layout.itemAt(3).widget().setObjectName("")
        layout.itemAt(4).widget().setText("文件对比")
        self.detail_button = w._button("详情", self.toggle_inspector)
        self.detail_button.setToolTip("展开或收起右侧编辑 / 证书检查器")
        layout.addWidget(self.detail_button)

    def _build_sidebar(self):
        w = self.w
        frame = QFrame()
        frame.setObjectName("WorkflowSidebar")
        frame.setFixedWidth(196)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(5)
        title = QLabel("工作流程")
        title.setObjectName("Heading")
        layout.addWidget(title)
        hint = QLabel("原稿与工作副本分离")
        hint.setObjectName("Dim")
        layout.addWidget(hint)
        self.steps = []
        for label, action in (
            ("01  导入原稿", self.show_import),
            ("02  内容编辑", lambda: self.show_workspace(0)),
            ("03  签名与证书", lambda: self.show_workspace(1)),
            ("04  导出与对比", lambda: self.show_workspace(2)),
        ):
            button = w._button(label, action)
            button.setObjectName("WorkflowStep")
            button.setCheckable(True)
            layout.addWidget(button)
            self.steps.append(button)
        layout.addStretch(1)
        return frame

    def _move_editor_to_inspector(self):
        w = self.w
        w.selection_editor.setParent(None)
        self.editor_index = w.right_tabs.addTab(w.selection_editor, "选中内容")
        head = w.inspector.layout().itemAt(0).widget()
        head.setText("当前工作区")
        self.inspector_picker = QComboBox()
        self.inspector_picker.setToolTip("切换当前编辑、证书、对象与分析详情")
        for i in range(w.right_tabs.count()):
            self.inspector_picker.addItem(w.right_tabs.tabText(i), i)
        w.inspector.layout().insertWidget(1, self.inspector_picker)
        w.right_tabs.tabBar().hide()
        self.inspector_picker.currentIndexChanged.connect(w.right_tabs.setCurrentIndex)
        w.right_tabs.currentChanged.connect(self._sync_inspector)
        self.certificate = CertificateSummary()
        # Both summary and original, selectable audit evidence remain accessible.
        cert_scroll = QScrollArea()
        cert_scroll.setWidgetResizable(True)
        cert_scroll.setMinimumHeight(120)
        cert_scroll.setWidget(self.certificate)
        w.signature_view.layout().insertWidget(1, cert_scroll, 2)
        w.signature_details.setMaximumHeight(90)
        w.signature_details.setPlaceholderText("详细证据；证书字段在上方分项显示")
        w.signature_list.setMaximumHeight(70)
        w.signature_view.layout().insertWidget(0, w._button("读取并验证当前签名", w.verify_active, primary=True))
        sources = QWidget()
        sources_layout = QVBoxLayout(sources)
        sources_layout.setContentsMargins(0, 0, 0, 0)
        sources_layout.setSpacing(5)
        self.signature_sources = {}
        for slot, title in (("A", "A 原稿"), ("B", "B 工作副本")):
            line = QHBoxLayout()
            label = QLabel(title + "：未打开")
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            self.signature_sources[slot] = label
            line.addWidget(label, 1)
            line.addWidget(w._button("查看 " + slot, lambda checked=False, s=slot: self.select_signature_slot(s)))
            sources_layout.addLayout(line)
        self.signature_context = QLabel("请选择 A 原稿或 B 工作副本查看；默认离线，不自动签署。")
        self.signature_context.setWordWrap(True)
        self.signature_context.setTextFormat(Qt.PlainText)
        sources_layout.addWidget(self.signature_context)
        sources_layout.addWidget(w._button("返回第 3 步 · 签署工作副本", self.show_signing_flow))
        w.signature_view.layout().insertWidget(0, sources)
        self._build_signing_flow()

    def _build_signing_flow(self):
        from .qt_signing_panel import SigningPanel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.signing_panel = SigningPanel(self)
        scroll.setWidget(self.signing_panel)
        panel = self.signing_panel
        self.signing_state, self.signing_file = panel.state, panel.file
        self.generate_b_key_button = panel.generate_button
        self.generated_credentials_note = panel.generated_note
        self.sign_b_button, self.finish_signing_button = panel.sign_button, panel.finish_button
        self.signing_output = panel.output
        self.signing_dialog = QDialog(self.w)
        self.signing_dialog.setWindowTitle('B · 证书与签署设置')
        self.signing_dialog.resize(460, 680)
        dialog_layout = QVBoxLayout(self.signing_dialog)
        dialog_layout.addWidget(scroll, 1)
        # Keep the real signing action visible, even when settings are scrolled.
        panel.layout().removeWidget(panel.sign_button)
        dialog_layout.addWidget(panel.sign_button)
        close = self.w._button('返回 A / B 签名信息', self.signing_dialog.hide)
        dialog_layout.addWidget(close)

    def _build_b_actions(self):
        w = self.w
        pane = self.signature_workspace.panes['B']
        self.b_action_state = pane.operation_note
        self.b_sign_action = w._button('生成\n新签名', self.generate_b_signature, primary=True)
        self.b_sign_action.setToolTip('只保存本轮签名数据：读取 A 参数、生成密钥和证书。不写 PDF。检测 A / B 后进入第 4 步，再点导出。')
        pane.set_action(0, self.b_sign_action)
        # 证书卡片也使用同一个第三阶段，不再从这里弹出已移除的设置入口。
        self.b_settings_button = w._button('生成\n证书', lambda: self.run_signature_stage(3))
        pane.set_action(1, self.b_settings_button)
        self.b_key_action = w._button('生成\n密钥对', lambda: self.run_signature_stage(2))
        self.b_key_action.setToolTip('执行本轮第二环节，仅生成密钥并显示待用公钥；第三环节沿用同一密钥生成证书。')
        pane.set_action(2, self.b_key_action)
        self.b_raw_action = w._tool_menu('查看 /\n导出数据', [
            ('查看完整原始签名数据…', lambda: pane.show_details(5), '查看已有解析结构'),
            ('导出完整 JSON…', pane.export_json, '主动导出证据'),
            ('导出当前 CMS DER…', pane.export_der, '主动导出当前签名容器'),
        ])
        pane.set_action(5, self.b_raw_action)

    def generate_b_signature(self):
        # 主按钮直接提交完整生成流水线，不再把 A 的界面缓存就绪当作生成前提。
        # 已有任务占用线程时保留本次点击，任务结束后自动处理，不丢失请求。
        if getattr(self, '_b_generation_active', False):
            self.w._notify('B 新签名正在生成，同一次操作无需重复点击。')
            return
        if self.w._busy:
            if self._pending_b_signing is None:
                self._pending_b_signing = self._signing_context_identity()
                QTimer.singleShot(250, self._resume_b_signing)
            self.w._notify('已接收生成请求；当前任务结束后自动读取 A 并生成 B 新签名。')
            self.refresh_signing_flow()
            return
        self._pending_b_signing = None
        try:
            from .qt_automatic_signing import start_automatic_b_signing
            start_automatic_b_signing(self)
        except Exception as exc:
            # Qt 槽函数异常必须显示到窗口，不能只写到 pythonw 不可见的标准错误。
            self.w._error(f'启动 B 签名生成失败：{type(exc).__name__}: {exc}')

    def run_signature_stage(self, target):
        from .qt_signing_stages import run_signature_stage
        run_signature_stage(self, target)

    def _resume_b_signing(self):
        pending = self._pending_b_signing
        if pending is None:
            return
        if self._mode != 1:
            self._pending_b_signing = None
            self.refresh_signing_flow()
            return
        if pending != self._signing_context_identity():
            self._pending_b_signing = None
            self.w._notify('A 或 B 已切换，先前请求未应用到新文档；可再次点击生成。')
            self.refresh_signing_flow()
            return
        if self.w._busy:
            # 不在轮询中重启 A 的读取定时器，避免等待过程反复推迟真正的读取。
            QTimer.singleShot(250, self._resume_b_signing)
            return
        self._pending_b_signing = None
        self.generate_b_signature()

    def _signing_context_identity(self):
        return (self._signature_identity('A'), self._signature_identity('B'))

    def _signing_reason(self):
        return signing_block_reason(busy=self.w._busy, b_accessible=self._signature_identity('B') is not None)

    def sign_b_with_selected_credentials(self):
        """设置页签署使用既有凭据；选文件/密码的模态窗口期间不抢跑自动读取任务。"""
        if self.w._busy:
            self.w._notify('后台任务处理中，完成后可使用所选凭据签署。')
            return
        self._signature_timer.stop()
        try:
            self.w.sign_active()
        except Exception as exc:
            self.w._error(f'使用所选凭据签署失败：{type(exc).__name__}: {exc}')
        finally:
            # 取消、失败和成功提交都恢复只读任务；执行器仍负责串行运行。
            self.request_signature_reads()

    def b_signing_ready(self):
        return not self._signing_reason()

    def _select_signing_value(self, key, value):
        identity = self._signing_context_identity()
        if self._signing_selection.get('identity') != identity:
            self._signing_selection = {'identity': identity}
        self._signing_selection[key] = Path(value).resolve()

    def selected_signing_credential(self):
        if self._signing_selection.get('identity') == self._signing_context_identity():
            value = self._signing_selection.get('credential')
            return value if value and value.is_file() else None
        return None

    def selected_signing_output(self):
        if self._signing_selection.get('identity') == self._signing_context_identity():
            value = self._signing_selection.get('output')
            if value and not signing_output_block_reason(value, self.w.docs.values()):
                return value
        return None

    def select_b_signing_certificate(self):
        if self.w._busy:
            self.w._notify("后台任务处理中，请等待完成。")
            return
        path, _ = QFileDialog.getOpenFileName(self.w, '选择自己的签署凭据', '', 'Certificate (*.p12 *.pfx)')
        if path and Path(path).is_file():
            self._select_signing_value('credential', path)
        self.refresh_signing_flow()

    def select_b_signing_output(self):
        if not self.b_signing_ready():
            self.w._notify(self._signing_reason())
            return
        path, _ = QFileDialog.getSaveFileName(self.w, '选择独立签名 PDF 输出位置',
                                            str(self.default_signed_output(self.w.docs['B'])), 'PDF (*.pdf)')
        if path:
            target = Path(path)
            reason = signing_output_block_reason(target, self.w.docs.values())
            if reason:
                self.w._error(reason)
                return
            self._select_signing_value('output', target)
        self.refresh_signing_flow()

    def _add_modes(self):
        w = self.w
        # 第 4 步不再放这一排按钮。导出 PDF 在 B 预览框标题右侧。
        w.workspace_tabs.addTab(QWidget(), "文件对比")
        structure_page = QWidget()
        row = QHBoxLayout(structure_page)
        row.setContentsMargins(12, 8, 12, 8)
        row.addWidget(w._button("解析对象与资源", w.inspect_active, primary=True))
        row.addWidget(w._button("绘制顺序", w.analyze_paint))
        row.addWidget(w._button("修订线索", w.analyze_revisions))
        row.addWidget(w._tool_menu("更多分析", [
            ("深度解析", w.deep_analysis, "读取既有 PDF 语义分析"),
            ("完整解析导出", w.export_bundle, "导出独立分析包"),
            ("文档属性", lambda: w.show_advanced_inspector(1), "查看或另存标准属性"),
        ]))
        row.addStretch()
        w.workspace_tabs.addTab(structure_page, "PDF 结构")
        # Step 3 is an explicit output workflow, not the generic certificate
        # toolbox. Advanced existing capabilities remain in the tools menu.
        w.signer_output_button.setText("个人证书重新签发并签署…")
        w.signer_output_button.setObjectName("")
        signing_row = w.workspace_tabs.widget(1).layout()
        while signing_row.count():
            item = signing_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
        # 工具直接展开为两行独立按钮，不再把功能藏进“更多证书工具”菜单。
        from .qt_certificate_tools import CertificateToolsBar
        commands = QWidget()
        command_layout = QVBoxLayout(commands)
        command_layout.setContentsMargins(0, 0, 0, 0)
        navigation = QHBoxLayout()
        navigation.addWidget(w._button("← 返回编辑", lambda: w.workspace_tabs.setCurrentIndex(0)))
        navigation.addStretch()
        command_layout.addLayout(navigation)
        self.certificate_tools = CertificateToolsBar(self)
        command_layout.addWidget(self.certificate_tools)
        signing_row.addWidget(commands, 1)
        w.signing_tools.hide()
        content_row = w.workspace_tabs.widget(0).layout()
        self.next_step_toolbar = w._button("完成编辑 → 签名与证书", self.advance_to_signing, primary=True)
        content_row.insertWidget(max(0, content_row.count() - 1), self.next_step_toolbar)
        w.workspace_tabs.setTabText(1, "签名与证书")

    def _build_welcome(self):
        w = self.w
        scroll = QScrollArea()
        scroll.setObjectName("WelcomeScroll")
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("WelcomeContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(16)
        layout.addStretch(1)
        from . import APP_NAME
        eyebrow = QLabel(APP_NAME + "  ·  本地 PDF 工作台")
        eyebrow.setObjectName("Dim")
        layout.addWidget(eyebrow)
        title = QLabel("从一份 PDF 开始")
        title.setWordWrap(True)
        title.setObjectName("WelcomeTitle")
        layout.addWidget(title)
        description = QLabel("查看原稿、编辑工作副本、识别与验证数字签名。\n原始文件保留不变，结果由你选择新路径保存。")
        description.setObjectName("WorkflowContext")
        description.setWordWrap(True)
        layout.addWidget(description)
        from .qt_import_card import PdfImportCard
        self.import_card = PdfImportCard()
        self.import_card.browse_requested.connect(self.browse_original)
        self.import_card.pdf_dropped.connect(self.import_original)
        self.import_card.rejected.connect(w._notify)
        layout.addWidget(self.import_card)
        actions = QHBoxLayout()
        actions.addWidget(w._button("导入对比文件", lambda: w.open_file("B")))
        self.resume_workspace = w._button("返回当前工作", lambda: self.show_workspace(self._mode))
        actions.addWidget(self.resume_workspace)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addSpacing(18)
        for title, detail in (
            ("内容编辑", "点击原稿文字或图片，在右侧修改并预览实际工作副本。"),
            ("签名与证书", "主体、签发者、有效期、指纹、数学完整性和独立信任分别显示。"),
            ("文件对比", "并排阅读原稿和结果，按需展开真实像素差异与报告。"),
        ):
            label = QLabel(title)
            label.setObjectName("Heading")
            layout.addWidget(label)
            hint = QLabel(detail)
            hint.setObjectName("Dim")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def show_workspace(self, index):
        if self.w.workspace_tabs.currentIndex() == index:
            self.select_workspace(index)
        else:
            self.w.workspace_tabs.setCurrentIndex(index)

    def show_import(self):
        self._import_requested = True
        self.stage.setCurrentIndex(0)
        self.w.page_controls.hide()
        for index, button in enumerate(self.steps):
            button.setChecked(index == 0)
        self.import_card.setFocus()

    def browse_original(self):
        if self.w._busy:
            self.w._notify("后台任务处理中，请等待完成。")
            return
        filename, _ = QFileDialog.getOpenFileName(self.w, "导入原稿 PDF", "", "PDF 文件 (*.pdf)")
        if filename:
            self.import_original(Path(filename))

    def import_original(self, path):
        if self.w._busy:
            self.w._notify("后台任务处理中，请等待完成。")
            return
        try:
            self.w.load_path("A", Path(path))
        except Exception as exc:
            self.w._error("无法导入 PDF：" + str(exc))
            return
        self._import_requested = False
        self.show_workspace(0)

    def _sync_inspector(self, index):
        self.inspector_picker.blockSignals(True)
        self.inspector_picker.setCurrentIndex(index)
        self.inspector_picker.blockSignals(False)

    def _fit_previews(self):
        """第 4 步只并排 A 和 B。编辑栏不在预览里时，两栏平分宽度。"""
        splitter = self.w.preview_splitter
        count = splitter.count()
        for index in range(count):
            splitter.setStretchFactor(index, 1)
        if count >= 3:
            editing = self._mode == 0
            self.w.selection_editor.setVisible(editing)
            splitter.setCollapsible(1, not editing)
            splitter.setSizes([530, 320, 530] if editing else [1, 0, 1])
        elif count == 2:
            splitter.setSizes([1, 1])

    def select_workspace(self, index):
        w = self.w
        previous_mode = self._mode
        self._import_requested = False
        self._mode = index
        # 第三步有两行独立工具，不能继续沿用旧的 116px 固定高度把按钮裁掉。
        if index == 1:
            height = (w.workspace_tabs.tabBar().sizeHint().height()
                      + w.workspace_tabs.widget(index).sizeHint().height() + 12)
            w.workspace_tabs.setFixedHeight(max(116, height))
        elif index == 2:
            # 这一页没有工具按钮，只留标签栏，避免空出一条空白。
            w.workspace_tabs.setFixedHeight(w.workspace_tabs.tabBar().sizeHint().height() + 6)
        else:
            w.workspace_tabs.setFixedHeight(116)
        if w._edit_mode != "none":
            w._set_edit_mode("none")
        self.document_stage.setCurrentIndex(1 if index == 1 else 0)
        w.inspector.setMinimumWidth(340 if index == 1 else 280)
        if index == 0:
            w.right_tabs.setCurrentIndex(self.editor_index)
        elif index == 1:
            w.right_tabs.setCurrentIndex(self.evidence_index)
            w.results.hide()
            if previous_mode != 1 and w.docs.get("B"):
                w.active_slot.setCurrentText("B")
            self.signature_slot_changed()
            self.request_signature_reads()
        elif index == 3:
            w.right_tabs.setCurrentIndex(0)
        self._inspector_requested = index != 2
        w.inspector.setVisible(self._inspector_requested)
        w.main_splitter.setSizes([900, 380 if index == 1 else 320] if self._inspector_requested else [1220, 0])
        self._fit_previews()
        for i, button in enumerate(self.steps):
            button.setChecked(i == {0: 1, 1: 2, 2: 3, 3: -1}.get(index))
        w._refresh_workflow_context()
        w._render_delayed()

    def refresh(self):
        w = self.w
        if not hasattr(self, "stage"):
            return
        loaded = any(w.docs.values())
        importing = self._import_requested or not loaded
        self.resume_workspace.setVisible(loaded)
        w.page_controls.setVisible(loaded and self._mode != 1 and not importing)
        self.stage.setCurrentIndex(0 if importing else 1)
        if importing:
            for index, button in enumerate(self.steps):
                button.setChecked(index == 0)
        source = w._active()
        if source:
            self.header_file.setText(source.name)
            self.header_file.setToolTip(str(source))
        self.refresh_signing_flow()
        self.resize()

    def edit_preview_completed(self, source, result):
        self._signed_output = None
        self._signed_original = None
        self.refresh_signing_flow()

    def generate_b_credentials(self, *, reissue=False, preferred_algorithm=None):
        w = self.w
        if w._busy:
            w._notify("后台任务处理中，请等待完成。")
            return
        original, result = w.docs.get("A"), w.docs.get("B")
        identity = self._signature_identity("B")
        from .qt_keygen_dialog import open_personal_certificate_editor
        def completed(folder, safe):
            if w.docs.get("A") != original or w.docs.get("B") != result or self._signature_identity("B") != identity:
                w._notify(f"新密钥已保存到 {folder}；文档已变化，未绑定到当前 B。")
                return
            from asn1crypto import x509 as asn_x509
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            from .signature_evidence import certificate_details
            # Only read the public certificate, never the generated private key.
            cert = x509.load_pem_x509_certificate((folder / "self-signed-certificate.pem").read_bytes())
            details = certificate_details(asn_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)))
            self._generated_credentials = {"folder": folder, "original": original, "result": result, "identity": identity}
            public_material = {
                "note": "新生成的凭据，不是从 PDF 提取的现有签名；是否已签入 B，以实际输出为准。",
                "folder": str(folder), "p12_path": str(folder / "personal-signing.p12"),
                "public_certificate": details,
            }
            self.signature_comparison.panes['B'].set_generated_credentials(public_material)
            self.signature_workspace.panes['B'].set_generated_credentials(public_material)
            self._select_signing_value('credential', folder / 'personal-signing.p12')
            self.generated_credentials_note.setText(f"已生成独立公钥与自签证书：\n{folder}\n公钥详见中间 B 的“公钥信息”；可点“查看完整信息”展开。")
            self.refresh_signing_flow()
            self.show_signature_evidence("B")
        # 模态参数窗口不能让尚未开始的自动验签占用执行器，导致保存时任务被丢弃。
        self._signature_timer.stop()
        try:
            open_personal_certificate_editor(w, reissue=reissue,
                preferred_algorithm=preferred_algorithm, on_created=completed)
        finally:
            self.request_signature_reads()

    def generate_b_keys_from_a(self):
        """从 A 当前选中签名的实际报告读密钥规格，不依赖旧的仅字段验签档案。"""
        w = self.w
        original = w.docs.get('A')
        if original is None:
            w._error('请先导入 A 原稿，再读取它的密钥算法。')
            return
        identity = self._signature_identity('A')
        selected = self.signature_workspace.panes['A']._selected().get('field')
        from .signature_evidence import read_signature_audit
        from .automatic_signing import generation_reference, reference_defaults

        def read_parameters():
            report = read_signature_audit(original, [], allow_network=False)
            reference = generation_reference(report, selected)
            return reference_defaults(reference['entry'], reference['audit'])

        def completed(parameters):
            if self._signature_identity('A') != identity:
                w._notify('A 已切换，未把旧原稿算法应用到新文件。')
                return
            # 算法选择与 A 的数学验签/信任结论无关；未识别时界面明确说明默认值。
            w._notify(parameters['note'])
            self.generate_b_credentials(preferred_algorithm=parameters['algorithm'])

        w._task('读取 A 当前签名的密钥算法', read_parameters, completed)

    def compare_current_signatures(self):
        # 对照使用现有全面解析和右侧 A/B 验证区，避免旧对照器遗漏未关联字段树对象。
        self.show_full_signature_evidence()
        self.reload_signature_evidence()

    def generated_credential_path(self):
        generated = self._generated_credentials
        if generated and generated['original'] == self.w.docs.get('A') and generated['result'] == self.w.docs.get('B') and generated['identity'] == self._signature_identity('B'):
            return generated['folder'] / 'personal-signing.p12'
        return None

    def advance_to_signing(self):
        w = self.w
        w.workspace_tabs.setCurrentIndex(1)
        w.active_slot.setCurrentText("B")
        self.show_full_signature_evidence()
        w._notify("第 3 步：A / B 信息逐行对照；点击 B 签名行右侧按钮，自动生成凭据并签署。")

    def show_signing_flow(self):
        self.refresh_signing_flow()
        self.signing_dialog.show()
        self.signing_dialog.raise_()
        self.signing_dialog.activateWindow()

    def show_signature_evidence(self, slot):
        if self.w.docs.get(slot) is None:
            self.w._notify(f"{slot} 尚未打开 PDF。")
            return
        self.select_signature_slot(slot)
        self.signature_comparison.show_slot(slot)
        self.show_full_signature_evidence()

    def _central_signature_selected(self, slot, field):
        self.signature_comparison.select_signature_field(slot, field)
        self.select_signature_slot(slot)
        self.signature_comparison.show_slot(slot)

    def show_full_signature_evidence(self):
        self.document_stage.setCurrentIndex(1 if self._mode == 1 else 0)
        self._inspector_requested = True
        self.w.show_advanced_inspector(self.evidence_index)
        self.signature_comparison.select_slot(self.w.active_slot.currentText())
        self._refresh_signature_sources()
        self.request_signature_reads()

    def reload_signature_evidence(self):
        if self.w._busy:
            self.w._notify("请等待当前后台任务完成后再重新读取。")
            return
        # 用户显式点击检测：对两侧当前文件各检查一次，不受自动流程“B 待生成”时机影响。
        self._manual_signature_reads = {slot: identity for slot in ('A', 'B')
                                        if (identity := self._signature_identity(slot)) is not None}
        self._signature_cache.clear()
        self.clear_signature()
        self.show_full_signature_evidence()

    def refresh_signing_flow(self):
        tools = getattr(self, 'certificate_tools', None)
        if tools is not None:
            tools.set_busy(self.w._busy)
        view = getattr(self, 'signature_comparison', None)
        if view is not None and hasattr(view, 'check_button'):
            view.check_button.setEnabled(not self.w._busy and any(self.w.docs.values()))
        if not hasattr(self, 'signing_panel'):
            return
        w, panel = self.w, self.signing_panel
        result = w.docs.get('B')
        reason = self._signing_reason()
        ready = not reason
        signed = (result is not None and self._signed_output == result
                  and self._signed_original == w.docs.get('A')
                  and not w.selection_editor.has_pending_edits())
        credential, output = self.selected_signing_credential(), self.selected_signing_output()
        panel.file.setText(result.name if result else '尚未导入 B')
        panel.file.setToolTip(str(result) if result else '')
        panel.state.setText('签署输出已保存；是否有效请查看实际验证结果。' if signed else reason or
                            ('准备完成，点击下方按钮签署。' if credential and output else 'B 已就绪；请选择凭据与输出位置。'))
        panel.import_button.setEnabled(not w._busy)
        for button in (panel.generate_button, panel.choose_button, panel.output_button):
            button.setEnabled(not w._busy)
        panel.credential.setText('已选凭据：' + credential.name if credential else '尚未选择 P12 / PFX；也可生成独立自签资料。')
        panel.credential.setToolTip(str(credential) if credential else '')
        # 已签过一次后仍优先显示本次新选位置，不能被上一份已保存输出遮盖。
        panel.output.setText(str(output) if output else '已保存签署输出：\n' + str(result) if signed else '尚未选择新的输出路径；签署时可选择。')
        panel.sign_button.setEnabled(ready)
        if hasattr(self, 'b_sign_action'):
            # 忙碌期间主按钮仍可接收并排队；是否能签署不取决于 A 的验签结论。
            self.b_sign_action.setEnabled(self._signature_identity('B') is not None
                                          and not self._b_generation_active
                                          and self._pending_b_signing is None)
            self.b_settings_button.setEnabled(not w._busy)
            self.b_key_action.setEnabled(not w._busy)
            self.b_raw_action.setEnabled(not w._busy)
            self.b_action_state.setText(
                '签名数据已保存 · 请检测 A / B，进入第 4 步后点导出' if self._saved_signature_ready() and not signed else
                '新签署输出已载入 · 本栏查看签名证书，右侧查看验证结果' if signed else
                reason or ('凭据：' + credential.name if credential else '选择已有证书，或生成新公钥与自签证书'))
            self.b_action_state.setToolTip(str(output or result or ''))
        panel.finish_button.setEnabled(True)
        panel.finish_button.setText('进入第 4 步 →' if self._saved_signature_ready() else
                                    ('完成签署 → 导出与对比' if signed else '暂不签署 → 导出与对比'))
        if hasattr(self, 'next_step_toolbar'):
            self.next_step_toolbar.setEnabled(True)
        w.selection_editor.next_step_button.setEnabled(True)

    def signed_output_completed(self, source, result):
        self._signed_output = result
        self._signed_original = self.w.docs.get("A")
        self.w.selection_editor._preview_note("B 已切换为实际签署输出文件；检查依据来自生成后文件。", "ready")
        self.w.selection_editor.save_state.setText("本轮内容编辑已完成；当前 B 为实际签署输出。")
        self.refresh_signing_flow()
        self.signing_dialog.hide()
        self.show_signature_evidence('B')

    def finish_signing(self):
        self.signing_dialog.hide()
        self.w.workspace_tabs.setCurrentIndex(2)

    def resize(self):
        w = self.w
        show_sidebar = w.width() >= 1350
        self.sidebar.setVisible(show_sidebar)
        if self._sidebar_visible != show_sidebar:
            self._sidebar_visible = show_sidebar
            w._render_delayed()

    def toggle_inspector(self):
        self._inspector_requested = not self.w.inspector.isVisible()
        self.w.inspector.setVisible(self._inspector_requested)
        self.w.main_splitter.setSizes([900, 320] if self._inspector_requested else [1220, 0])
        self.w._render_delayed()

    def clear_signature(self):
        self._audit_owner = None
        self.w._signature_audit = None
        self.w.signature_list.clear()
        self.w.signature_details.clear()
        self.w.signature_summary.setText("尚未核验 · 证书身份与数学签名分开评估")
        self.certificate.clear()

    def _signature_identity(self, slot):
        path = self.w.docs.get(slot)
        if path is None:
            return None
        try:
            # 读取、生成预检和回调共同使用同一函数，不维护互不兼容的元组格式。
            return file_identity(path)
        except OSError:
            return None

    def _cached_signature(self, slot):
        cached = self._signature_cache.get(slot)
        identity = self._signature_identity(slot)
        return cached if cached and identity is not None and cached[0] == identity else None

    def _refresh_signature_sources(self):
        for slot, label in self.signature_sources.items():
            path = self.w.docs.get(slot)
            cached = self._cached_signature(slot)
            identity = self._signature_identity(slot)
            manual = getattr(self, '_manual_signature_reads', {}).get(slot)
            # 第四环节故意不自动读 B。这里不能写成“等待读取”或“未签署”。
            check_pending = (slot == 'B' and path is not None and cached is None and identity is not None
                             and getattr(self, '_staged_check_pending_identity', None) == identity
                             and manual != identity)
            state = ("无法读取文件，未作结论" if path and identity is None else
                     "读取失败，未作结论" if cached and cached[2] else
                     "验证异常；详见证据" if cached and cached[1].get("validation_error") else
                     f"{cached[1]['count']} 个数字签名" if cached else
                     "待离线读取") if path else "未打开"
            waiting_generation = (slot == 'B' and path is not None and cached is None
                                  and identity is not None
                                  and getattr(self, '_signed_output', None) != path
                                  and manual != identity)
            if check_pending:
                state = '已签署，尚未检查'
            elif waiting_generation:
                state = '待生成新签名；生成完成后检查实际 B'
            elif path and cached is None and identity is not None:
                state = ('正在离线读取，尚未形成结论' if
                         getattr(self, '_signature_reading', {}).get(slot) == identity
                         else '等待读取，尚未形成结论')
            label.setText(f"{slot} {'原稿' if slot == 'A' else '工作副本'}：{state}")
            label.setToolTip(str(path) if path else "")
            # A failing display must not prevent another pane/side loading.
            for view_name in ("signature_comparison", "signature_workspace"):
                view = getattr(self, view_name, None)
                if view is None:
                    continue
                try:
                    preferred = None
                    if slot == 'B':
                        try:
                            preferred = self.signature_workspace.panes['A']._selected().get('field')
                        except Exception:
                            preferred = None
                    view.update_slot(slot, path, cached, self._signature_identity(slot), preferred)
                    if check_pending:
                        view.panes[slot].set_check_pending()
                    elif waiting_generation:
                        view.panes[slot].set_generation_pending()
                    elif cached is None and path and identity is not None:
                        reading = getattr(self, '_signature_reading', {}).get(slot) == identity
                        view.panes[slot].set_reading(reading)
                except Exception as exc:
                    view.panes[slot].show_display_error(exc)
                    self.w._notify(f"{slot} 签名信息显示失败：{type(exc).__name__}: {str(exc) or '该异常未提供说明'}")

    def select_signature_slot(self, slot):
        if self.w.docs.get(slot) is None:
            self.w._notify(f"{slot} 尚未打开 PDF")
            return
        self.w.active_slot.setCurrentText(slot)
        self.signature_slot_changed()
        self.request_signature_reads()

    def signature_slot_changed(self):
        self.clear_signature()
        self._refresh_signature_sources()
        slot = self.w.active_slot.currentText()
        path = self.w.docs.get(slot)
        if hasattr(self, 'signature_comparison'):
            self.signature_comparison.select_slot(slot)
        self.signature_context.setText(
            f"正在查看 {slot} {'原稿' if slot == 'A' else '工作副本'}：{path.name}" if path else f"{slot} 尚未打开 PDF")
        cached = self._cached_signature(slot)
        if cached:
            if cached[2]:
                self.w.signature_summary.setText("离线读取失败，不能断言无签名：" + cached[2])  # 限制：离线读取失败时不能断言没有签名
            else:
                self._display_signature_audit(cached[1])
        elif path:
            self.w.signature_summary.setText(f"{slot}：尚未读取此文件的签名；切到数字签名工作区后自动离线读取。")
        if self._mode == 1:
            self.request_signature_reads()

    def invalidate_signature(self, slot):
        if not self._b_generation_active:
            # 用户导入另一份 A/B 时另起流程；正在回填本轮签署 B 时保留同一会话。
            self._signing_stage_session = None
            self._staged_check_pending_identity = None
            if hasattr(self, 'certificate_tools'):
                self.certificate_tools.set_phase(0)
        self._signing_selection = {}
        # Replacing B must not erase the separately cached A evidence.
        self._signature_cache.pop(slot, None)
        self._signature_requests.discard(slot)
        self._signature_reading.pop(slot, None)
        getattr(self, '_manual_signature_reads', {}).pop(slot, None)
        had_generated_credentials = self._generated_credentials is not None
        self._generated_credentials = None
        if hasattr(self, 'generated_credentials_note') and had_generated_credentials:
            self.generated_credentials_note.setText("当前文档已变化。已生成资料保留在原输出目录；可重新选择自己的 P12/PFX 签署。")
        if hasattr(self, "signature_comparison"):
            self.signature_comparison.panes['B'].clear_generated_credentials()
            self.signature_comparison.update_slot(slot, self.w.docs.get(slot), None, self._signature_identity(slot))
        if hasattr(self, 'signature_workspace'):
            self.signature_workspace.panes['B'].clear_generated_credentials()
            self.signature_workspace.update_slot(slot, self.w.docs.get(slot), None, self._signature_identity(slot))

    def request_signature_reads(self):
        for slot in ("A", "B"):
            # 第三步自动流程：A 原稿直接检查；B 以生成完成的输出为检查输入。
            # 单独的人工验签功能仍可检查导入文件，不把“待生成”伪装成失败。
            identity = self._signature_identity(slot)
            # 分项第四步只签署；第五步/显式检测才读取结果，不把“尚未检查”误报成失败。
            if (slot == 'B' and identity is not None
                    and getattr(self, '_staged_check_pending_identity', None) == identity
                    and getattr(self, '_manual_signature_reads', {}).get(slot) != identity):
                self._signature_requests.discard(slot)
                continue
            manual = (identity is not None and
                      getattr(self, '_manual_signature_reads', {}).get(slot) == identity)
            if slot == 'B' and not manual and getattr(self, '_signed_output', None) != self.w.docs.get('B'):
                self._signature_requests.discard(slot)
                continue
            if identity is not None and self._cached_signature(slot) is None and self._signature_reading.get(slot) != identity:
                self._signature_requests.add(slot)
            else:
                self._signature_requests.discard(slot)
        if self._mode == 1 and self._signature_requests and not self._signature_timer.isActive():
            # 已有读取倒计时继续运行，界面刷新不能反复重置它。
            self._signature_timer.start(250)

    def _read_next_signature(self):
        if self._mode != 1 or not self._signature_requests:
            return
        if self.w._busy:
            self._signature_timer.start(500)
            return
        active = self.w.active_slot.currentText()
        slot = active if active in self._signature_requests else sorted(self._signature_requests)[0]
        self._signature_requests.discard(slot)
        path, identity = self.w.docs.get(slot), self._signature_identity(slot)
        if path is None or identity is None or self._cached_signature(slot):
            self.request_signature_reads()
            return
        self._signature_reading[slot] = identity
        self._refresh_signature_sources()
        if active == slot:
            self.w.signature_summary.setText(f"正在离线读取 {slot}：{path.name}；不会联网或自动签署。")
        from .signature_evidence import read_signature_audit
        def finish(result=None, error=None):
            try:
                if self._signature_identity(slot) == identity:
                    self._signature_cache[slot] = (identity, result, error)
                    self._refresh_signature_sources()
                    if self._mode == 1 and self.w.active_slot.currentText() == slot:
                        self.signature_slot_changed()
            finally:
                if self._signature_reading.get(slot) == identity:
                    self._signature_reading.pop(slot, None)
                # 显式检测只对应本次这份文件，完成后不延续到用户后来替换的 B。
                manual_reads = getattr(self, '_manual_signature_reads', {})
                if manual_reads.get(slot) == identity:
                    manual_reads.pop(slot, None)
                from .automatic_signing import read_completion_text
                message = (read_completion_text(slot, result, error) if self._signature_identity(slot) == identity
                           else f"{slot} 文件已变化，旧读取结果未应用。")
                self.w._notify(message)
                # Completion is explicit; the next side can still continue.
                self.request_signature_reads()
        self.w._task(f"离线读取 {slot} 数字签名", lambda: read_signature_audit(path, [], allow_network=False),
                     lambda result: finish(result=result), on_failed=lambda error: finish(error=error))

    def _display_signature_audit(self, result):
        w = self.w
        w._signature_audit = result
        w.signature_list.clear()
        for index, row in enumerate(result.get("signatures", [])):
            item = QListWidgetItem(f"#{index + 1} · {row.get('field', '')} · {row.get('overall_technical_result', '无法判断')}")
            item.setData(Qt.UserRole, index)
            w.signature_list.addItem(item)
        slot = w.active_slot.currentText()
        path = w.docs.get(slot)
        w.signature_summary.setText(
            f"{slot} · {path.name if path else ''} · {result['count']} 个数字签名\n"
            + ("验证未完成：" + result["validation_error"] if result.get("validation_error") else
               "发现未关联字段树的签名数据；详见证书、公钥及对象信息。" if result.get('unlinked_signature_count') else
               "此文件没有数字签名，也没有可展示的签署证书。修改预览不会自动签署。" if result['count'] == 0 else
               "证书声明、数学完整性和独立身份信任分别评估。")  # 限制：证书声明、数学完整性和身份信任分开评估
            + f"\n信任根：{result.get('trust_roots_supplied', 0)}；"
            + ("已授权联网" if result.get('network_fetching') else "仅离线，未联网检查吊销"))
        import json
        w.signature_details.setPlainText(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:100000])
        self.update_signature()

    def update_signature(self, row=None):
        # 显示层只消费当前侧自己的缓存，不把全局界面数据写回 A 或 B。
        # 原实现会将 w._signature_audit 按此刻 active_slot 再入库，存在串侧风险。
        slot = self.w.active_slot.currentText()
        cached = self._cached_signature(slot)
        audit = cached[1] if cached and not cached[2] else None
        if audit is None:
            self.w._signature_audit = None
            self._audit_owner = None
            self.certificate.clear()
            return
        self.w._signature_audit = audit
        self._audit_owner = (slot, cached[0])
        self._refresh_signature_sources()
        path = self.w.docs.get(slot)
        self.signature_context.setText(f"正在查看 {slot}：{path.name if path else '未打开'}")
        signatures = audit.get('signatures') or []
        # 点击事件携带的行也可能来自旧侧；仅接受当前报告中真实存在的那一行。
        if row is not None and any(row is current for current in signatures):
            self.certificate.set_signature(row)
        elif signatures:
            # 摘要也跟随当前信息/验证面板选中的签名，不另行固定为第一个签名。
            view = getattr(self, 'signature_comparison', None)
            entry = view.panes[slot]._selected() if view is not None else {}
            selected = next((current for current in signatures if current.get('field') == entry.get('field')),
                            signatures[-1])
            self.certificate.set_signature(selected)
        else:
            self.certificate.clear()
            entries = (audit.get('full_evidence') or {}).get('signatures') or []
            if entries:
                self.certificate.state.setText(f'已读取 {len(entries)} 项签名数据；完整字段验签未完成，详见当前侧依据')
            else:
                self.certificate.state.setText('验证未完成，不能断言无签名' if audit.get('validation_error')
                                               else '未发现数字签名；普通图章图片不等于数字签名')
            for value in self.certificate.values.values():
                value.setText('详见当前侧签名数据' if entries else
                              '未判断（验证未完成）' if audit.get('validation_error') else '不适用（此文件无数字签名）')

    def choose_signing_source(self):
        w = self.w
        reason = self._signing_reason()
        if reason:
            w._notify(reason)
            return None
        w.active_slot.setCurrentText("B")
        return w.docs["B"]

    def default_signed_output(self, source):
        original = self.w.docs.get("A")
        if original:
            return original.with_name(original.stem + "-edited-signed.pdf")
        return source.with_name(source.stem + "-signed.pdf")

    def _saved_signature_ready(self):
        session = getattr(self, '_signing_stage_session', None)
        folder = getattr(session, 'folder', None)
        return bool(session is not None and session.phase >= 3 and folder
                    and (Path(folder) / 'personal-signing.p12').is_file())

    def _export_signed_file(self):
        w = self.w
        session = self._signing_stage_session
        suggested = self.default_signed_output(w.docs['B']) if w.docs.get('B') else Path('signed.pdf')
        filename, _ = QFileDialog.getSaveFileName(w, '导出签署文件（不覆盖原稿）', str(suggested), 'PDF (*.pdf)')
        if not filename:
            return
        target = Path(filename)
        reason = signing_output_block_reason(target, w.docs.values())
        if reason:
            w._error(reason)
            return
        if self._signing_context_identity() != getattr(session, 'ui_context', None):
            w._error('A 或 B 已变化，请重新点击「生成新签名」保存数据后再导出。')
            return

        def sign():
            session.output = target
            session.sign_b()
            return target

        def completed(pdf):
            self.certificate_tools.set_phase(session.phase)
            w._notify(f'签署文件已导出：{pdf}。原稿和当前 B 未覆盖。')

        w._task('导出签署文件', sign, completed)

    def export_copy(self):
        w = self.w
        if w._busy:
            w._notify("后台操作尚未完成，请稍候再导出")
            return
        if w.workspace_tabs.currentIndex() == 0 and w.selection_editor.save_button.isEnabled():
            w.selection_editor.save_edit()
            return
        if self._saved_signature_ready():
            if w.workspace_tabs.currentIndex() != 2:
                w._notify('签名数据已保存，文件还没写。请检测 A / B，进入第 4 步后再点导出。')
                return
            self._export_signed_file()
            return
        source = w.docs.get("B")
        if source is None:
            w._notify("尚未生成工作副本；请先选择内容预览修改，或使用签署功能生成副本")
            return
        filename, _ = QFileDialog.getSaveFileName(w, "导出独立工作副本（不覆盖原稿）",
                                                  str(w.docs["A"].with_name(w.docs["A"].stem + "-edited-export.pdf")
                                                      if w.docs.get("A") else source.with_name(source.stem + "-export.pdf")), "PDF (*.pdf)")
        if not filename:
            return
        target = Path(filename)
        if target.exists() or target.resolve() in {p.resolve() for p in w.docs.values() if p is not None}:
            w._error("目标文件已经存在或是当前文档；请选择新的文件名。")
            return
        def copy_new():
            created = False
            try:
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    created = True
                    shutil.copyfileobj(incoming, outgoing)
            except BaseException:
                if created:
                    target.unlink(missing_ok=True)
                raise
            return str(target)
        w._task("导出独立工作副本", copy_new, lambda path: w._notify(f"工作副本已导出：{path}；原稿未覆盖"))
