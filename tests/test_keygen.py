from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.keygen import generate_personal_credentials, reissue_personal_certificate
from pdf_studio.signing import sign_document
from pdf_studio.signature_audit import audit_signatures


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256"])
def test_generated_secret_is_encrypted_and_certificate_not_ca(
    tmp_path: Path, algorithm: str,
) -> None:
    password = "A unique local testing password 2026!"
    output = tmp_path / f"credentials-{algorithm}"
    report = generate_personal_credentials(
        output, label="Personal Test User", password=password, algorithm=algorithm,
    )
    assert not any(password in str(value) for value in report.values())
    private_pem = (output / "private-key-encrypted.pem").read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in private_pem
    assert b"PRIVATE KEY-----" not in private_pem.split(b"ENCRYPTED PRIVATE KEY")[0]
    with pytest.raises((ValueError, TypeError)):
        serialization.load_pem_private_key(private_pem, password=b"wrong password")
    private = serialization.load_pem_private_key(private_pem, password=password.encode())
    assert private is not None
    certificate = x509.load_pem_x509_certificate(
        (output / "self-signed-certificate.pem").read_bytes()
    )
    assert certificate.subject == certificate.issuer
    constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert constraints.ca is False
    assert usage.digital_signature and not usage.key_cert_sign
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates((output / "personal-signing.p12").read_bytes(), b"wrong")
    pkey, pcert, _ = pkcs12.load_key_and_certificates(
        (output / "personal-signing.p12").read_bytes(), password.encode()
    )
    assert pkey is not None and pcert is not None
    from hashlib import sha256
    public_der = pkey.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert report["public_key_sha256_fingerprint"] == sha256(public_der).hexdigest().upper()
    assert report["public_key_sha256_fingerprint"] != report["certificate_sha256_fingerprint"]
    with pytest.raises(ValueError, match="existing"):
        generate_personal_credentials(output, label="Other", password=password)


def test_generated_personal_p12_signs_pdf_but_not_ca_trusted(tmp_path: Path) -> None:
    password = "Strong local password for signing!"
    folder = tmp_path / "credentials"
    generate_personal_credentials(folder, label="Self Signed Test", password=password)
    src = tmp_path / "test.pdf"
    canvas = Canvas(str(src))
    canvas.drawString(80, 700, "A synthetic personal document")
    canvas.save()
    signed = sign_document(
        src, tmp_path / "signed.pdf",
        pfx=folder / "personal-signing.p12", password=password.encode(),
    )
    report = audit_signatures(signed)
    assert report["count"] == 1
    assert report["signatures"][0]["signature_valid"] is True
    assert report["signatures"][0]["overall_technical_result"] == (
        "cryptographically_valid_identity_unverified"
    )


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256"])
def test_reissue_owned_selfsigned_certificate_changes_cn_and_validity_not_key(
    tmp_path: Path, algorithm: str,
) -> None:
    source = tmp_path / "original-identity"
    newer = tmp_path / "new-certificate"
    password = "Only used in temporary synthetic test"
    old = generate_personal_credentials(
        source, label="First Personal Label", password=password,
        days_valid=30, algorithm=algorithm,
    )
    original_bytes = {
        file.name: file.read_bytes() for file in source.iterdir() if file.is_file()
    }
    renewed = reissue_personal_certificate(
        source, newer, label="New Personal Label", days_valid=180, password=password,
    )
    assert renewed["key_reused"]
    assert renewed["certificate_subject_claim"] == "New Personal Label"
    assert renewed["previous_certificate_fingerprint"] == old["certificate_sha256_fingerprint"]
    assert renewed["certificate_sha256_fingerprint"] != old["certificate_sha256_fingerprint"]
    assert (newer / "private-key-encrypted.pem").read_bytes() == original_bytes["private-key-encrypted.pem"]
    assert all(file.read_bytes() == original_bytes[file.name] for file in source.iterdir() if file.is_file())
    with pytest.raises((ValueError, TypeError)):
        reissue_personal_certificate(
            source, tmp_path / "bad-reissue",
            label="Wrong Password", days_valid=50, password="Wrong secret 2026!",
        )
    assert not (tmp_path / "bad-reissue").exists()
    overwritten = reissue_personal_certificate(
        source, newer, label="Overwrite", days_valid=50, password=password,
    )
    assert overwritten["certificate_subject_claim"] == "Overwrite"
