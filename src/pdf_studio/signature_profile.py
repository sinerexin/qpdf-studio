"""Read-only comparison of signature crypto parameters, not signer imitation.

Digital signatures bind to a specific public key and PDF byte range. Creating
a new keypair of the same algorithm cannot reproduce the original signer's
public key, certificate trust or signature. This module never copies certificate
subjects, serial numbers, identifiers, appearances or cryptographic key data.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pyhanko.pdf_utils.reader import PdfFileReader

from .keygen import ALGORITHMS
from .signature_audit import audit_signatures


def _key_details(cert: x509.Certificate) -> dict:
    public = cert.public_key()
    key_bytes = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    base: dict = {
        "public_key_sha256": hashlib.sha256(key_bytes).hexdigest().upper(),
        "certificate_sha256": cert.fingerprint(hashes.SHA256()).hex().upper(),
    }
    if isinstance(public, rsa.RSAPublicKey):
        algorithm = f"RSA-{public.key_size}"
        base.update(
            {"key_family": "RSA", "key_bits": public.key_size,
             "key_curve": None}
        )
    elif isinstance(public, ec.EllipticCurvePublicKey):
        curve = public.curve
        if isinstance(curve, ec.SECP256R1):
            algorithm = "ECDSA-P256"
        elif isinstance(curve, ec.SECP384R1):
            algorithm = "ECDSA-P384"
        else:
            algorithm = None
        base.update({"key_family": "ECDSA", "key_bits": public.key_size,
                     "key_curve": curve.name})
    else:
        algorithm = None
        base.update({"key_family": type(public).__name__, "key_bits": None,
                     "key_curve": None})
    base["supported_matching_algorithm"] = algorithm if algorithm in ALGORITHMS else None
    return base


def inspect_signature_profile(pdf: Path, *, trust_roots: list[Path] | None = None) -> dict:
    """Extract signed PDF public parameters and crypto verification evidence.

    Exposes fingerprints for identifying DIFFERENCES, not for copying them to
    generated certificates. All identity assertions are read-only evidence.
    """
    source = Path(pdf).resolve(strict=True)
    roots = trust_roots or []
    report = audit_signatures(source, roots)
    signatures = []
    with source.open("rb") as stream:
        reader = PdfFileReader(stream)
        for index, signed in enumerate(reader.embedded_signatures):
            audit = report["signatures"][index]
            cert = signed.signer_cert
            profile: dict = {
                "index": index + 1,
                "field": signed.field_name,
                "cryptographically_intact": bool(audit.get("intact", False)),
                "mathematically_valid": bool(audit.get("signature_valid", False)),
                "identity_trusted_without_explicit_root": False,  # 限制：没有明确可信根时，不把身份记为已信任
                "trust_roots_supplied": len(roots),
                "trust_path_validated": bool(audit.get("signer_trusted")) if roots else False,  # 限制：未提供可信根时，信任路径不算通过
                "overall_technical_result": audit.get("overall_technical_result"),
                "trust_state_description": {
                    "signature_valid_and_certificate_path_trusted":
                        "Signature valid; supplied local trust anchors validate certificate path",
                    "cryptographically_valid_identity_unverified":  # 限制：没有可信根时使用这个状态
                        "Signature mathematically valid; signer identity is NOT independently trusted",  # 限制：数学有效不等于身份已验证
                    "cryptographically_valid_but_certificate_not_trusted":  # 限制：可信根未通过时单独记录
                        "Signature mathematically valid; supplied anchors do NOT trust certificate",  # 限制：证书未通过所提供的可信根时，不记为身份已验证
                    "cryptographic_integrity_or_signature_failed":
                        "Signature or signed revision integrity failed",
                    "inconclusive_validation_error": "Signature verification inconclusive",
                }.get(audit.get("overall_technical_result"), "Unknown signature status"),
                "signature_digest": audit.get("digest_algorithm"),
                "signature_mechanism": audit.get("signature_mechanism"),
                "byte_range": audit.get("byte_range_evidence", {}).get("byte_range"),
            }
            if cert is not None:
                converted = x509.load_der_x509_certificate(cert.dump())
                profile.update(_key_details(converted))
                profile["claimed_subject_read_only"] = converted.subject.rfc4514_string()
                profile["claimed_issuer_read_only"] = converted.issuer.rfc4514_string()
                profile["self_signed_claim"] = converted.subject == converted.issuer
            else:
                profile.update(
                    {"key_family": None, "key_bits": None, "key_curve": None,
                     "public_key_sha256": None, "certificate_sha256": None,
                     "supported_matching_algorithm": None}
                )
            proposed = profile["supported_matching_algorithm"]
            profile["compatible_personal_key_algorithm"] = (
                proposed if proposed and profile["cryptographically_intact"]
                and profile["mathematically_valid"] else None
            )
            profile["matching_scope"] = (
                "Algorithm family and parameter size only. Newly generated public key, "
                "certificate fingerprint, claimed identity, trust and PDF signature "
                "are distinct and cannot be made equivalent to the original signer."  # 限制：新公钥和证书不能等同于原签署者
            )
            signatures.append(profile)
    return {
        "schema": "qpdf-studio/signature-crypto-profile-v1",
        "source": source.name,
        "signature_count": len(signatures),
        "trust_roots_supplied": len(roots),
        "signatures": signatures,
        "warning": (
            "This is read-only signature analysis. A matching algorithm does NOT "
            "recreate the original private/public key, signing identity or CA trust."  # 限制：算法相同不能重建原密钥、原身份或 CA 信任
        ),
    }


def compare_signature_parameters(original: Path, newly_signed: Path,
                                 *, original_index: int = 1,
                                 new_index: int = 1,
                                 original_roots: list[Path] | None = None,
                                 new_roots: list[Path] | None = None) -> dict:
    """Compare signatures without suggesting the new signature is the original."""
    before = inspect_signature_profile(original, trust_roots=original_roots)
    after = inspect_signature_profile(newly_signed, trust_roots=new_roots)
    try:
        a = before["signatures"][original_index - 1]
        b = after["signatures"][new_index - 1]
    except IndexError as error:
        raise ValueError("Both documents must contain the selected real PDF signature") from error
    if original_index < 1 or new_index < 1:
        raise ValueError("Signature index must start at 1")
    exact_params = (
        a["key_family"] is not None and a["key_family"] == b["key_family"]
        and a["key_bits"] == b["key_bits"]
        and a["key_curve"] == b["key_curve"]
    )
    return {
        "schema": "qpdf-studio/signer-parameter-comparison-v1",
        "original": a, "new": b,
        "same_algorithm_parameters": exact_params,
        "same_public_key": bool(
            a["public_key_sha256"] and
            a["public_key_sha256"] == b["public_key_sha256"]
        ),
        "same_certificate_fingerprint": bool(
            a["certificate_sha256"] and
            a["certificate_sha256"] == b["certificate_sha256"]
        ),
        "new_signature_cryptographically_valid": (
            b["cryptographically_intact"] and b["mathematically_valid"]
        ),
        "conclusion": (
            "Similar algorithm parameters do not mean the same public key, "
            "same signer identity, issuer, CA trust or original digital signature."  # 限制：算法参数相同不等于同一签署者、签发者或原签名
        ),
    }
