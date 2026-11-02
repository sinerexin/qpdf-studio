from pathlib import Path

from reportlab.pdfgen.canvas import Canvas
from pdf_studio.purepdf import PdfDocument, PdfSyntaxError, PdfStream, Ref, Scanner


def test_independent_parser_can_inspect_pdf(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.pdf"
    canvas = Canvas(str(path), pageCompression=1)
    canvas.drawString(70, 700, "Python-only parser")
    canvas.showPage()
    canvas.drawString(70, 700, "Page two")
    canvas.save()
    doc = PdfDocument.open(path)
    result = doc.report()
    assert result["revision_count"] == 1
    assert result["page_count"] == 2
    assert result["xref_entries"] >= 5
    assert not result["warnings"], result["warnings"]
    assert any(
        isinstance(doc.get(ref), PdfStream)
        for ref in doc.xref if ref.number > 0
    )


def test_syntax_and_invalid_pdf_fail_closed() -> None:
    assert Scanner(b"[12 0 R /Root (Hello\\nWorld)]").parse() == [
        Ref(12, 0), __import__("pdf_studio.purepdf", fromlist=["PdfName"]).PdfName("Root"),
        b"Hello\nWorld",
    ]
    try:
        PdfDocument(b"not a PDF")
    except PdfSyntaxError:
        pass
    else:
        raise AssertionError("Invalid PDF unexpectedly accepted")


def test_modern_compressed_xref_and_objstream(tmp_path: Path) -> None:
    # pikepdf here only prepares a modern fixture; the parser itself imports
    # nothing from qpdf and must decode its result using the Python stdlib.
    import pikepdf
    source = tmp_path / "source.pdf"
    modern = tmp_path / "modern.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(100, 500, "Modern PDF with XRef Stream")
    canvas.save()
    with pikepdf.Pdf.open(source) as pdf:
        pdf.save(modern, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    doc = PdfDocument.open(modern)
    report = doc.report()
    assert report["page_count"] == 1
    assert report["compressed_objects"] > 0
    assert report["xref_entries"] > 5
    assert not report["warnings"], report["warnings"]
