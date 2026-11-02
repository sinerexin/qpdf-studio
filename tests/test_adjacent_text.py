from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.adjacent_text import (
    list_adjacent_text_spans, replace_adjacent_text_span,
)


def _document(path: Path, *, separated: bool = False) -> None:
    base = path.parent / "base.pdf"
    canvas = Canvas(str(base))
    canvas.drawString(80, 700, "Baseline")
    canvas.save()
    with pikepdf.Pdf.open(base) as pdf:
        segments = (
            b"BT /F1 12 Tf 90 600 Td (Alpha) Tj "
            + (b"20 0 Td " if separated else b"")
            + b"(Beta) Tj (Gamma) Tj ET"
        )
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(segments)
        pdf.save(path)


def test_adjacent_tj_operators_are_edited_without_new_paint(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "edited.pdf"
    _document(src)
    spans = list_adjacent_text_spans(src, 1)
    span = next(x for x in spans if x["text"] == "AlphaBetaGamma")
    original = src.read_bytes()
    result = replace_adjacent_text_span(
        src, dst, page=1, start_operator_index=span["start_operator_index"],
        expected_text="AlphaBetaGamma", replacement="DeltaZetaOmega",
    )
    assert result["new_text"] == "DeltaZetaOmega"
    assert src.read_bytes() == original
    text = PdfReader(str(dst)).pages[0].extract_text()
    assert "Delta" in text and "Omega" in text
    assert "Alpha" not in text and "Gamma" not in text
    with pikepdf.Pdf.open(src) as old, pikepdf.Pdf.open(dst) as new:
        operators_old = list(pikepdf.parse_content_stream(old.pages[0]))
        operators_new = list(pikepdf.parse_content_stream(new.pages[0]))
        assert [str(op.operator) for op in operators_old] == [
            str(op.operator) for op in operators_new
        ]
        assert len(operators_new) == len(operators_old)
        assert not any(str(op.operator) in {"re", "Do"} for op in operators_new)


def test_positioning_instruction_breaks_editable_group(tmp_path: Path) -> None:
    src = tmp_path / "positioned.pdf"
    _document(src, separated=True)
    spans = list_adjacent_text_spans(src, 1)
    assert not any(span["text"] == "AlphaBetaGamma" for span in spans)
    with pytest.raises(ValueError, match="no longer match"):
        replace_adjacent_text_span(
            src, tmp_path / "should-not-exist.pdf", page=1,
            start_operator_index=3,
            expected_text="AlphaBetaGamma", replacement="DeltaZetaOmega",
        )
    assert not (tmp_path / "should-not-exist.pdf").exists()


def test_layout_width_change_is_reported_not_silently_reflowed(tmp_path: Path) -> None:
    source, dest = tmp_path / "source.pdf", tmp_path / "edited.pdf"
    canvas = Canvas(str(source))
    canvas.setFont("Helvetica", 20)
    canvas.drawString(72, 700, "AAAA")
    canvas.save()
    from pdf_studio.replace_text import list_text_runs, replace_text_run

    row = next(r for r in list_text_runs(source, 1)["runs"] if r["text"] == "AAAA")
    report = replace_text_run(
        source, dest, page=1, operator_index=row["operator_index"],
        expected_text="AAAA", replacement="WWWW",
    )
    layout = report["layout_diagnostics"]
    assert layout["status"] == "measured", layout
    assert layout["after_width_pt"] > layout["before_width_pt"]
    assert layout["layout_review_recommended"] is True
