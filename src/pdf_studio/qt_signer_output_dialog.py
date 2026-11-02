"""Guided signer-name change using OWN credentials, then produce a signed PDF.

This is intentionally separate from the PDF content-editing screen.
"""
from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from .reissue_and_sign import reissue_signer_and_sign
from .credential_passwords import load_managed_password


def open_signer_output_dialog(workbench) -> None:
    pdf = workbench._need_source()
    if pdf is None:
        return
    folder = QFileDialog.getExistingDirectory(
        workbench, "选择证书与对应加密私钥资料夹"
    )
    if not folder:
        return
    existing = Path(folder)
    managed = False
    try:
        cert = x509.load_pem_x509_certificate(
            (existing / "self-signed-certificate.pem").read_bytes()
        )
        names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        present = names[0].value if names else cert.subject.rfc4514_string()
        if not (existing / "private-key-encrypted.pem").exists():
            raise ValueError("Missing original encrypted personal private key")
        # 对本程序生成的资料沿用 Windows 托管密码；外部资料仍由用户输入。
        p12 = existing / 'personal-signing.p12'
        managed = p12.is_file() and load_managed_password(p12) is not None
    except (OSError, ValueError, IndexError, RuntimeError) as exc:
        workbench._error(f"无法读取证书或对应私钥文件：{exc}")
        return

    dialog = QDialog(workbench)
    dialog.setWindowTitle("数字签名 · 使用已有密钥签发新证书并签署 PDF")
    dialog.resize(660, 400)
    layout = QVBoxLayout(dialog)
    explanation = QLabel(
        "第一步：修改你自己证书中的签署者名称（自我声明）；"  # 限制：新名称只是自我声明
        "第二步：使用对应私钥生成新的本地自签证书（输入不限于自签证书）；"
        "第三步：使用新证书对当前 PDF 添加新签名并另存。"
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    form = QFormLayout()
    form.addRow("待签署 PDF（只读）", QLabel(pdf.name))
    form.addRow("自己原证书的签署者", QLabel(present))
    signer = QLineEdit(present)
    signer.setObjectName("PersonalNewSignerName")
    signer.setToolTip("只能填写本人或有权代表的身份；此名称不会自动获得 CA 认证")  # 限制：填写的名称不会自动获得 CA 认证
    form.addRow("新签署者名称（可编辑）", signer)
    validity = QSpinBox()
    validity.setRange(1, 825)
    validity.setValue(365)
    validity.setSuffix(" 天")
    form.addRow("新证书有效期", validity)
    password = QLineEdit()
    password.setObjectName("ExistingPrivateKeyPassword")
    password.setEchoMode(QLineEdit.Password)
    password.setPlaceholderText("已有个人私钥的解密密码")
    if managed:
        form.addRow("原私钥密码", QLabel('已识别本机托管凭据，签署时自动读取。'))
    else:
        form.addRow("原私钥密码", password)
    credential_target = QLineEdit(
        str(pdf.parent / (pdf.stem + "-new-personal-signer"))
    )
    credential_target.setObjectName("NewCredentialDirectory")
    form.addRow("证书文件夹", credential_target)
    saved_file = QLineEdit(str(pdf.with_name(pdf.stem + "-signed-by-new-signer.pdf")))
    saved_file.setObjectName("NewSignedOutput")
    choose = QPushButton("浏览")
    row = QHBoxLayout()
    row.addWidget(saved_file)
    row.addWidget(choose)

    def choose_output() -> None:
        filename, _ = QFileDialog.getSaveFileName(
            dialog, "选择新的签名 PDF 输出路径", saved_file.text(),
            "PDF (*.pdf)",
        )
        if filename:
            saved_file.setText(filename)

    choose.clicked.connect(choose_output)
    form.addRow("新签名 PDF 输出位置", row)
    layout.addLayout(form)
    actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    actions.button(QDialogButtonBox.Save).setText("重新签发并签署新 PDF")
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    if dialog.exec() != QDialog.Accepted:
        return
    label = signer.text().strip()
    new_dir = Path(credential_target.text())
    output = Path(saved_file.text())
    secret = None if managed else password.text()
    days_valid = validity.value()
    password.clear()
    if not label or (not managed and not secret) or not str(new_dir).strip() or not str(output).strip():
        workbench._error("请填写自己新的签署者名称、私钥密码、新证书目录及新 PDF 路径")
        return

    def completed(result: dict) -> None:
        workbench.load_path("B", output)
        workbench.active_slot.setCurrentText("B")
        if hasattr(workbench, 'studio_shell'):
            # 重签输出也回到本轮第三步的实际 B 信息/验证区，不停留在旧版隐藏详情页。
            shell = workbench.studio_shell
            shell._select_signing_value('credential', new_dir / 'personal-signing.p12')
            shell.signed_output_completed(pdf, output)
        else:
            workbench.right_tabs.setCurrentWidget(workbench.signature_view)
            workbench.show_advanced_inspector(
                workbench.right_tabs.indexOf(workbench.signature_view)
            )
        workbench._json_output("修改个人签署者并生成新数字签名 PDF", result)
        workbench._notify(
            f"新签署者：{result['new_signer_claim']}。"
            "新 PDF 已加载到 B，使用「验签」和「A/B 签名身份对照」核验。"
        )
        QMessageBox.information(
            workbench, "新数字签名 PDF 已生成",
            f"新签署者：{result['new_signer_claim']}\n"
            f"新 PDF：{output}\n"
            f"新证书：{new_dir}\n"
            "请妥善保管加密私钥和 P12。"
            "自签名身份默认不被第三方信任。",  # 限制：自签名身份默认不是第三方已信任身份
        )

    def task():
        nonlocal secret
        try:
            # 主线程已冻结有效期；工作线程只读取凭据和处理文件，不访问 Qt 控件。
            secret = load_managed_password(existing / 'personal-signing.p12') if managed else secret
            return reissue_signer_and_sign(pdf, existing, new_dir, output,
                signer_label=label, password=secret, days_valid=days_valid, store_password=managed)
        finally:
            secret = None

    workbench._task("重新签发个人证书并对新 PDF 签名", task, completed)
