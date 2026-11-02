"""Native read-only, side-by-side A/B signature evidence viewer."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QStackedWidget, QTabWidget, QToolButton, QMenu, QVBoxLayout, QWidget,
)

from .signature_evidence import PRIVATE_KEY_NOTE
from .signature_card_data import default_signature_index, report_source_details
from .qt_evidence_section import EvidenceSection
from .signature_presentation import STATUS_TITLES, status_cells, status_explanation, coverage_description

PREVIEW_CHARACTERS = 300000
TABS = ("验证总览", "证书与证书链", "完整公钥", "覆盖与修订", "时间戳与属性", "原始签名数据")


def assessment(value):
    return "通过" if value is True else "未通过" if value is False else "未判断 / 不适用"


def signature_information(entry, audit, state):
    """Factual signature/container fields, separate from verification conclusions."""
    if not entry:
        return {'签名数据': state}
    return {
        # 扫描得到的显示名称不是 PDF 里真实存在的字段名，须明确区分。
        '签名字段': '未关联字段树' if entry.get('discovery') else entry.get('field'),
        '对象发现与关联情况': entry.get('discovery'),
        '签名对象引用': entry.get('signature_object_reference'),
        '签名类型 / SubFilter': audit.get('signature_subfilter'),
        '签署者证书': entry.get('signer_certificate'),
        '签署时间（声明）': audit.get('signing_date_claimed'),
        '签署原因（声明）': audit.get('reason_claimed'),
        '签署地点（声明）': audit.get('location_claimed'),
        '摘要算法': audit.get('digest_algorithm'),
        '签名算法': audit.get('signature_mechanism'),
        'ByteRange': entry.get('byte_range'),
        'CMS 长度': entry.get('cms_length'),
        'CMS SHA256': entry.get('cms_sha256'),
        'SignerInfo 与签名属性': entry.get('signer_infos', []),
        '读取错误': entry.get('errors'),
    }


class SignatureEvidencePane(QWidget):
    def __init__(self, slot, source_paths, *, information_only=False):
        super().__init__()
        self.information_only = information_only
        self.slot = slot
        self.source_paths = source_paths
        self.report = None
        self.generated_credentials = None
        self._identity = None
        self._rendered = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        heading = QLabel(("A · 原有签名与证书" if slot == "A" else "B · 签名与证书") if information_only else ("A · 验证结果" if slot == "A" else "B · 验证结果"))
        heading.setObjectName("Heading")
        layout.addWidget(heading)
        self.file_label = QLabel()
        self.file_label.setTextFormat(Qt.PlainText)
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.file_label)
        self.state = QLabel()
        self.state.setTextFormat(Qt.PlainText)
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.state)
        cards = QGridLayout()
        cards.setSpacing(5)
        self.status_cards = []
        # A/B 共用五项状态卡；不能因原稿/修改稿或发现路径不同而更换标准。
        for _ in STATUS_TITLES:
            card = QLabel()
            card.setObjectName('SignatureStatusCard')
            card.setWordWrap(True)
            card.setAlignment(Qt.AlignCenter)
            cards.addWidget(card, len(self.status_cards) // 2, len(self.status_cards) % 2)
            self.status_cards.append(card)
            card.setVisible(not information_only)  # 限制：信息模式只显示字段，不显示验证结论卡片
        layout.addLayout(cards)
        self.signatures = QComboBox()
        self.signatures.currentIndexChanged.connect(self._signature_changed)
        layout.addWidget(self.signatures)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.editors = []
        for title in TABS:
            text = EvidenceSection()
            self.editors.append(text)
            self.tabs.addTab(text, title)
        self.tabs.currentChanged.connect(self._render_tab)
        layout.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        self.export_button = QToolButton()
        self.export_button.setText("导出数据 ▾")
        menu = QMenu(self.export_button)
        self.export_json_action = menu.addAction('完整 JSON…', self.export_json)
        self.der_button = menu.addAction('当前 CMS DER…', self.export_der)
        self.export_button.setMenu(menu)
        self.export_button.setPopupMode(QToolButton.InstantPopup)
        row.addWidget(self.export_button)
        layout.addLayout(row)
        note = QLabel("字段可展开、复制；完整数据可主动导出。")
        note.setToolTip("完整数据指当前可解析范围；所有缺失、错误及上限均单列。\n" + PRIVATE_KEY_NOTE)
        note.setWordWrap(True)
        note.setObjectName("WorkflowHint")
        layout.addWidget(note)
        self.show_report(None, None, None)

    def show_report(self, path, cached, identity, preferred_field=None):
        token = (str(path), identity, id(cached[1]) if cached else None, str(cached[2]) if cached else None)
        if token == self._identity:
            return
        same_path = self._identity is not None and self._identity[0] == str(path)
        previous_field = self._selected().get('field') if same_path else None
        self.report = cached[1] if cached and not cached[2] else None
        filename = Path(path).name if path else "尚未打开此侧 PDF"
        self.file_label.setText(filename if len(filename) <= 34 else filename[:20] + '…' + filename[-10:])
        source = report_source_details(self.report)
        self.file_label.setToolTip((str(path) if path else '') +
                                  '\n实际读取：' + str(source['实际读取文件'] or '尚未读取') +
                                  '\n文件 SHA-256：' + str(source['实际文件_SHA256'] or '尚未计算'))
        self.signatures.blockSignals(True)
        self.signatures.clear()
        rows = self._entries()
        for row in rows:
            self.signatures.addItem(f"#{row.get('index', '?')} · {row.get('field') or '未命名签名'}")
        # 同一文件保留当前选择。换文件时，对侧正在看的字段这里也有，就显示同一条。
        selected_index = default_signature_index(rows, previous_field or preferred_field)
        self.signatures.setCurrentIndex(selected_index if rows else -1)
        self.signatures.blockSignals(False)
        self.signatures.setEnabled(bool(rows))
        self.export_button.setEnabled(self.report is not None)
        self._rendered.clear()
        for editor in self.editors:
            editor.clear()
        if cached and cached[2]:
            state = "读取失败，不能断言无签名：\n" + str(cached[2])  # 限制：读取失败时不能断言这份 PDF 没有签名
        elif path is None:
            state = "未打开 · 无验证结果"
        elif identity is None:
            state = "文件不可访问 · 无验证结论"
        elif self.report is None:
            state = "等待后台离线读取；未签署、未联网。"
        elif self.report.get('unlinked_signature_count'):
            # 先显示已发现的数据数量，避免验证器返回 0 时误报“没有签名”。
            state = f"发现 {len(rows)} 项签名数据，其中 {self.report['unlinked_signature_count']} 项未关联字段树；可查看证书和公钥。"
            if self.report.get('validation_error'):
                state += ' 部分解析或验证未完成，详见依据。'
        elif self.report.get("validation_error"):
            state = "验证未完成，不能断言无签名：\n" + self.report["validation_error"]  # 限制：验证未完成时不能断言没有签名
        elif (self.report.get("full_evidence") or {}).get("errors"):
            state = "结构读取不完整，详见原始数据中的 errors。"
        elif self.report.get("count") == 0 and not rows:
            state = "读取完成 · 未发现已有数字签名；仍可为 B 自动生成凭据并签署。"  # 限制：没读到已有数字签名时，仍允许为 B 另生成新签名
        else:
            state = f"读取到 {len(rows)} 项签名数据 · 字段验证器返回 {self.report.get('count', '未知')} 项；可逐个选择查看。"
        self._base_state = state
        self._signature_changed()
        # Failed rendering must remain retryable for the same source/report.
        self._identity = token

    def set_reading(self, reading):
        if self.report is not None:
            return
        self._base_state = ('正在后台离线读取，尚未形成结论。' if reading else
                            '等待后台离线读取，尚未形成结论。')
        self.state.setText(self._base_state)
        self.state.setToolTip(self._base_state)

    def set_generation_pending(self):
        """B 尚未生成时只说明流程状态，不显示借用 A 的通过或失败结论。"""
        if self.report is not None:
            return
        self._base_state = 'B 待生成新签名；生成完成后重新读取并检查最终文件。'
        self.state.setText(self._base_state)
        self.state.setToolTip(self._base_state)
        for card, title in zip(self.status_cards, STATUS_TITLES):
            card.setText(title + '\n待生成后检查')
            card.setProperty('tone', 'neutral')
            card.style().unpolish(card)
            card.style().polish(card)

    def set_check_pending(self):
        """第四环节已签署，第五环节尚未检查。不能写成正在读取、未签署或已经通过。"""
        if self.report is not None:
            return
        self._base_state = '已签署，尚未检查。请执行第 5 环节「检查与对照」，或点击「检测 A / B」。此状态不是通过，也不是失败。'
        self.state.setText(self._base_state)
        self.state.setToolTip(self._base_state)
        note = getattr(self, 'operation_note', None)
        if note is not None:
            note.setText('签署输出已载入；尚未检查，不能把本栏当成验证通过或失败。')
        for card, title in zip(self.status_cards, STATUS_TITLES):
            card.setText(title + '\n尚未检查')
            card.setProperty('tone', 'neutral')
            card.style().unpolish(card)
            card.style().polish(card)

    def show_display_error(self, error):
        self._identity = None
        self._rendered.clear()
        for editor in self.editors:
            editor.clear()
        detail = f"{type(error).__name__}: {str(error) or '该异常未提供说明'}"
        self.state.setText("签名信息显示失败；不是无签名，也不是禁止操作。\n" + detail)  # 限制：显示失败不等于这份 PDF 没有签名
        for card in self.status_cards:
            card.setText('显示失败\n结果未展示')
            card.setProperty('tone', 'neutral')
            card.style().unpolish(card)
            card.style().polish(card)

    def _entries(self):
        return ((self.report or {}).get("full_evidence") or {}).get("signatures", [])

    def set_generated_credentials(self, data):
        self.generated_credentials = data
        self._rendered.clear()
        self.tabs.setCurrentIndex(2)
        self._render_tab(2)

    def clear_generated_credentials(self):
        self.generated_credentials = None
        self._rendered.clear()
        self._render_tab(self.tabs.currentIndex())

    def _selected(self):
        entries = self._entries()
        index = self.signatures.currentIndex()
        return entries[index] if 0 <= index < len(entries) else {}

    def _audit(self):
        row = self._selected()
        # Manual validation without enrichment must still remain visible.
        if row.get("audit") is not None:
            return row["audit"]
        rows = (self.report or {}).get("signatures", [])
        return next((a for a in rows if a.get("field") == row.get("field")), {})

    def _signature_changed(self, *_):
        self._rendered.clear()
        audit = self._audit()
        self.state.setText(self._base_state)
        evidence = (self.report or {}).get('full_evidence') or {}
        for card, (title, value, tone) in zip(self.status_cards, status_cells(self.report, evidence, audit, self._selected())):
            card.setText(title + '\n' + value)
            card.setProperty('tone', tone)
            card.style().unpolish(card)
            card.style().polish(card)
            card.setToolTip(status_explanation(title, audit))  # 中文说明只解释该项，不混用身份、摘要和数学签名结论。
        self.der_button.setEnabled(bool(self._selected().get("cms_der_base64")))
        self._render_tab(self.tabs.currentIndex())

    def _section(self, index):
        report = self.report or {}
        evidence = report.get("full_evidence") or {}
        entry = self._selected()
        audit = self._audit()
        if not report and not (index == 2 and self.generated_credentials):
            return {"状态": self._base_state}
        if index == 0 and self.information_only:
            return {'文件读取依据': report_source_details(report),
                    **signature_information(entry, audit, self._base_state)}
        if index == 0:
            return {
                # 总览与状态卡调用同一投影，避免详情再次出现旧的笼统“未通过”。
                "文件读取依据": report_source_details(report),
                "统一检查结果": {name: value for name, value, _ in status_cells(self.report, evidence, audit, entry)},
                "读取状态说明": "完整仅指当前配置范围内的证据读取，不等于所有验签项目通过。",
                "签名结构检查": audit.get('signature_structure'),
                "覆盖范围说明": coverage_description(audit),
                "验证结论": audit.get("overall_technical_result", "未判断 / 不适用"),
                "信任根数量": report.get("trust_roots_supplied"), "允许联网": report.get("network_fetching", False),
                "吊销策略": audit.get("revocation_assessment", "未判断 / 不适用"),
                # 补充摘要结果单独展示，不冒充完整验签器的 intact/signature_valid。
                "对象发现与关联": entry.get('discovery'),
                "补充字节摘要对照": audit.get('supplemental_digest'),
                "本次验证全部字段": audit, "验证器错误": report.get("validation_error"),
                "证据读取错误": evidence.get("errors"), "当前签名错误": entry.get("errors"),
                "完整性上限": evidence.get("limits", []) + entry.get("limits", []),
                "SHA256": evidence.get("source_sha256"), "局限": report.get("limitations"),
            }
        if index == 1:
            return {"说明": evidence.get("certificate_bag_note"),
                    "签署证书": entry.get("signer_certificate"),
                    "全部内嵌证书": entry.get("embedded_certificates", []),
                    "验证器证书路径": audit.get("certificate_path"),
                    "已验证路径的完整证书": ((audit.get("validation_details") or {}).get("signature") or {}).get("validated_certificate_path"),
                    "DSS离线验证材料": evidence.get("dss_validation_store"),
                    "不是身份确认": "内嵌证书集合不保证完整或可信，不会仅凭主体名称认可签署方身份。"}  # 限制：内嵌证书和主体名称不能当成已验证身份
        if index == 2:
            return {"签署公钥": (entry.get("signer_certificate") or {}).get("public_key"),
                    "本次独立生成的公钥与证书_非PDF提取值": self.generated_credentials,
                    "全部内嵌证书公钥": [{"subject": c.get("subject"), "sha256": c.get("sha256"), "key": c.get("public_key")} for c in entry.get("embedded_certificates", [])],
                    "私钥": PRIVATE_KEY_NOTE}
        if index == 3:
            return {"ByteRange": entry.get("byte_range"), "签名对象引用": entry.get("signature_object_reference"),
                    "签名结构与Contents原始位置": audit.get('signature_structure'),
                    "签署修订索引_从0开始": entry.get("signed_revision_index_zero_based"),
                    "对象发现与关联": entry.get('discovery'),
                    "补充字节摘要对照": audit.get('supplemental_digest'),
                    "覆盖级别": audit.get("coverage"), "后续改动分类": audit.get("modification_level"),
                    "DocMDP": entry.get("docmdp_policy_claimed", audit.get("docmdp_policy_claimed")),
                    "FieldMDP": entry.get("fieldmdp_policy_claimed"), "ByteRange证据": audit.get("byte_range_evidence"),
                    "修订映射可信程度": audit.get("revision_mapping"), "修订全部字段": report.get("revisions"),
                    "原始字典_重构表示": entry.get("pdf_dictionary_reconstructed_not_source_bytes"),
                    "说明": "存在后续修订不自动等于恶意篡改；允许的改动需结合 DocMDP / FieldMDP 和差异策略判断。"}
        if index == 4:
            details = audit.get("validation_details") or {}
            return {"声称签署时间": audit.get("signing_date_claimed"),
                    "文档时间戳验证_若为DocTimeStamp": details.get("signature") if entry.get("document_timestamp_tst_info") else "不适用 / 未判断",
                    "签名时间戳验证结果": details.get("signature_timestamp", "未判断 / 不适用"),
                    "内容时间戳验证结果": details.get("content_timestamp", "未判断 / 不适用"),
                    "文档时间戳TSTInfo": entry.get("document_timestamp_tst_info"),
                    "全部SignerInfo及属性": entry.get("signer_infos", []),
                    "内嵌吊销数据_不等于已通过检查": entry.get("embedded_revocation_info"),
                    "说明": evidence.get("timestamp_note")}
        return {"数据层次说明": evidence.get("raw_note"), "CMS长度": entry.get("cms_length"),
                "CMS_SHA256": entry.get("cms_sha256"), "CMS_DER_Base64": entry.get("cms_der_base64"),
                "PDF_Contents原值_含填充_Base64": entry.get("pdf_contents_base64"),
                "PDF_Contents长度": entry.get("pdf_contents_length"), "PDF_Contents_SHA256": entry.get("pdf_contents_sha256"),
                "未解析签名容器_Base64": entry.get("unparsed_signature_contents_base64"),
                "PDF签名字典_不是磁盘原字节": entry.get("pdf_dictionary_reconstructed_not_source_bytes"),
                "对象扫描范围与进度": evidence.get('object_discovery'),
                "对象发现与关联": entry.get('discovery'),
                "CMS完整解析结构": entry.get("cms_structure"), "错误": entry.get("errors"),
                "限制": evidence.get("limits", []) + entry.get("limits", []),
                "未填写的签名字段_不等于已签名": evidence.get("empty_signature_fields", [])}  # 限制：空签名字段不等于已经签名

    def _render_tab(self, index):
        if index < 0 or index in self._rendered:
            return
        self.editors[index].set_payload(self._section(index))
        self._rendered.add(index)

    def _save(self, title, suffix, content):
        source = self.source_paths().get(self.slot)
        name = (source.stem if source else self.slot) + "-signature-evidence" + suffix
        destination, _ = QFileDialog.getSaveFileName(self, title, name, f"Evidence (*{suffix})")
        if not destination:
            return
        output = Path(destination)
        if output.resolve() in {p.resolve() for p in self.source_paths().values() if p is not None}:
            QMessageBox.warning(self, "保留 PDF 原件", "不能覆盖当前 A / B 源文件，请选择独立输出路径。")
            return
        if output.suffix.lower() != suffix:
            QMessageBox.warning(self, "输出格式", f"请使用 {suffix} 后缀，避免误写为 PDF。")
            return
        try:
            output.write_bytes(content)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def export_json(self):
        if self.report is not None:
            data = dict(self.report)
            if self.generated_credentials:
                data['generated_credentials_not_extracted_from_pdf'] = self.generated_credentials
            self._save("导出当前侧完整签名证据（不包含 PDF 正文）", ".json",
                       json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"))

    def export_der(self):
        value = self._selected().get("cms_der_base64")
        if value:
            self._save("导出选中签名的 CMS DER", ".der", base64.b64decode(value))


class SignatureEvidenceComparison(QWidget):
    """Right-inspector evidence browser; never replaces the PDF page previews."""
    slot_changed = Signal(str)

    def __init__(self, source_paths):
        super().__init__()
        self.setObjectName('SignatureInspector')
        self._comparison_token = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        self.source_selector = QComboBox()
        self.source_selector.addItems(['A · 原稿验证数据', 'B · 修改 / 签署输出验证数据', 'A / B · 验证对照'])
        self.source_selector.currentIndexChanged.connect(self._source_changed)
        layout.addWidget(self.source_selector)
        self.category = QComboBox()
        self.category.addItems(TABS)
        self.category.currentIndexChanged.connect(self._select_category)
        layout.addWidget(self.category)
        self.stack = QStackedWidget()
        self.panes = {s: SignatureEvidencePane(s, source_paths) for s in ('A', 'B')}
        for pane in self.panes.values():
            pane.tabs.tabBar().hide()
            pane.tabs.currentChanged.connect(self._select_category)
            self.stack.addWidget(pane)
        self.comparison = EvidenceSection()
        self.stack.addWidget(self.comparison)
        layout.addWidget(self.stack, 1)

    def _source_changed(self, index):
        self.stack.setCurrentIndex(index)
        if index < 2:
            self.slot_changed.emit('A' if index == 0 else 'B')
        else:
            self._render_comparison()

    def select_slot(self, slot):
        index = 0 if slot == 'A' else 1
        # Compare is an explicit inspector choice; refreshes must not collapse it.
        if self.source_selector.currentIndex() == 2:
            return
        self.source_selector.blockSignals(True)
        self.source_selector.setCurrentIndex(index)
        self.source_selector.blockSignals(False)
        self.stack.setCurrentIndex(index)

    def select_signature_field(self, slot, field):
        pane = self.panes[slot]
        rows = pane._entries()
        index = next((i for i, row in enumerate(rows) if row.get('field') == field), -1)
        if index >= 0 and pane.signatures.currentIndex() != index:
            pane.signatures.blockSignals(True)
            pane.signatures.setCurrentIndex(index)
            pane.signatures.blockSignals(False)
            pane._signature_changed()
            if self.stack.currentIndex() == 2:
                self._render_comparison()

    def show_slot(self, slot):
        self.source_selector.blockSignals(True)
        index = 0 if slot == 'A' else 1
        self.source_selector.setCurrentIndex(index)
        self.source_selector.blockSignals(False)
        self.stack.setCurrentIndex(index)

    def _select_category(self, index):
        if index < 0:
            return
        self.category.blockSignals(True)
        self.category.setCurrentIndex(index)
        self.category.blockSignals(False)
        for pane in self.panes.values():
            pane.tabs.blockSignals(True)
            pane.tabs.setCurrentIndex(index)
            pane.tabs.blockSignals(False)
            pane._render_tab(index)
        if self.stack.currentIndex() == 2:
            self._render_comparison()

    def _render_comparison(self):
        index = self.category.currentIndex()
        token = (index, tuple((pane._identity, pane.signatures.currentIndex(), id(pane.generated_credentials))
                              for pane in self.panes.values()))
        if token == self._comparison_token:
            return
        self._comparison_token = token
        self.comparison.set_payload({
            'A · 原稿': self.panes['A']._section(index),
            'B · 修改 / 签署输出': self.panes['B']._section(index),
        })

    def update_slot(self, slot, path, cached, identity, preferred_field=None):
        self.panes[slot].show_report(path, cached, identity, preferred_field)
        if self.stack.currentIndex() == 2:
            self._render_comparison()
