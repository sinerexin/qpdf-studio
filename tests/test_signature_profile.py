"""Original/new signature profiles must never conflate identity or public keys."""
from pathlib import Path
from reportlab.pdfgen.canvas import Canvas
import pytest

from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document
from pdf_studio.signature_profile import (
    inspect_signature_profile, compare_signature_parameters,
)


def _sign(tmp_path: Path, suffix: str, algorithm: str) -> tuple[Path, Path]:
    label = "Independent Personal Test " + suffix
    folder = tmp_path / ("keys-" + suffix)
    password = "Only private key testing " + suffix
    generate_personal_credentials(
        folder, label=label, password=password, algorithm=algorithm,
    )
    pdf = tmp_path / ("input-" + suffix + ".pdf")
    c = Canvas(str(pdf))
    c.drawString(75, 710, "A synthetic document belongs to a test subject.")
    c.save()
    signed = sign_document(
        pdf, tmp_path / ("signed-" + suffix + ".pdf"),
        pfx=folder / "personal-signing.p12", password=password.encode(),
    )
    return signed, folder / "self-signed-certificate.pem"


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256", "ECDSA-P384"])
def test_match_same_algorithm_but_keys_and_certificates_are_different(
    tmp_path: Path, algorithm: str,
) -> None:
    first, _ = _sign(tmp_path, "alpha", algorithm)
    second, _ = _sign(tmp_path, "beta", algorithm)
    original = inspect_signature_profile(first)
    assert original["signature_count"] == 1
    profile = original["signatures"][0]
    assert profile["compatible_personal_key_algorithm"] == algorithm
    assert profile["mathematically_valid"] and profile["cryptographically_intact"]
    assert profile["overall_technical_result"] == "cryptographically_valid_identity_unverified"
    assert profile["identity_trusted_without_explicit_root"] is False
    result = compare_signature_parameters(first, second)
    assert result["same_algorithm_parameters"]
    assert result["same_public_key"] is False
    assert result["same_certificate_fingerprint"] is False
    assert result["new_signature_cryptographically_valid"]
    assert result["original"]["claimed_subject_read_only"] != result["new"]["claimed_subject_read_only"]


def test_independent_trust_roots_for_original_and_output(tmp_path: Path) -> None:
    original, old_root = _sign(tmp_path, "old", "ECDSA-P256")
    new, new_root = _sign(tmp_path, "new", "ECDSA-P256")
    comparison = compare_signature_parameters(
        original, new, original_roots=[old_root], new_roots=[new_root],
    )
    assert comparison["original"]["overall_technical_result"] == (
        "signature_valid_and_certificate_path_trusted"
    )
    assert comparison["new"]["overall_technical_result"] == (
        "signature_valid_and_certificate_path_trusted"
    )
    cross = compare_signature_parameters(
        original, new, original_roots=[old_root], new_roots=[old_root],
    )
    assert cross["original"]["trust_path_validated"] is True
    assert cross["new"]["trust_path_validated"] is False
    assert not cross["same_public_key"]


def test_unsigned_original_has_no_original_signer_profile(tmp_path: Path) -> None:
    blank = tmp_path / "not-signed.pdf"
    canvas = Canvas(str(blank))
    canvas.drawString(90, 700, "Unsigned")
    canvas.save()
    report = inspect_signature_profile(blank)
    assert report["signature_count"] == 0
    assert report["signatures"] == []
    signed, _ = _sign(tmp_path, "newonly", "RSA-3072")
    with pytest.raises(ValueError, match="contain"):
        compare_signature_parameters(blank, signed)
