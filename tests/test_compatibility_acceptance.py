"""Real synthetic PDF compatibility boundaries for the core workbench."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.replace_text import list_text_runs, replace_text_run
from pdf_studio.paragraph_reflow import list_reflow_blocks
from pdf_studio.original_layout import reflow_like_original
from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document


def test_two_page_image_asset_survives_true_second_page_edit(tmp_path: Path) -> None:
    original = tmp_path / "multipage.pdf"
    output = tmp_path / "multipage-edited.pdf"
    canvas = Canvas(str(original))
    canvas.drawImage(
        ImageReader(Image.new("RGB", (24, 24), (30, 120, 210))),
        100, 500, width=24, height=24,
    )
    canvas.drawString(75, 720, "First page remains intact")
    canvas.showPage()
    canvas.drawString(75, 700, "Second page text to change")
    canvas.save()

    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    runs = list_text_runs(original, 2)["runs"]
    target = next(row for row in runs if row["text"] == "Second page text to change")
    result = replace_text_run(
        original, output, page=2,
        operator_index=target["operator_index"],
        expected_text=target["text"], replacement="Second page updated text",
    )
    assert result["text_visible_in_extraction"]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
    assert len(PdfReader(str(output)).pages) == 2
    assert "First page remains intact" in PdfReader(str(output)).pages[0].extract_text()
    assert "Second page updated text" in PdfReader(str(output)).pages[1].extract_text()
    assert "Second page text to change" not in PdfReader(str(output)).pages[1].extract_text()
    with pikepdf.Pdf.open(original) as before, pikepdf.Pdf.open(output) as after:
        assert before.pages[0].obj["/Contents"].read_bytes() == (
            after.pages[0].obj["/Contents"].read_bytes()
        )
        images_before = before.pages[0].resources.get("/XObject")
        images_after = after.pages[0].resources.get("/XObject")
        assert images_before and images_after
        assert sorted(images_before.keys()) == sorted(images_after.keys())
        for image_name in images_before:
            assert images_before[image_name].read_bytes() == (
                images_after[image_name].read_bytes()
            )


def test_original_layout_rejects_multi_column_ambiguity(tmp_path: Path) -> None:
    source = tmp_path / "columns.pdf"
    out = tmp_path / "invalid.pdf"
    canvas = Canvas(str(source))
    first = canvas.beginText(72, 700)
    first.setFont("Helvetica", 14)
    first.setLeading(19)
    first.textLine("Left column paragraph")
    first.textLine("Left column second line")
    canvas.drawText(first)
    canvas.drawString(320, 510, "Right column must stay fixed")
    canvas.save()
    target = next(row for row in list_reflow_blocks(source, 1)
                  if "Left column paragraph" in row["text"])
    with pytest.raises(ValueError, match="column|safe automatic flow|another"):
        reflow_like_original(
            source, out, page=1,
            start_operator_index=target["start_operator_index"],
            expected_text=target["text"],
            replacement=(
                "This is an extended original-column paragraph which cannot "
                "automatically move unrelated right-column content safely."
            ),
        )
    assert not out.exists()


def test_signed_multipage_pdf_rejects_direct_content_rewrite(tmp_path: Path) -> None:
    source = tmp_path / "original.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(75, 715, "Page one")
    canvas.showPage()
    canvas.drawString(75, 715, "Page two protected")
    canvas.save()
    password = "Synthetic private PDF test password value"
    folder = tmp_path / "credentials"
    generate_personal_credentials(
        folder, label="Synthetic signature test", password=password,
        algorithm="ECDSA-P256",
    )
    signed = sign_document(
        source, tmp_path / "signed.pdf",
        pfx=folder / "personal-signing.p12",
        password=password.encode(),
    )
    editable = next(row for row in list_text_runs(signed, 2)["runs"]
                    if row["text"] == "Page two protected")
    with pytest.raises(ValueError, match="signed|certified"):
        replace_text_run(
            signed, tmp_path / "refused.pdf", page=2,
            operator_index=editable["operator_index"],
            expected_text=editable["text"], replacement="Altered page two text",
        )
    assert not (tmp_path / "refused.pdf").exists()
