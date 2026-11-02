"""Synthetic Qt acceptance: actual PDF operations, mock only modal user input.

All keys and documents belong to a temporary test fixture. No personal key is
loaded and no synthetic private material is retained after pytest cleanup.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QInputDialog, QLineEdit,
    QMessageBox, QPushButton,
)
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader

from pdf_studio.qt_desktop import PdfStudioQt
from pdf_studio.signature_audit import audit_signatures


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    window = PdfStudioQt()
    window.show()
    yield window, app
    if window._busy:
        _finish_task(window, app)
    window.close()


def _finish_task(window: PdfStudioQt, app, seconds: float = 12.0) -> None:
    timeout = time.monotonic() + seconds
    while window._busy and time.monotonic() < timeout:
        app.processEvents()
        time.sleep(0.025)
    app.processEvents()
    assert not window._busy, f"Qt worker did not finish: {window.status.text()}"
    assert "失败" not in window.status.text(), window.status.text()


def _click(window: PdfStudioQt, label: str) -> None:
    matching = [b for b in window.findChildren(QPushButton) if b.text() == label]
    if matching:
        assert len(matching) == 1, (label, len(matching))
        matching[0].click()
        return
    menus = [window.content_edit_menu.menu(), window.signing_tools.menu()]
    actions = [a for menu in menus for a in menu.actions() if a.text() == label]
    assert len(actions) == 1, (label, len(actions))
    actions[0].trigger()


def test_gui_open_pdf_and_direct_text_modes(gui, tmp_path: Path) -> None:
    window, app = gui
    doc = tmp_path / "owned-example.pdf"
    pdf = Canvas(str(doc))
    pdf.drawString(75, 710, "Owned example PDF content")
    pdf.save()
    window.load_path("A", doc)
    window.render_previews()
    assert window.pages_count["A"] == 1
    # Default UI now scales PDF pages to fit the preview width rather than
    # forcing a fixed 125% image that clips off the right-hand side.
    preview_width = window.previews["A"].image.pixmap().width()
    viewport_width = window.previews["A"].scroll.viewport().width()
    assert 200 <= preview_width <= viewport_width + 18
    _click(window, "替换已有文字")
    assert window._replacing_existing_text
    _click(window, "段落重排")
    assert window._reflowing_paragraph and not window._replacing_existing_text


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256"])
def test_gui_personal_key_export_and_owned_pdf_sign_verify(
    gui, tmp_path: Path, monkeypatch, algorithm: str,
) -> None:
    window, app = gui
    source = tmp_path / "owned-test.pdf"
    document = Canvas(str(source))
    document.drawString(75, 710, "Synthetic personally controlled PDF")
    document.save()
    window.load_path("A", source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = tmp_path / "generated-personal-credentials"
    output = tmp_path / "personally-signed.pdf"
    # All strings are synthetic fixtures, not user credentials.
    test_password = "local-testing-only-password-value"

    def enter_personal_parameters(dialog) -> int:
        assert "创建个人公钥" in dialog.windowTitle()
        algorithms = dialog.findChildren(QComboBox)
        assert len(algorithms) == 1
        algorithms[0].setCurrentText(algorithm)
        inputs = dialog.findChildren(QLineEdit)
        secrets = [item for item in inputs
                   if item.echoMode() == QLineEdit.Password]
        assert len(secrets) == 2
        for item in secrets:
            item.setText(test_password)
        destinations = [item for item in inputs
                        if "尚不存在的新文件夹" in item.placeholderText()]
        assert len(destinations) == 1
        destinations[0].setText(str(folder))
        return QDialog.Accepted

    with monkeypatch.context() as patches:
        patches.setattr(QDialog, "exec", enter_personal_parameters)
        patches.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
        _click(window, "生成公钥 / 加密私钥")
        _finish_task(window, app)

    pem = folder / "private-key-encrypted.pem"
    pub = folder / "public-key.pem"
    certificate = folder / "self-signed-certificate.pem"
    p12 = folder / "personal-signing.p12"
    assert all(file.is_file() and file.stat().st_size > 0
               for file in (pem, pub, certificate, p12))
    assert b"ENCRYPTED PRIVATE KEY" in pem.read_bytes()
    private = serialization.load_pem_private_key(
        pem.read_bytes(), password=test_password.encode(),
    )
    public = serialization.load_pem_public_key(pub.read_bytes())
    assert private.public_key().public_numbers() == public.public_numbers()
    extracted_key, extracted_certificate, _ = pkcs12.load_key_and_certificates(
        p12.read_bytes(), test_password.encode(),
    )
    assert extracted_key and extracted_certificate
    cert = x509.load_pem_x509_certificate(certificate.read_bytes())
    assert cert.subject == cert.issuer

    with monkeypatch.context() as patches:
        patches.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(p12), "P12"),
        )
        patches.setattr(
            QFileDialog, "getSaveFileName",
            lambda *args, **kwargs: (str(output), "PDF"),
        )
        patches.setattr(
            QInputDialog, "getText",
            lambda *args, **kwargs: (test_password, True),
        )
        _click(window, "签署 PDF")
        _finish_task(window, app)

    assert output.is_file()
    assert window.docs["B"] == output.resolve()
    assert window.active_slot.currentText() == "B"
    assert window.previews["B"].image.pixmap() is not None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert "Synthetic personally controlled PDF" in PdfReader(str(output)).pages[0].extract_text()
    signed = audit_signatures(output)
    assert signed["count"] == 1
    assert signed["signatures"][0]["intact"]
    assert signed["signatures"][0]["signature_valid"]
    assert signed["signatures"][0]["overall_technical_result"] == (
        "cryptographically_valid_identity_unverified"
    )
    with monkeypatch.context() as patches:
        patches.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)
        _click(window, "验签")
        _finish_task(window, app)
    assert window._signature_audit is not None
    assert window._signature_audit["count"] == 1
    assert window.signature_list.count() == 1
    assert not window._signature_audit["signatures"][0]["signer_trusted"]

    with monkeypatch.context() as patches:
        patches.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
        patches.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(certificate), "PEM"),
        )
        patches.setattr(
            QInputDialog, "getItem",
            lambda *args, **kwargs: ("离线验证（默认）", True),
        )
        _click(window, "验签")
        _finish_task(window, app)
    assert window._signature_audit["trust_roots_supplied"] == 1
    assert window._signature_audit["network_fetching"] is False
    assert window._signature_audit["signatures"][0]["overall_technical_result"] == (
        "signature_valid_and_certificate_path_trusted"
    )
