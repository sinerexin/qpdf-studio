"""One local smoke test: qpdf analysis, diff, real PDF signing & separate verification.

The generated identity is fictional and lives only in a temporary directory.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from PIL import Image

from pdf_studio.core import inspect_pdf, compare
from pdf_studio.signing import sign_document, verify_document
from pdf_studio.signature_audit import audit_signatures
from pdf_studio.body_text import add_body_text
from pdf_studio.replace_text import list_text_runs, replace_text_run
from pdf_studio.visual import render_document, visual_diff
from pdf_studio.revisions import inspect_revisions
from pdf_studio.annotations import add_note
from pdf_studio.deep import semantic_inventory
from pdf_studio.pages import transform_pages, combine_documents


def _pdf(path: Path, text: str, with_image: bool = False) -> None:
    canvas = Canvas(str(path))
    canvas.drawString(80, 700, text)
    if with_image:
        canvas.drawImage(ImageReader(Image.new("RGB", (32, 32), "#2277bb")),
                         90, 600, width=32, height=32)
    canvas.save()


def test_local_core_and_signing_roundtrip() -> None:
    with TemporaryDirectory() as work:
        root = Path(work)
        first, second = root / "a.pdf", root / "b.pdf"
        _pdf(first, "Owned document alpha", with_image=True)
        _pdf(second, "Owned document beta")
        one = inspect_pdf(first, root / "parse_a", streams=True,
                          operators=True, text_geometry=True, assets=True)
        two = inspect_pdf(second, root / "parse_b")
        assert one["page_count"] == 1 and one["object_count"] > 0
        assert one["pages"][0].get("content_operators")
        assert one["pages"][0].get("text_fragments")
        assert one["pages"][0].get("fonts")
        assert one["pages"][0].get("images")
        assert compare(one, two)["page_text_differences"]
        rendered = render_document(first, root / "render")
        assert rendered["rendered_page_count"] == 1
        assert (root / "render" / "page_0001.png").is_file()
        pixels = visual_diff(first, second, root / "pixel_diff", scale=1.0)
        assert pixels["pages"][0]["changed_pixels"] > 0
        semantics = semantic_inventory(first)
        assert len(semantics["pages"]) == 1
        assert semantics["pages"][0]["glyph_count_total"] > 0
        assert "metadata" in semantics and "attachments" in semantics
        separated = transform_pages(first, root / "extracted.pdf", pages=[1], rotate=90)
        assert separated["rotate_clockwise_degrees"] == 90
        joined = combine_documents([first, second], root / "combined.pdf")
        assert len(joined["inputs"]) == 2
        assert not (root / "combined.pdf.changes.json").exists()

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QPDF Studio Test Certificate")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(identity).issuer_name(identity)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(digital_signature=True, content_commitment=True,
                              key_encipherment=False, data_encipherment=False, key_agreement=False,
                              key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
                critical=True,
            ).sign(key, hashes.SHA256())
        )
        pfx = root / "test-only.p12"
        pfx.write_bytes(pkcs12.serialize_key_and_certificates(
            b"qpdf-studio-test", key, cert, None, serialization.NoEncryption()
        ))
        trusted = root / "test-root.pem"
        trusted.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        signed = sign_document(first, root / "signed.pdf", pfx=pfx)
        report = verify_document(signed, [trusted])
        assert report["count"] == 1, report
        assert report["signatures"][0]["intact"] is True, report
        assert report["signatures"][0]["signature_valid"] is True, report
        professional = audit_signatures(signed, [trusted])
        assert professional["count"] == 1, professional
        assert professional["signatures"][0]["byte_range_evidence"]["structurally_plausible"]
        assert professional["signatures"][0]["certificate_validity"]["currently_within_dates"]
        assert professional["signatures"][0]["overall_technical_result"].startswith("signature_valid"), professional
        anonymous = audit_signatures(signed)
        assert anonymous["signatures"][0]["overall_technical_result"] == (
            "cryptographically_valid_identity_unverified"
        )
        modified = bytearray(signed.read_bytes())
        assert modified.startswith(b"%PDF-")
        modified[7] = ord("5") if modified[7] != ord("5") else ord("6")
        broken = root / "changed-signed-content.pdf"
        broken.write_bytes(modified)
        rejected = audit_signatures(broken, [trusted])
        assert rejected["signatures"][0]["overall_technical_result"] == (
            "cryptographic_integrity_or_signature_failed"
        ), rejected
        try:
            add_body_text(signed, root / "signed-edited.pdf",
                          page=1, x=80, y=240, text="Should fail")
        except ValueError as error:
            assert "Signed" in str(error)
        else:
            raise AssertionError("Editing signed PDF unexpectedly accepted")
        safe_runs = [row for row in list_text_runs(signed, 1)["runs"] if row["editable"]]
        assert safe_runs
        with __import__("pytest").raises(ValueError, match="signed|certified"):
            replace_text_run(
                signed, root / "forbidden-replacement.pdf", page=1,
                operator_index=safe_runs[0]["operator_index"],
                array_index=safe_runs[0]["array_index"],
                expected_text=safe_runs[0]["text"], replacement="New text",
            )
        assert not (root / "forbidden-replacement.pdf").exists()
        signed_structure = inspect_pdf(signed, root / "parse_signed")
        assert len(signed_structure["signatures"]) == 1
        assert first.read_bytes() != signed.read_bytes()
        evidence = inspect_revisions(signed)
        assert evidence["byte_ranges"]
        assert evidence["byte_ranges"][0]["structurally_plausible"]
        assert evidence["byte_ranges"][0]["signed_revision_ends_at"] <= len(signed.read_bytes())
        try:
            add_note(signed, root / "signed-altered.pdf", page_number=1,
                     x=90, y=80, contents="Should not rewrite signed file")
        except ValueError as exc:
            assert "signature" in str(exc).lower()
        else:
            raise AssertionError("Signed PDF note modification unexpectedly accepted")
        try:
            transform_pages(signed, root / "incorrectly-edited.pdf", pages=[1])
        except ValueError as exc:
            assert "digital signature" in str(exc)
        else:
            raise AssertionError("Page transform did not preserve signed-file safety")
