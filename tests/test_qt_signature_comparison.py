"""Qt A/B identity panel shows independent signer claims and trust evidence."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.qt_desktop import PdfStudioQt
from pdf_studio.signing import sign_document


def _signed_doc(folder: Path, name: str, algorithm: str) -> Path:
    owner = folder / ("credential-" + name)
    password = "Synthetic private signing " + name
    generate_personal_credentials(owner, label="Personal Demo " + name,
                                  password=password, algorithm=algorithm)
    src = folder / ("original-" + name + ".pdf")
    canvas = Canvas(str(src))
    canvas.drawString(72, 701, "A privately controlled test document")
    canvas.save()
    return sign_document(
        src, folder / ("signed-" + name + ".pdf"),
        pfx=owner / "personal-signing.p12", password=password.encode(),
    )


def test_a_b_signature_comparison_visible_with_claims_fingerprints_and_trust(tmp_path: Path):
    a = _signed_doc(tmp_path, "old", "ECDSA-P256")
    b = _signed_doc(tmp_path, "new", "ECDSA-P256")
    app = QApplication.instance() or QApplication([])
    ui = PdfStudioQt()
    ui.load_path("A", a)
    ui.load_path("B", b)
    ui.show()

    try:
        assert any(action.text() == "A/B 签名身份对照"
                   for action in ui.signing_tools.menu().actions())
        ui.compare_signature_identities()
        dialog = ui._signature_comparison_dialog
        assert dialog.isVisible()
        end = time.monotonic() + 15
        left = dialog.findChild(QPlainTextEdit, "SignatureProfileA")
        right = dialog.findChild(QPlainTextEdit, "SignatureProfileB")
        footer = dialog.findChild(QLabel, "SignatureComparisonConclusion")
        assert left and right and footer
        while time.monotonic() < end:
            app.processEvents()
            if "证书 SHA-256 指纹" in left.toPlainText() and not ui._busy:
                break
            time.sleep(0.025)
        assert not ui._busy
        for text in (left.toPlainText(), right.toPlainText()):
            assert "证书 SHA-256 指纹" in text
            assert "公钥 SHA-256 指纹" in text
            assert "身份信任尚未建立" in text
            assert "证书主体声明" in text
        assert "Personal Demo old" in left.toPlainText()
        assert "Personal Demo new" in right.toPlainText()
        assert "公钥指纹：不同" in footer.text()
        assert "证书指纹：不同" in footer.text()
        dialog.close()
        app.processEvents()
    finally:
        ui.close()


def test_generate_personal_key_same_algorithm_default_but_not_same_identity(tmp_path: Path):
    source = _signed_doc(tmp_path, "source", "RSA-3072")
    app = QApplication.instance() or QApplication([])
    ui = PdfStudioQt()
    ui.load_path("A", source)
    ui.show()
    seen = []
    try:
        with patch(
            "pdf_studio.qt_desktop.open_personal_certificate_editor",
            side_effect=lambda *a, **kw: seen.append(kw),
        ):
            ui.generate_compatible_personal_keys()
            deadline = time.monotonic() + 15
            while ui._busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.03)
            app.processEvents()
        assert not ui._busy
        assert seen == [{"reissue": False, "preferred_algorithm": "RSA-3072"}]
    finally:
        ui.close()
