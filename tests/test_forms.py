from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from pdf_studio.forms import text_fields, fill_text_fields


def test_fill_form_field_and_preserve_original(tmp_path: Path) -> None:
    source, output = tmp_path / "blank.pdf", tmp_path / "completed.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(60, 720, "Sample AcroForm for own records")
    canvas.acroForm.textfield(name="comment", tooltip="Comment", x=60, y=630,
                              width=200, height=22, value="Initial")
    canvas.save()
    raw = source.read_bytes()
    assert text_fields(source)["comment"]["value"] == "Initial"
    report = fill_text_fields(source, output, {"comment": "Revised text"})
    assert report["fields_changed"]["comment"]["after"] == "Revised text"
    assert text_fields(output)["comment"]["value"] == "Revised text"
    assert source.read_bytes() == raw
    assert not (tmp_path / "completed.pdf.changes.json").exists()
