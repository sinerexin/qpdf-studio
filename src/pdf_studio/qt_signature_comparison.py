"""Compare original/new PDF digital signature identity evidence, side by side.

The app does not and cannot clone somebody else's public key. Displayed
identity fields are explicitly CERTIFICATE CLAIMS; trust requires independently
verified trust anchors, supplied separately for the original and output PDF.
"""
# 限制：不能复制别人的公钥，名称只是证书声明。
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .signature_profile import inspect_signature_profile


STATUS_LABELS = {
    "signature_valid_and_certificate_path_trusted": "签名数学有效 · 指定本地可信根验证通过",
    "cryptographically_valid_identity_unverified": "签名数学有效 · 身份信任尚未建立",  # 限制：数学有效不等于身份已验证
    "cryptographically_valid_but_certificate_not_trusted": "签名数学有效 · 未通过提供的可信根核验",  # 限制：证书未通过可信根时，不显示为身份已验证
    "cryptographic_integrity_or_signature_failed": "签名数学完整性或有效性验证失败",
    "inconclusive_validation_error": "签名验证结果不确定",
}


def format_signature_identity(report: dict, role: str) -> str:
    """Short, read-only summary of claims, fingerprints and independent trust."""
    lines = [
        f"{role} · {report['source']}",
        f"检测到的数字签名：{report['signature_count']} 个",
        f"显式提供可信根：{report['trust_roots_supplied']} 个",
    ]
    if not report["signatures"]:
        return "\n".join(lines + [
            "",
            "该 PDF 未检测到数字签名。",  # 限制：没有数字签名时不能给出身份结论
            "没有签名身份、证书指纹或可以成立的签名信任结论。",  # 限制：没有签名就不能成立信任结论
        ])
    for sig in report["signatures"]:
        lines.extend([
            "",
            f"签名 #{sig['index']}  ·  字段 {sig['field']}",
            "【证书主体声明（未经独立身份确认）】",  # 限制：证书主体只显示为声明，不是已确认身份
            sig.get("claimed_subject_read_only") or "不可用",
            "【证书签发者声明】",
            sig.get("claimed_issuer_read_only") or "不可用",
            "【证书 SHA-256 指纹】",
            sig.get("certificate_sha256") or "不可用",
            "【公钥 SHA-256 指纹】",
            sig.get("public_key_sha256") or "不可用",
            f"公钥规格：{sig.get('key_family') or '未知'} / "
            f"{sig.get('key_bits') or '未知'} 位 / "
            f"{sig.get('key_curve') or '—'}",
            f"签名机制：{sig.get('signature_mechanism') or '不可用'}",
            f"摘要算法：{sig.get('signature_digest') or '不可用'}",
            f"签名字节完整：{'是' if sig.get('cryptographically_intact') else '否/未确认'}",
            f"签名数学有效：{'是' if sig.get('mathematically_valid') else '否/未确认'}",
            "【信任状态（分别验证）】",
            STATUS_LABELS.get(sig.get("overall_technical_result"), "未知/尚未验证"),
        ])
    return "\n".join(lines)


def signature_diff_conclusion(left: dict, right: dict) -> str:
    if not left["signatures"] or not right["signatures"]:
        return (
            "至少一份 PDF 未检测到数字签名，无法比较两个签署者。"  # 限制：缺签名时不能比较签署者身份
        )
    a = left["signatures"][0]
    b = right["signatures"][0]
    same_algo = (
        a.get("key_family") is not None and
        a.get("key_family") == b.get("key_family") and
        a.get("key_bits") == b.get("key_bits") and
        a.get("key_curve") == b.get("key_curve")
    )
    same_key = (
        a.get("public_key_sha256") is not None and
        a.get("public_key_sha256") == b.get("public_key_sha256")
    )
    same_cert = (
        a.get("certificate_sha256") is not None and
        a.get("certificate_sha256") == b.get("certificate_sha256")
    )
    return (
        f"算法规格：{'相同' if same_algo else '不同'}    "
        f"公钥指纹：{'相同' if same_key else '不同'}    "
        f"证书指纹：{'相同' if same_cert else '不同'}\n"
        "算法一致并不代表签署者相同。请分别核实两份证书的身份、"  # 限制：算法相同不等于签署者相同
        "签名覆盖范围及独立可信根。"
    )


def show_signature_comparison(workbench) -> None:
    original = workbench.docs.get("A")
    produced = workbench.docs.get("B")
    if original is None or produced is None:
        workbench._notify("请先打开 A 原件和 B 新产出 PDF，再进行签名身份对照")
        return
    previous = getattr(workbench, "_signature_comparison_dialog", None)
    if previous is not None and previous.isVisible():
        previous.raise_()
        previous.activateWindow()
        return
    dialog = QDialog(workbench)
    workbench._signature_comparison_dialog = dialog
    dialog.setWindowTitle("原件 A / 新产出 B · 数字签名身份、指纹与信任对照")
    dialog.resize(1050, 690)
    layout = QVBoxLayout(dialog)
    explanation = QLabel(  # 限制：对照界面必须说明证书名称不是已验证身份
        "左侧为原件签名，右侧为新产出签名；两份文件分别执行离线数学与"  # 限制：两份签名分开验证，不互相继承
        "证书路径验证。主体名称来自证书声明，不能代表已验证真实机构身份。"  # 限制：证书上的名称只是自我声明，不能当成已验证的机构身份
        "可信根必须从独立可信渠道获取，不能把 PDF 自带证书直接当作可信机构证书。"  # 限制：PDF 自带证书不能直接当作可信根
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)

    root_paths: dict[str, Path | None] = {"A": None, "B": None}
    selects = QHBoxLayout()
    root_labels: dict[str, QLabel] = {}
    for slot, title in (("A", "原件"), ("B", "新产出")):
        selector = QHBoxLayout()
        button = QPushButton(f"{title} · 选择独立核实的可信根")
        indicator = QLabel("无可信根：身份未核实")  # 限制：未选择可信根时，身份显示为未核实
        indicator.setWordWrap(True)
        root_labels[slot] = indicator

        def choose_root(s=slot, caption=title):
            filename, _ = QFileDialog.getOpenFileName(
                dialog, f"{caption}可信根：请先独立核实来源",
                "", "Certificate (*.pem *.crt *.cer)",
            )
            if filename:
                root_paths[s] = Path(filename)
                root_labels[s].setText(Path(filename).name + "（用户提供）")

        def clear_root(s=slot):
            root_paths[s] = None
            root_labels[s].setText("无可信根：身份未核实")  # 限制：清除可信根后，身份回到未核实

        button.clicked.connect(choose_root)
        clear_button = QPushButton("清除")
        clear_button.clicked.connect(clear_root)
        selector.addWidget(button)
        selector.addWidget(clear_button)
        selector.addWidget(indicator, 1)
        selects.addLayout(selector, 1)
    layout.addLayout(selects)

    columns = QHBoxLayout()
    texts: dict[str, QPlainTextEdit] = {}
    for slot, title in (("A", "A 原件签名"), ("B", "B 新产出签名")):
        column = QVBoxLayout()
        heading = QLabel(title)
        column.addWidget(heading)
        viewer = QPlainTextEdit()
        viewer.setObjectName(f"SignatureProfile{slot}")
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        viewer.setPlainText("等待签名分析…")
        texts[slot] = viewer
        column.addWidget(viewer, 1)
        columns.addLayout(column, 1)
    layout.addLayout(columns, 1)
    conclusion = QLabel("请比较原件与新产出的身份、证书指纹、公钥指纹和信任状态。")
    conclusion.setObjectName("SignatureComparisonConclusion")
    conclusion.setWordWrap(True)
    layout.addWidget(conclusion)
    controls = QHBoxLayout()
    rerun = QPushButton("重新核验 A / B（离线）")
    dismiss = QPushButton("关闭")
    controls.addWidget(rerun)
    controls.addStretch(1)
    controls.addWidget(dismiss)
    layout.addLayout(controls)
    dismiss.clicked.connect(dialog.close)

    def run() -> None:
        if workbench._busy:
            return
        roots_a = [root_paths["A"]] if root_paths["A"] else []
        roots_b = [root_paths["B"]] if root_paths["B"] else []
        conclusion.setText("分别读取 A 与 B 的签名和证书信息，正在离线核验…")

        def completed(pair: tuple[dict, dict]) -> None:
            left, right = pair
            if workbench.docs.get("A") != original or workbench.docs.get("B") != produced:
                conclusion.setText("文件已切换，请重新打开对照窗口以避免显示过期身份信息。")
            else:
                texts["A"].setPlainText(format_signature_identity(left, "A 原件"))
                texts["B"].setPlainText(format_signature_identity(right, "B 新产出"))
                conclusion.setText(signature_diff_conclusion(left, right))

        def read_both() -> tuple[dict, dict]:
            return (
                inspect_signature_profile(original, trust_roots=roots_a),
                inspect_signature_profile(produced, trust_roots=roots_b),
            )

    # Runs under the existing Qt event loop; no nested modal loop is created.
    # Users can still navigate the A/B viewer while this comparison is open.
        workbench._task("A/B签名身份独立核验", read_both, completed)
    rerun.clicked.connect(run)
    QTimer.singleShot(0, run)
    dialog.show()
