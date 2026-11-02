from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.body_text import add_body_text


def test_visible_body_text_addition_creates_real_pdf_content(tmp_path: Path) -> None:
    src, dst = tmp_path / "original.pdf", tmp_path / "editor-output.pdf"
    doc = Canvas(str(src))
    doc.drawString(70, 700, "Original content remains")
    doc.save()
    original = src.read_bytes()
    report = add_body_text(
        src, dst, page=1, x=90, y=350, text="New visible line", size=18,
        color="#204E80",
    )
    assert report["text_added"] == "New visible line"
    extracted = PdfReader(str(dst)).pages[0].extract_text()
    assert "Original content remains" in extracted
    assert "New visible line" in extracted
    assert report["editing_method"] == "direct-page-content-stream-append"
    with pikepdf.Pdf.open(src) as before, pikepdf.Pdf.open(dst) as after:
        before_ops = list(pikepdf.parse_content_stream(before.pages[0]))
        after_ops = list(pikepdf.parse_content_stream(after.pages[0]))
        # Real text operands must be appended to /Contents, not painted as
        # a page XObject/image/white-rectangle replacement.
        assert len(after_ops) > len(before_ops)
        assert any(str(op.operator) == "Tj" and
                   str(op.operands[0]) == "New visible line" for op in after_ops)
        assert not any(str(op.operator) in {"Do", "re"} for op in after_ops)
        assert any(str(k).startswith("/PdfStudioTextFont") for k in
                   after.pages[0].resources["/Font"].keys())
        assert "/XObject" not in after.pages[0].resources
    assert src.read_bytes() == original
    assert not (tmp_path / "editor-output.pdf.changes.json").is_file()
    with pytest.raises(ValueError):
        add_body_text(src, src, page=1, x=90, y=350, text="Overwrite")


def test_direct_body_text_preserves_existing_font_resources_and_pages(tmp_path: Path) -> None:
    source, first, second = (
        tmp_path / "input.pdf", tmp_path / "first.pdf", tmp_path / "second.pdf"
    )
    document = Canvas(str(source))
    document.drawString(80, 700, "Page one text")
    document.showPage()
    document.drawString(80, 700, "Page two remains")
    document.save()
    add_body_text(source, first, page=1, x=90, y=380, text="First addition")
    add_body_text(first, second, page=1, x=95, y=330, text="Second addition")
    reader = PdfReader(str(second))
    page1 = reader.pages[0].extract_text()
    assert "Page one text" in page1
    assert "First addition" in page1 and "Second addition" in page1
    assert reader.pages[1].extract_text().strip() == "Page two remains"
    with pikepdf.Pdf.open(second) as final:
        names = [str(name) for name in final.pages[0].resources["/Font"].keys()]
        assert len(names) == len(set(names))
        assert len([name for name in names if name.startswith("/PdfStudioTextFont")]) == 2


def test_direct_append_with_embedded_truetype_font(tmp_path: Path) -> None:
    import reportlab

    font = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
    assert font.is_file()
    source, target = tmp_path / "original.pdf", tmp_path / "with-font.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(75, 680, "Original line")
    canvas.save()
    add_body_text(source, target, page=1, x=75, y=600,
                  text="Résumé original", font_path=font)
    assert "Résumé original" in PdfReader(str(target)).pages[0].extract_text()
    with pikepdf.Pdf.open(target) as doc:
        page = doc.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        assert any(str(op.operator) == "Tj" for op in ops)
        assert not any(str(op.operator) == "Do" for op in ops)
        font_resources = page.resources["/Font"]
        assert any(str(key).startswith("/PdfStudioTextFont") for key in font_resources)
