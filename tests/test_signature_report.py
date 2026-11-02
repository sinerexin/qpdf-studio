from pathlib import Path

from pdf_studio.signature_report import signature_audit_html, save_signature_html


def test_signature_report_html_escapes_untrusted_certificate_text(tmp_path: Path) -> None:
    report = {
        "source": "<script>alert('file')</script>.pdf",
        "count": 1,
        "signatures": [{
            "field": "Signature",
            "overall_technical_result": "cryptographically_valid_identity_unverified",
            "intact": True,
            "signature_valid": True,
            "signer_trusted": False,
            "certificate": {"claimed_subject": "<script>fake issuer</script>"},
        }],
        "limitations": ["Certificate subject is not identity proof."],
    }
    content = signature_audit_html(report)
    assert "&lt;script&gt;fake issuer&lt;/script&gt;" in content
    assert "<script>fake issuer</script>" not in content
    target = tmp_path / "report.html"
    assert save_signature_html(report, target) == target
    assert target.is_file()
