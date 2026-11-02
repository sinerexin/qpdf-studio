from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.annotations import add_note
from pdf_studio.revisions import inspect_revisions
from pdf_studio.paint_diagnostics import inspect_paint_operations


def test_note_annotation_and_revision_report(tmp_path: Path) -> None:
    source, target = tmp_path / "original.pdf", tmp_path / "annotated.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(100, 700, "Document for annotation feature")
    canvas.save()
    original_bytes = source.read_bytes()
    result = add_note(source, target, page_number=1, x=100, y=250,
                      contents="Review this figure", author="My notes")
    assert result["input_sha256"] != result["output_sha256"]
    assert source.read_bytes() == original_bytes
    assert not (tmp_path / "annotated.pdf.changes.json").exists()
    with pikepdf.Pdf.open(target) as pdf:
        notes = pdf.pages[0].obj["/Annots"]
        assert str(notes[0]["/Subtype"]) == "/Text"
        assert str(notes[0]["/Contents"]) == "Review this figure"
    found = inspect_revisions(target)
    assert found["chain_status"] == "parsed"
    assert found["revision_chain"]
    assert found["signature_or_certification_structure_present"] is False
    paints = inspect_paint_operations(target)
    assert len(paints["pages"]) == 1
    assert paints["pages"][0]["operator_counts"]["Tj"] >= 1
    assert any(event["kind"] == "text" for event in paints["pages"][0]["events"])
    with pytest.raises(ValueError):
        add_note(source, source, page_number=1, x=100, y=250, contents="Overwritten")


def test_unsigned_revision_evidence_does_not_claim_signer(tmp_path: Path) -> None:
    path = tmp_path / "example.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(50, 500, "No digital signature")
    canvas.save()
    result = inspect_revisions(path)
    assert result["byte_ranges"] == []
    assert result["signature_or_certification_structure_present"] is False
    assert result["startxref_markers"]
