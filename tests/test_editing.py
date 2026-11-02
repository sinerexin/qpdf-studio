from pathlib import Path

from reportlab.pdfgen.canvas import Canvas
from pdf_studio.editing import document_properties, edit_properties


def test_properties_edit_is_separate_and_auditable(tmp_path: Path) -> None:
    original = tmp_path / "unsigned.pdf"
    canvas = Canvas(str(original))
    canvas.drawString(100, 700, "Synthetic example")
    canvas.save()
    raw_original = original.read_bytes()
    changed = tmp_path / "new.pdf"
    result = edit_properties(original, changed, {"Title": "My Draft", "Subject": "Workshop"})
    assert result["operation"] == "edit-pdf-properties"
    assert original.read_bytes() == raw_original
    assert changed.exists()
    assert not (tmp_path / "new.pdf.changes.json").exists()
    assert document_properties(changed)["Title"] == "My Draft"
    try:
        edit_properties(original, original, {"Title": "overwrite"})
        raise AssertionError("Overwrite was not rejected")
    except ValueError:
        pass
