"""Qt workflow for creating and reissuing personal self-signed credentials."""
from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout,
)

from .credential_passwords import new_password, load_managed_password
from .keygen import (
    ALGORITHMS, generate_personal_credentials, reissue_personal_certificate,
)


def open_personal_certificate_editor(
    workbench, *, reissue: bool = False, preferred_algorithm: str | None = None,
    on_created=None,
) -> None:
    source = None
    managed = False
    old_label = "Personal PDF signing test"
    if reissue:
        folder = QFileDialog.getExistingDirectory(
            workbench, "选择自己持有的个人密钥资料文件夹"
        )
        if not folder:
            return
        source = Path(folder)
        try:
            cert = x509.load_pem_x509_certificate(
                (source / "self-signed-certificate.pem").read_bytes()
            )
            names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            old_label = names[0].value if names else cert.subject.rfc4514_string()
            # 仅读取用户所选目录中本程序创建的密码记录，不搜索其他凭据。
            p12 = source / 'personal-signing.p12'
            managed = p12.is_file() and load_managed_password(p12) is not None
            public_key = cert.public_key()
            if isinstance(public_key, rsa.RSAPublicKey):
                existing_algorithm = f"RSA-{public_key.key_size}"
            elif isinstance(public_key, ec.EllipticCurvePublicKey):
                existing_algorithm = (
                    "ECDSA-P384" if isinstance(public_key.curve, ec.SECP384R1)
                    else "ECDSA-P256" if isinstance(public_key.curve, ec.SECP256R1)
                    else f"ECDSA-{public_key.curve.name}"
                )
            else:
                existing_algorithm = "已有密钥算法"
        except (OSError, ValueError, IndexError, RuntimeError) as error:
            workbench._error(f"无法读取输入证书：{error}")
            return

    dialog = QDialog(workbench)
    dialog.setWindowTitle(
        "使用已有密钥生成新证书（输入证书类型不限）" if reissue else
        "本机创建个人公钥、加密私钥及自签名证书"
    )
    dialog.resize(640, 450)
    layout = QVBoxLayout(dialog)
    explanation = QLabel(
        "自签名证书仅用于个人学习或自己有权签署的 PDF。"  # 限制：自签名证书只用于个人或有权签署的 PDF
        "填写的名称是自我声明，不能证明任何机构或 CA 的签发身份。\n"  # 限制：证书名称不能证明机构或 CA
        "已签发的证书不可原位改写；修改后重新签发到新目录，"  # 限制：已签发证书不能原地改写
        "旧证书和旧 PDF 签名都不会被改变。"  # 限制：重签不会改旧证书和旧 PDF 签名
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    form = QFormLayout()
    label = QLineEdit(old_label)
    form.addRow("个人证书显示名称（可编辑）", label)
    days = QSpinBox()
    days.setRange(1, 825)
    days.setValue(365)
    days.setSuffix(" 天")
    form.addRow("有效期限（可编辑）", days)
    algorithm = QComboBox()
    algorithm.addItems(list(ALGORITHMS))
    if not reissue and preferred_algorithm in ALGORITHMS:
        algorithm.setCurrentText(preferred_algorithm)
        algorithm.setToolTip(
            "仅根据原签名公钥的算法族、长度/曲线设置默认值。"
            "生成的是完全独立的新密钥对，不复制原签署者的身份、证书或公钥。"  # 限制：新密钥不复制原签署者的身份、证书或公钥
        )
    if reissue:
        if algorithm.findText(existing_algorithm) < 0:
            algorithm.addItem(existing_algorithm)
        algorithm.setCurrentText(existing_algorithm)
    algorithm.setEnabled(not reissue)
    form.addRow("新密钥算法" if not reissue else "密钥沿用原有算法", algorithm)
    if reissue:
        algorithm.setToolTip("重新签发证书沿用已存在的同一把私钥；更改密钥算法需生成新密钥对")
    password = QLineEdit()
    password.setEchoMode(QLineEdit.Password)
    password.setPlaceholderText("至少 12 个字符，不保存到报告")
    if reissue and not managed:
        form.addRow("已有密钥的解密密码", password)
    confirm = QLineEdit()
    confirm.setEchoMode(QLineEdit.Password)
    if reissue and not managed:
        form.addRow("确认密码", confirm)
    else:
        password_note = QLabel('已识别本机托管凭据，沿用原密码并自动读取。' if managed else
                              '密码由程序自动生成，并由当前 Windows 用户保护保存；本机再次签署时自动读取。')
        password_note.setWordWrap(True)
        form.addRow('凭据密码', password_note)
    destination = QLineEdit()
    destination.setPlaceholderText("请选择一个尚不存在的新文件夹路径")

    def choose() -> None:
        directory = QFileDialog.getExistingDirectory(dialog, "选择证书输出目录的上级文件夹")
        if directory:
            proposed = Path(directory) / (
                "qpdf-personal-certificate-reissued" if reissue else "qpdf-personal-certificate"
            )
            destination.setText(str(proposed))

    folder_row = QHBoxLayout()
    folder_row.addWidget(destination)
    browse = QPushButton("浏览")
    browse.clicked.connect(choose)
    folder_row.addWidget(browse)
    form.addRow("新输出路径", folder_row)
    layout.addLayout(form)
    actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    if dialog.exec() != QDialog.Accepted:
        return
    if reissue and not managed and password.text() != confirm.text():
        QMessageBox.warning(workbench, "密码不一致", "两个密码输入框不一致")
        return
    if not destination.text().strip():
        QMessageBox.warning(workbench, "尚未选择输出文件夹", "选择一个新的证书文件夹路径")
        return
    secret = password.text() if reissue and not managed else None
    password.clear()
    confirm.clear()
    target = Path(destination.text())
    new_label = label.text().strip()
    validity_days = days.value()
    selected_algorithm = algorithm.currentText()

    def task():
        nonlocal secret
        try:
            if reissue:
                # 密码仅在工作线程按需取出；新证书继续保存托管记录，不要求用户猜自动密码。
                secret = load_managed_password(source / 'personal-signing.p12') if managed else secret
                return reissue_personal_certificate(
                    source, target, label=new_label,
                    days_valid=validity_days, password=secret, store_password=managed,
                )
            return generate_personal_credentials(
                target, label=new_label, algorithm=selected_algorithm,
                days_valid=validity_days, password=new_password(), store_password=True,
            )
        finally:
            secret = None

    def completed(result: dict) -> None:
        safe = dict(result)
        if on_created is not None:
            try:
                on_created(target.resolve(), safe)
            except Exception as exc:
                workbench._notify(f"密钥已保存到 {target}，但公钥展示失败：{exc}")
        if on_created is None:
            workbench._json_output(
                "证书重新签发完成（仍为自签名）" if reissue else  # 限制：重新签发的结果仍标明是自签名
                "个人加密私钥和自签名证书创建完成", safe,
            )
        else:
            workbench._notify(f"B 的独立公钥、加密私钥和自签证书已保存：{target}；尚未签入 PDF。")
        QMessageBox.information(
            workbench, "本地证书已保存",
            f"生成目录：{target}\n" +
            ("请保留整个凭据目录；自动密码仅限生成时的 Windows 用户读取。\n" if not reissue else
             "请妥善保管加密私钥、P12/PFX 和密码；请勿上传或发送给他人。\n") +
            "自签名证书不会自动获得外部软件或 CA 的信任。"  # 限制：自签名证书不会自动获得外部软件或 CA 信任
        )

    workbench._task(
        "重新签发个人自签名证书" if reissue else "生成个人加密密钥和证书",
        task, completed,
    )
