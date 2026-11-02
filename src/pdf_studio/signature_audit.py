"""Comprehensive, conservative PDF digital-signature assessment.

We report separate conclusions for byte integrity, CMS verification, CA trust
and later revisions. The claimed certificate subject is never treated as proof
of a bank, issuer or institutional identity without a verified trust path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyhanko.pdf_utils.reader import PdfFileReader

from .revisions import inspect_revisions
from .signing import verify_document


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def audit_signatures(source: Path, roots: list[Path] | None = None,
                     *, allow_network: bool = False,
                     require_revocation: bool = False) -> dict[str, Any]:
    """Check signatures, trust and revision evidence with explicit options.

    Network retrieval is OFF unless opted in, and cannot be used without
    explicit trusted roots. Even a trusted certificate does not prove a PDF's
    business data was accurate or that a named organization authored it.
    """
    src = Path(source).resolve(strict=True)
    roots = roots or []
    if allow_network and not roots:
        raise ValueError("Network trust verification requires independently verified trust roots")
    if require_revocation and not roots:
        raise ValueError("Strict revocation assessment requires explicit trust roots")
    basic = verify_document(
        src, roots, allow_fetching=allow_network,
        revocation_mode="hard-fail" if require_revocation else "soft-fail",
    )
    evidence = inspect_revisions(src)
    today = datetime.now(timezone.utc)
    enhanced = []
    with src.open("rb") as stream:
        reader = PdfFileReader(stream)
        for index, signed in enumerate(reader.embedded_signatures):
            row = dict(basic["signatures"][index])
            cert = signed.signer_cert
            row["signature_index"] = index + 1
            row["signature_subfilter"] = None
            row["signing_date_claimed"] = None
            row["docmdp_policy_claimed"] = None
            try:
                sig_object = signed.sig_object
                row["signature_subfilter"] = str(sig_object.get("/SubFilter", ""))
                row["signing_date_claimed"] = str(sig_object.get("/M", "")) or None
                row["reason_claimed"] = str(sig_object.get("/Reason", "")) or None
                row["location_claimed"] = str(sig_object.get("/Location", "")) or None
            except Exception as exc:
                row["pdf_signature_metadata_error"] = f"{type(exc).__name__}: {exc}"
            try:
                row["docmdp_policy_claimed"] = str(signed.docmdp_level)
            except Exception:
                pass
            if cert is not None:
                validity = cert["tbs_certificate"]["validity"]
                beginning = validity["not_before"].native
                ending = validity["not_after"].native
                row["certificate_validity"] = {
                    "not_before": _date_text(beginning),
                    "not_after": _date_text(ending),
                    "currently_within_dates": bool(beginning <= today <= ending),
                    "caution": "Current certificate dates are not the historical signing-time trust evaluation",
                }
                row["self_issued_subject"] = cert.subject == cert.issuer
            matching = next((
                item for item in evidence["byte_ranges"]
                if item.get("structurally_plausible") and
                item.get("object_ref") == str(getattr(signed, "sig_object_ref", ""))
            ), None)
            if matching is None and index < len(evidence["byte_ranges"]):
                # Fallback is only an approximate match; no canonical link is
                # inferred from it for a signature integrity conclusion.
                matching = evidence["byte_ranges"][index]
                row["revision_mapping"] = "positional_only_not_authoritative"
            if matching:
                row["byte_range_evidence"] = matching
                row["additional_bytes_after_signed_revision"] = matching.get("bytes_after_signed_revision")
            if "verification_error" in row:
                result = "inconclusive_validation_error"
            elif not row.get("intact") or not row.get("signature_valid"):
                result = "cryptographic_integrity_or_signature_failed"
            elif not roots:  # 限制：调用方没有单独提供可信根
                result = "cryptographically_valid_identity_unverified"  # 限制：没有单独提供可信根时，签名即使数学有效也不记为身份已验证
            elif row.get("signer_trusted"):
                result = "signature_valid_and_certificate_path_trusted"
            else:  # 限制：提供了可信根，但证书路径没有通过
                result = "cryptographically_valid_but_certificate_not_trusted"  # 限制：证书未通过所提供的可信根时，不记为身份已验证
            row["overall_technical_result"] = result
            row["revocation_assessment"] = (
                "strict_policy_requested" if require_revocation else
                "online_fetch_opted_in_soft_fail" if allow_network else
                "offline_only_no_online_revocation"
            )
            enhanced.append(row)
    return {
        "schema": "qpdf-studio/signature-audit-v1",
        "source": src.name,
        "count": len(enhanced),
        "trust_roots_supplied": len(roots),
        "network_fetching": allow_network,
        "strict_revocation_policy": require_revocation,
        "signatures": enhanced,
        "revisions": {
            "chain_status": evidence["chain_status"],
            "revision_chain": evidence["revision_chain"],
            "eof_marker_offsets": evidence["eof_marker_offsets"],
        },
        "limitations": [  # 限制：验签结果必须附带这些身份和信任边界
            "Cryptographic validity is separate from signer identity and CA trust",  # 限制：数学签名有效和证书身份信任分开记录
            "A certificate subject string can be self-asserted",  # 限制：证书主体文字可以是自我声明
            "No online OCSP or CRL fetching occurs without explicit opt-in",  # 限制：未明确允许时不联网查询吊销
            "Legacy/unsupported PDF signatures may have incomplete results",  # 限制：旧式或不支持的签名结果可能不完整
            "Signed revisions and later permissible changes require independent policy review",  # 限制：签名后的后续改动需要单独审查
            "This does not authenticate the truth of transaction or business content",  # 限制：验签不证明业务内容真实
        ],
    }
