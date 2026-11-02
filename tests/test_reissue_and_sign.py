"""Owned signer rename -> new self-signed certificate -> NEW signed PDF."""
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.reissue_and_sign import reissue_signer_and_sign
from pdf_studio.signing import sign_document
from pdf_studio.signature_profile import inspect_signature_profile


def _sample_pdf(path: Path) -> None:
    canvas = Canvas(str(path))
    canvas.drawString(80, 710, "Synthetic document for private personal signing")
    canvas.save()


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256"])
def test_owned_signer_can_be_renamed_before_new_signature(
    tmp_path: Path, algorithm: str,
) -> None:
    source = tmp_path / "unsigned.pdf"
    _sample_pdf(source)
    original_bytes = source.read_bytes()
    secret = "Testing independent private credential owner password!"
    existing = tmp_path / "owned-credential"
    before = generate_personal_credentials(
        existing, label="Original Private Test Name",
        password=secret, algorithm=algorithm,
    )
    fresh = tmp_path / "renamed-credentials"
    output = tmp_path / "newly-signed.pdf"
    result = reissue_signer_and_sign(
        source, existing, fresh, output,
        signer_label="Updated Private Test Name",
        password=secret, days_valid=125,
    )
    profile = inspect_signature_profile(output)["signatures"][0]
    assert "Updated Private Test Name" in profile["claimed_subject_read_only"]
    assert "Original Private Test Name" not in profile["claimed_subject_read_only"]
    assert profile["cryptographically_intact"] and profile["mathematically_valid"]
    assert profile["overall_technical_result"] == "cryptographically_valid_identity_unverified"
    assert result["old_certificate_sha256"] == before["certificate_sha256_fingerprint"]
    assert result["new_certificate_sha256"] != result["old_certificate_sha256"]
    assert result["new_public_key_sha256"] == before["public_key_sha256_fingerprint"]
    assert result["own_private_key_reused"] is True
    assert source.read_bytes() == original_bytes
    assert not (output.with_suffix(".pdf.signer-changes.json")).is_file()
    old_cert = x509.load_pem_x509_certificate(
        (existing / "self-signed-certificate.pem").read_bytes()
    )
    new_cert = x509.load_pem_x509_certificate(
        (fresh / "self-signed-certificate.pem").read_bytes()
    )
    assert old_cert.subject != new_cert.subject
    assert old_cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == new_cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    again = reissue_signer_and_sign(
        output, fresh, fresh, output, signer_label="Other", password=secret,
    )
    assert "Other" in again["new_signer_claim"]


def test_existing_signed_pdf_allows_new_independent_signature(tmp_path: Path) -> None:
    original = tmp_path / "source.pdf"
    _sample_pdf(original)
    secret = "Synthetic personal password for signed document"
    existing = tmp_path / "owned-credential"
    generate_personal_credentials(
        existing, label="Original Signer", password=secret,
    )
    signed = sign_document(
        original, tmp_path / "signed-original.pdf",
        pfx=existing / "personal-signing.p12", password=secret.encode(),
    )
    immutable = signed.read_bytes()
    reissue_signer_and_sign(
        signed, existing, tmp_path / "new-credentials", tmp_path / "newly-signed.pdf",
        signer_label="New Personal Signer", password=secret,
    )
    assert signed.read_bytes() == immutable
    assert (tmp_path / "new-credentials").exists()
    assert (tmp_path / "newly-signed.pdf").exists()
    assert inspect_signature_profile(tmp_path / "newly-signed.pdf")["signature_count"] == 2
