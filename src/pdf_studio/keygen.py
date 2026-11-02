"""Create locally owned, encrypted PDF signing credentials.

Generates a self-signed end-entity certificate for personal testing/owned
document signatures. NEVER produces a trusted CA credential, nor claims to
issue certificates on behalf of any organization.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re

from .certificate_names import build_subject

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


ALGORITHMS = ("RSA-3072", "RSA-2048", "RSA-4096", "ECDSA-P256", "ECDSA-P384")


def _check_config(label: str, password: str, days_valid: int) -> None:
    # A 的 CN 可能包含姓名、机构和多段标识；不能用旧的 100 字符产品上限拦截。
    # 保留完整输入，具体证书编码交给 build_subject 和加密库处理，不自动截短名称。
    if not isinstance(label, str) or not label.strip():
        raise ValueError("签署者名称不能为空")
    if re.search(r"[\x00-\x1f\x7f]", label):
        raise ValueError("Personal label includes control characters")
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("Use a private-key password between 12 and 1024 characters")
    if not isinstance(days_valid, int) or not (1 <= days_valid <= 825):
        raise ValueError("Certificate lifetime must be between 1 and 825 days")


def _copied_serial(value) -> int:
    """只用 A 实际给出的正整数序列号。读不出来就失败，不改成随机数。"""
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise ValueError("A 的证书序列号必须是正整数")
        value = int(text)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("A 的证书序列号必须是正整数")
    if value.bit_length() >= 160:
        raise ValueError("A 的证书序列号超过 159 位，新证书无法原样写入")
    return value


def _aware_moment(value, name: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"A 的有效期{name}无法读取") from exc
    else:
        raise ValueError(f"A 的有效期{name}无法读取")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"A 的有效期{name}没有时区")
    return moment


def _validity_window(days_valid: int, not_valid_before, not_valid_after):
    """两端都来自 A 时原样使用，不受 1–825 天上限限制。缺一端不补造。"""
    if not_valid_before is None and not_valid_after is None:
        today = datetime.now(timezone.utc)
        return today - timedelta(minutes=5), today + timedelta(days=days_valid)
    if not_valid_before is None or not_valid_after is None:
        raise ValueError("A 的有效期必须同时有起点和终点")
    start = _aware_moment(not_valid_before, "起点")
    end = _aware_moment(not_valid_after, "终点")
    if end <= start:
        raise ValueError("A 的有效期终点必须晚于起点")
    return start, end


def _certificate_for_key(private_key, label: str, days_valid: int, subject_attributes=None, *,
                         serial_number=None, not_valid_before=None, not_valid_after=None) -> x509.Certificate:
    # 主体可以沿用 A 模板中的 O/OU 等属性；CN 使用 A 的原名，不追加程序标识。
    # 序列号和有效期在签署流程里沿用 A。公钥、证书签名、指纹和 SKI 仍由本次新密钥产生。
    # 签发者仍是持有人自己，不复制 A 的 CA 名称或 CA 签名。
    owner = build_subject(label, subject_attributes)
    serial = x509.random_serial_number() if serial_number is None else _copied_serial(serial_number)
    start, end = _validity_window(days_valid, not_valid_before, not_valid_after)
    return (
        x509.CertificateBuilder()
        .subject_name(owner)  # 限制：新证书主体写成持有人自己
        .issuer_name(owner)  # 限制：签发者也是持有人自己，所以是自签名，不是 CA 代签
        .public_key(private_key.public_key())
        .serial_number(serial)
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)  # 限制：新证书强制不是 CA
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,  # 限制：新证书不能签发其他证书，也不能签发吊销列表
                encipher_only=False, decipher_only=False,
            ), critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
        .sign(
            private_key,
            hashes.SHA384() if isinstance(private_key, ec.EllipticCurvePrivateKey)
            and isinstance(private_key.curve, ec.SECP384R1) else hashes.SHA256(),
        )
    )


def _store_credentials(folder: Path, cert: x509.Certificate, private_key,
                       password: str, encrypted_key: bytes | None = None, *,
                       store_password: bool = False, overwrite: bool = False) -> dict:
    secret = password.encode("utf-8")
    if encrypted_key is None:
        encrypted_key = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(secret),
        )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    encrypted_p12 = pkcs12.serialize_key_and_certificates(
        name=b"qpdf Studio Personal - self signed",
        key=private_key, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(secret),
    )
    payloads = (
        ("private-key-encrypted.pem", encrypted_key),
        ("public-key.pem", public_key),
        ("self-signed-certificate.pem", cert.public_bytes(serialization.Encoding.PEM)),
        ("personal-signing.p12", encrypted_p12),
    )
    if store_password:
        from .credential_passwords import PASSWORD_RECORD, protect_password
        # Protect first: DPAPI failure must not leave credentials with a lost password.
        payloads += ((PASSWORD_RECORD, protect_password(password, encrypted_p12)),)
    created_folder = False
    if overwrite:
        folder.mkdir(parents=True, exist_ok=True)
    else:
        folder.mkdir(parents=True, exist_ok=False)
        created_folder = True
    committed: list[Path] = []
    try:
        for filename, content in payloads:
            output = folder / filename
            flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            flags |= 0 if overwrite else os.O_EXCL
            if overwrite:
                flags |= os.O_TRUNC
            fd = os.open(output, flags, 0o600)
            committed.append(output)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
    except Exception:
        if not overwrite:
            for output in committed:
                output.unlink(missing_ok=True)
            if created_folder:
                folder.rmdir()
        raise
    return {
        "certificate_type": "self-signed end-entity; not a certificate authority",  # 限制：生成的是自签名终端证书，不是 CA
        "certificate_subject_claim": cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
        "certificate_sha256_fingerprint": cert.fingerprint(hashes.SHA256()).hex().upper(),
        "public_key_sha256_fingerprint": hashlib.sha256(
            private_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest().upper(),
        "valid_from": cert.not_valid_before_utc.isoformat(),
        "valid_until": cert.not_valid_after_utc.isoformat(),
        "generated_files": [str(folder / name) for name, _ in payloads],
        "private_key_protection": "encrypted PKCS#8 PEM and password-protected PKCS#12",
        "trust_warning": (  # 限制：自签名结果必须带上身份未验证说明
            "This self-signed certificate is NOT automatically trusted by other viewers; "  # 限制：自签名证书不会自动被其他阅读器信任
            "the subject label does NOT verify real-world issuer identity."  # 限制：证书上的名称不能证明真实签发身份
        ),
        "safety_note": "Never share private-key-encrypted.pem or personal-signing.p12, or their password.",
    }


def _new_private_key(algorithm: str):
    if algorithm == "RSA-2048":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if algorithm == "RSA-3072":
        return rsa.generate_private_key(public_exponent=65537, key_size=3072)
    if algorithm == "RSA-4096":
        return rsa.generate_private_key(public_exponent=65537, key_size=4096)
    if algorithm == "ECDSA-P256":
        return ec.generate_private_key(ec.SECP256R1())
    if algorithm == "ECDSA-P384":
        return ec.generate_private_key(ec.SECP384R1())
    raise ValueError(f"Unsupported key algorithm: {algorithm}")


def generate_personal_credentials(
    destination: Path,
    *,
    label: str,
    password: str,
    algorithm: str = "RSA-3072",
    days_valid: int = 365,
    store_password: bool = False,
    subject_attributes=None, prepared_key=None,
    serial_number=None, not_valid_before=None, not_valid_after=None,
) -> dict:
    """Generate encrypted PKCS#8 PEM + PKCS#12, public key and self-signed cert.

    The destination must be a new unused folder, to avoid overwriting any
    private key. Passwords are never written to logs, metadata or return values.
    Optional serial and validity come only from A's signer certificate. They do
    not replace the new public key, certificate signature, or fingerprint.
    """
    folder = Path(destination).resolve()
    if folder.exists():
        raise ValueError("Choose a new unused credential directory; existing keys will not be overwritten")  # 限制：证书必须写到新目录，不能覆盖已有密钥
    _check_config(label, password, days_valid)
    if algorithm not in ALGORITHMS:
        raise ValueError("Unsupported key algorithm")
    # 五阶段流程传入第二步生成的同一把密钥；旧调用仍可在此自动生成新密钥。
    private_key = prepared_key if prepared_key is not None else _new_private_key(algorithm)
    cert = _certificate_for_key(
        private_key, label, days_valid, subject_attributes,
        serial_number=serial_number, not_valid_before=not_valid_before, not_valid_after=not_valid_after,
    )
    return {"algorithm": algorithm, **_store_credentials(folder, cert, private_key, password, store_password=store_password)}


def reissue_personal_certificate(
    credential_folder: Path, destination: Path, *, label: str, password: str,
    days_valid: int = 365, store_password: bool = False,
) -> dict:
    """Create a new local certificate using the matching existing encrypted key.

    The input may be CA-issued or CA-capable. The local output remains a new
    self-signed end-entity certificate; it does not retain an issuer signature.
    The new certificate gets a different serial and fingerprint.
    Existing PDF signatures remain bound to their original certificate.
    """
    _check_config(label, password, days_valid)
    source = Path(credential_folder).resolve(strict=True)
    target = Path(destination).resolve()
    old_certificate = x509.load_pem_x509_certificate(
        (source / "self-signed-certificate.pem").read_bytes()
    )
    private_pem = (source / "private-key-encrypted.pem").read_bytes()
    if not private_pem.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise ValueError("Private key must be encrypted PKCS#8 PEM")
    private_key = serialization.load_pem_private_key(private_pem, password=password.encode("utf-8"))
    expected_public = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    existing_public = old_certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if expected_public != existing_public:
        raise ValueError("Certificate public key does not match this private key")
    if not isinstance(private_key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise ValueError("Unsupported private key algorithm for certificate signing")
    new_certificate = _certificate_for_key(private_key, label, days_valid)  # 限制：即使原证书由 CA 签发，新产出仍是自签名终端证书
    # 本机自动密码模式沿用已有 Windows 用户保护机制，供新 P12 后续签署时读取。
    written = _store_credentials(target, new_certificate, private_key, password, private_pem,
                                 store_password=store_password, overwrite=target.exists())
    return {
        **written,
        "operation": "reissue-certificate-with-existing-key",
        "previous_certificate_fingerprint": old_certificate.fingerprint(hashes.SHA256()).hex().upper(),
        "key_reused": True,
        "new_serial_number": str(new_certificate.serial_number),
        "important": "Previously signed PDFs remain signed by the OLD certificate; reissuing does not alter those PDF signatures.",
    }
