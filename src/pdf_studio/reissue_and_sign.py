"""Reissue the user's OWN self-signed identity and sign a PDF output.

This workflow starts from a PDF, reissues a certificate using an owned,
password-protected private key, then signs the chosen PDF path.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf

from .keygen import reissue_personal_certificate
from .signature_profile import inspect_signature_profile
from .signing import sign_document


def _digest(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reissue_signer_and_sign(
    source_pdf: Path,
    existing_personal_credentials: Path,
    new_credentials_directory: Path,
    signed_output: Path,
    *,
    signer_label: str,
    password: str,
    days_valid: int = 365, store_password: bool = False,
) -> dict:
    """Change user's personal certificate display name and produce signed PDF.

    New certificate retains the caller's OWN key: public key fingerprint
    therefore remains equal, while X.509 fingerprint/serial and the new PDF
    signature differ. For an entirely NEW key pair use generate-keys.
    """
    src = Path(source_pdf).resolve(strict=True)
    old = Path(existing_personal_credentials).resolve(strict=True)
    fresh = Path(new_credentials_directory).resolve()
    target = Path(signed_output).resolve()
    if not src.is_file() or not old.is_dir():
        raise ValueError("Provide a real PDF and an owned personal credential directory")
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF signing requires password support")  # 限制：加密 PDF 的签署需要密码支持，当前不提供
    source_hash = _digest(src)
    old_cert_hash = _digest(old / "self-signed-certificate.pem")
    renewed = reissue_personal_certificate(
        old, fresh, label=signer_label, password=password, days_valid=days_valid,
        store_password=store_password,
    )
    new_certificate_path = fresh / "self-signed-certificate.pem"
    new_p12 = fresh / "personal-signing.p12"
    if not new_certificate_path.exists() or not new_p12.exists():
        raise ValueError("Reissue did not produce the required private credentials")
    if renewed["previous_certificate_fingerprint"] == renewed["certificate_sha256_fingerprint"]:
        raise ValueError("New self-signed certificate must have a fresh fingerprint")
    sign_document(src, target, pfx=new_p12, password=password.encode("utf-8"))
    try:
        verified = inspect_signature_profile(target)
        matching = [item for item in verified["signatures"]
                    if item.get("certificate_sha256") == renewed["certificate_sha256_fingerprint"]]
        if len(matching) != 1:
            raise ValueError("New signature could not be uniquely identified")
        first = matching[0]
        if (not first["cryptographically_intact"] or not first["mathematically_valid"]
                or first["certificate_sha256"] != renewed["certificate_sha256_fingerprint"]):
            raise ValueError("New PDF signature did not validate against the newly reissued certificate")
        if signer_label.strip() not in first["claimed_subject_read_only"]:
            raise ValueError("Signed PDF signer name does not match the chosen self-signed certificate")
        if target != src and _digest(src) != source_hash:
            raise ValueError("An original file changed during signing; aborting output")
        if fresh != old and _digest(old / "self-signed-certificate.pem") != old_cert_hash:
            raise ValueError("An original file changed during signing; aborting output")
    except Exception:
        if target != src:
            target.unlink(missing_ok=True)
        raise
    report = {
        "operation": "reissue-owned-personal-signer-and-produce-new-PDF-signature",
        "source_pdf": src.name, "source_sha256_unchanged": source_hash,
        "signed_pdf": target.name, "signed_pdf_sha256": _digest(target),
        "reissued_credentials_folder": fresh.name,
        "new_signer_claim": first["claimed_subject_read_only"],
        "old_certificate_sha256": renewed["previous_certificate_fingerprint"],
        "new_certificate_sha256": first["certificate_sha256"],
        "new_public_key_sha256": first["public_key_sha256"],
        "own_private_key_reused": True,
        "new_signature_mathematically_valid": True,
        "new_identity_trust": first["overall_technical_result"],
        "warning": (
            "New signer display name is SELF-DECLARED and not CA-verified. "  # 限制：新签署者名称是自我声明，不是 CA 已验证
            "Reusing the same owned private key preserves the public key (it is NOT "  # 限制：复用的是本人已有私钥，不是新密钥
            "a newly generated keypair)."  # 限制：公钥保持原密钥对，不是新生成的密钥对
        ),
    }
    return report
