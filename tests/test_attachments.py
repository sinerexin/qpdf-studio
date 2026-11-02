from pathlib import Path

from pypdf import PdfWriter

from pdf_studio.attachments import extract_attachments


def test_sanitized_attachment_export(tmp_path: Path) -> None:
    src = tmp_path / "attachment.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_attachment(filename="../../example.txt", data=b"local sample data")
    with src.open("wb") as output:
        writer.write(output)
    report = extract_attachments(src, tmp_path / "export")
    assert report["attachment_count"] == 1
    target = tmp_path / "export" / report["attachments"][0]["exported_as"]
    assert target.read_bytes() == b"local sample data"
    assert target.parent == tmp_path / "export"
    assert src.read_bytes().startswith(b"%PDF-")
