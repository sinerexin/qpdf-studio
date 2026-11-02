"""Standards-based PDF signing with a user-controlled private key, never simulated CA identity."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from pyhanko.keys import load_cert_from_pemder
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers, fields, validation
from pyhanko_certvalidator import ValidationContext

def sign_document(source: Path, target: Path, *, pfx: Path | None = None, cert: Path | None = None,
                  key: Path | None = None, password_env: str | None = None,
                  password: bytes | None = None, digest_algorithm: str = "sha256") -> Path:
    source = source.resolve(strict=True)
    target = target.resolve()
    if bool(pfx) == bool(cert and key) or (pfx and (cert or key)):
        raise ValueError("Supply either --pfx or both --cert and --key")
    if password_env and password is not None:
        raise ValueError("Specify either a password value or environment variable, not both")
    secret = password
    if password_env:
        if password_env not in os.environ:
            raise ValueError("Specified password environment variable is not set")
        secret = os.environ[password_env].encode("utf-8")
    if pfx:
        signer = signers.SimpleSigner.load_pkcs12(str(pfx.resolve(strict=True)), passphrase=secret)
    else:
        signer = signers.SimpleSigner.load(key_file=str(key.resolve(strict=True)),
                                            cert_file=str(cert.resolve(strict=True)),
                                            key_passphrase=secret)
    if signer is None:
        raise ValueError("Unable to load signing credentials")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".qpdf-signing-tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        with source.open("rb") as inp, tmp.open("xb") as out:
            writer = IncrementalPdfFileWriter(inp)
            existing_fields = {name for name, _, _ in fields.enumerate_sig_fields(writer)}
            number = 1
            while f"StudioSignature{number}" in existing_fields:
                number += 1
            field_name = f"StudioSignature{number}"
            signers.sign_pdf(writer, signers.PdfSignatureMetadata(
                field_name=field_name, md_algorithm=digest_algorithm),
                signer=signer, new_field_spec=fields.SigFieldSpec(sig_field_name=field_name), output=out)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target

def verify_document(source: Path, roots: list[Path] | None = None,
                    *, allow_fetching: bool = False,
                    revocation_mode: str = "soft-fail") -> dict[str, Any]:
    if revocation_mode not in ("soft-fail", "hard-fail"):
        raise ValueError("Unsupported revocation mode")
    if allow_fetching and not roots:  # 限制：没有独立可信根时不允许联网查证书
        raise ValueError("Network certificate checks require independently verified trust roots")  # 限制：联网核验证书必须先有独立核实的可信根
    roots = roots or []
    certs = [load_cert_from_pemder(str(p.resolve(strict=True))) for p in roots]
    findings = []
    with source.resolve(strict=True).open("rb") as inp:
        reader = PdfFileReader(inp)
        for signed in reader.embedded_signatures:
            line: dict[str, Any] = {"field": signed.field_name}
            signer_certificate = signed.signer_cert
            if signer_certificate is not None:
                line["certificate"] = {
                    "claimed_subject": signer_certificate.subject.human_friendly,
                    "issuer": signer_certificate.issuer.human_friendly,
                    "serial_number": str(signer_certificate.serial_number),
                    "sha256_fingerprint": signer_certificate.sha256_fingerprint,
                    "identity_note": "Subject text is a claim; trust and authorized representation are separate.",  # 限制：证书主体只是声明，不等于已验证身份
                }
            try:
                context = ValidationContext(
                    trust_roots=certs,
                    allow_fetching=allow_fetching,
                    revocation_mode=revocation_mode,
                )
                result = (validation.validate_pdf_timestamp(signed, validation_context=context)
                          if str(signed.sig_object_type) == '/DocTimeStamp' else
                          validation.validate_pdf_signature(signed, signer_validation_context=context))
                line.update({"intact": bool(result.intact), "signature_valid": bool(result.valid),
                             "signer_trusted": bool(result.trusted) if certs else False,  # 限制：没提供可信根时，身份信任直接记为假
                             "certificate_status": "checked" if certs else "not_established",  # 限制：没提供可信根时，证书状态记为未建立
                             "coverage": str(result.coverage),
                             "modification_level": str(result.modification_level),
                             "digest_algorithm": str(result.md_algorithm),
                             "signature_mechanism": str(result.pkcs7_signature_mechanism)})
                try:
                    chain = result.validation_path
                    line["certificate_path"] = (
                        [{"subject": cert.subject.human_friendly,
                          "issuer": cert.issuer.human_friendly}
                         for cert in chain]
                        if chain is not None else []
                    )
                except (TypeError, AttributeError):
                    line["certificate_path"] = []
                # Display existing status details without re-running verification.
                try:
                    from .signature_evidence import validation_details
                    line["validation_details"] = validation_details(result, bool(certs))
                except Exception as exc:
                    line["validation_details_error"] = f"{type(exc).__name__}: {exc}"
                line["timestamp_validation_data_present"] = (getattr(result, 'timestamp_validity', None) is not None
                                                             or str(signed.sig_object_type) == '/DocTimeStamp')
                line["revocation_details_available"] = result.revocation_details is not None
            except Exception as exc:
                line.update({"verification_error": f"{type(exc).__name__}: {exc}",
                             "signer_trusted": False})  # 限制：验签出错时不把身份记为已信任
            findings.append(line)
    return {"count": len(findings), "signatures": findings,
            "note": "A valid PDF signature alone cannot prove that a named bank issued the document."}  # 限制：签名有效不能证明出具机构
