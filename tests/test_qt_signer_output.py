"""Qt two-workspace workflow: edit OWN signer, reissue, sign and show result."""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton,
)
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.qt_desktop import PdfStudioQt
from pdf_studio.signature_profile import inspect_signature_profile


@pytest.mark.parametrize("algorithm", ["RSA-3072", "ECDSA-P256"])
def test_change_own_signer_name_and_produce_from_signature_workspace(
    tmp_path: Path, monkeypatch, algorithm: str,
) -> None:
    unsigned = tmp_path / "owned-unsigned.pdf"
    c = Canvas(str(unsigned))
    c.drawString(72, 698, "Owned unsigned synthetic PDF")
    c.save()
    original = unsigned.read_bytes()
    secret = "Personal key secret only for tests 2026"
    personal_folder = tmp_path / "my-personal-cert"
    before = generate_personal_credentials(
        personal_folder, label="My Old Test Name", password=secret,
        algorithm=algorithm,
    )
    reissued = tmp_path / "my-reissued-cert"
    destination = tmp_path / "signed-with-new-name.pdf"
    app = QApplication.instance() or QApplication([])
    ui = PdfStudioQt()
    ui.show()
    ui.load_path("A", unsigned)
    ui.workspace_tabs.setCurrentIndex(1)

    def fill(dialog: QDialog) -> int:
        assert "修改个人签署者并产出新 PDF" in dialog.windowTitle()
        signed = dialog.findChild(QLineEdit, "PersonalNewSignerName")
        password = dialog.findChild(QLineEdit, "ExistingPrivateKeyPassword")
        new_directory = dialog.findChild(QLineEdit, "NewCredentialDirectory")
        output = dialog.findChild(QLineEdit, "NewSignedOutput")
        assert signed and password and new_directory and output
        signed.setText("My Updated Test Name")
        password.setText(secret)
        new_directory.setText(str(reissued))
        output.setText(str(destination))
        return QDialog.Accepted

    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                QFileDialog, "getExistingDirectory",
                lambda *a, **kw: str(personal_folder),
            )
            patcher.setattr(QDialog, "exec", fill)
            patcher.setattr(
                QMessageBox, "information",
                lambda *a, **kw: QMessageBox.Ok,
            )
            buttons = [b for b in ui.findChildren(QPushButton)
                       if b.text() == "修改签署者并产出"]
            assert len(buttons) == 1 and buttons[0].isVisible()
            buttons[0].click()
            deadline = time.monotonic() + 16
            while ui._busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.03)
            app.processEvents()
        assert not ui._busy
        assert destination.exists(), ui.status.text()
        assert ui.docs["B"] == destination.resolve()
        assert ui.active_slot.currentText() == "B"
        assert unsigned.read_bytes() == original
        profile = inspect_signature_profile(destination)["signatures"][0]
        assert "My Updated Test Name" in profile["claimed_subject_read_only"]
        assert profile["mathematically_valid"] and profile["cryptographically_intact"]
        assert profile["overall_technical_result"] == (
            "cryptographically_valid_identity_unverified"
        )
        assert profile["public_key_sha256"] == before["public_key_sha256_fingerprint"]
        assert profile["certificate_sha256"] != before["certificate_sha256_fingerprint"]
    finally:
        ui.close()
