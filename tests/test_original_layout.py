"""Layout extraction from actual original PDF, not a manually authored template."""
from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.original_layout import (
    read_original_layout, plan_original_reflow, reflow_like_original,
)
from pdf_studio.paragraph_reflow import list_reflow_blocks
from pdf_studio.text_metrics import unique_text_bounds


def _source(path: Path, *, font_size: float = 14,
            leading: float = 19, x: float = 72) -> None:
    c = Canvas(str(path), pagesize=(612, 792))
    t = c.beginText(x, 690)
    t.setFont("Helvetica", font_size)
    t.setLeading(leading)
    t.textLine("Original article title and text")
    t.textLine("More text follows here")
    c.drawText(t)
    c.drawString(x, 390, "Following paragraph text")
    c.save()


def test_source_driven_layout_inherits_font_and_leading_and_shifts_flow(tmp_path: Path) -> None:
    source, target = tmp_path / "source.pdf", tmp_path / "edited.pdf"
    _source(source, font_size=16, leading=25, x=86)
    original_bytes = source.read_bytes()
    block = next(row for row in list_reflow_blocks(source, 1)
                 if "Original article" in row["text"])
    profile = read_original_layout(
        source, page=1, start_operator_index=block["start_operator_index"],
    )
    assert profile["observed"]["font_size_pt"] == 16
    assert profile["observed"]["line_leading_pt"] == 25
    assert profile["observed"]["origin_pdf"] == [86.0, 690.0]
    assert profile["inferred"]["is_exact_author_layout_template"] is False
    assert profile["inferred"]["max_line_width_pt"] > max(
        profile["observed"]["observed_line_widths_pt"]
    )
    assert profile["inferred"]["layout_confidence"] in {"low", "medium"}
    new_content = (
        "This much longer article is carefully rewritten using the original "
        "document font, the original leading and the same left alignment. "
        "The software should move the following paragraph only when safe."
    )
    plan = plan_original_reflow(
        source, page=1, start_operator_index=block["start_operator_index"],
        replacement=new_content,
    )
    assert len(plan["wrapped_lines"]) > 2
    assert plan["flow_plan"]["following"]
    before_footer = unique_text_bounds(source, 1, "Following paragraph text")
    report = reflow_like_original(
        source, target, page=1, start_operator_index=block["start_operator_index"],
        expected_text=block["text"], replacement=new_content,
    )
    after_footer = unique_text_bounds(target, 1, "Following paragraph text")
    assert after_footer and before_footer
    assert after_footer[1] < before_footer[1] - 3
    assert original_bytes == source.read_bytes()
    assert "Original article title and text" not in PdfReader(str(target)).pages[0].extract_text()
    assert report["original_layout"]["source_layout"]["observed"]["font_size_pt"] == 16
    assert not (tmp_path / "edited.pdf.changes.json").exists()
    assert report["operation"] == "reflow-according-to-original-pdf-layout-evidence"
    with pikepdf.Pdf.open(source) as before, pikepdf.Pdf.open(target) as after:
        original_ops = list(pikepdf.parse_content_stream(before.pages[0]))
        edited_ops = list(pikepdf.parse_content_stream(after.pages[0]))
        assert not any(str(op.operator) in ("re", "Do") for op in edited_ops)
        assert [str(op.operator) for op in original_ops].count("Tm") == [
            str(op.operator) for op in edited_ops
        ].count("Tm")


def test_original_layout_inference_explicitly_not_exact_source_template(tmp_path: Path) -> None:
    source = tmp_path / "single.pdf"
    c = Canvas(str(source), pagesize=(612, 792))
    text = c.beginText(92, 690)
    text.setFont("Helvetica", 12)
    text.setLeading(16)
    text.textLine("A short phrase")
    c.drawText(text)
    c.save()
    block = next(row for row in list_reflow_blocks(source, 1)
                 if "short phrase" in row["text"])
    profile = read_original_layout(
        source, page=1, start_operator_index=block["start_operator_index"],
    )
    assert profile["inferred"]["layout_confidence"] == "low"
    assert profile["inferred"]["is_exact_author_layout_template"] is False
    assert any("inferred" in msg for msg in profile["warnings"])


def test_original_layout_refuses_non_text_interleaved_page(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    _source(original)
    with pikepdf.Pdf.open(original) as pdf:
        page = pdf.pages[0]
        page.obj["/Contents"] = pdf.make_stream(
            page.obj["/Contents"].read_bytes()
            + b"\nq 1 0 0 rg 200 350 20 20 re f Q"
        )
        pdf.save(tmp_path / "graphic.pdf")
    graphic = tmp_path / "graphic.pdf"
    block = next(r for r in list_reflow_blocks(graphic, 1)
                 if "Original article" in r["text"])
    with pytest.raises(ValueError, match="Non-text|graphics|state"):
        reflow_like_original(
            graphic, tmp_path / "must-not-exist.pdf", page=1,
            start_operator_index=block["start_operator_index"],
            expected_text=block["text"], replacement="A longer paragraph" * 8,
        )
    assert not (tmp_path / "must-not-exist.pdf").exists()


def test_original_layout_uses_embedded_type0_cid_widths(tmp_path: Path) -> None:
    from test_type0_cid_and_tj_span import make_actual_type0_pdf
    raw = tmp_path / "cid-base.pdf"
    original, modified = tmp_path / "cid-source.pdf", tmp_path / "cid-output.pdf"
    make_actual_type0_pdf(raw)
    with pikepdf.Pdf.open(raw) as pdf:
        content = next(
            op for op in pikepdf.parse_content_stream(pdf.pages[0])
            if str(op.operator) == "Tj"
        )
        encoded = bytes(content.operands[0]).hex().encode()
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(
            b"BT /F0 24 Tf 30 TL 1 0 0 1 80 670 Tm <"
            + encoded + b"> Tj ET"
        )
        pdf.save(original)
    block = next(r for r in list_reflow_blocks(original, 1)
                 if r["text"] == "ABA")
    layout = read_original_layout(
        original, page=1,
        start_operator_index=block["start_operator_index"],
    )
    assert layout["observed"]["font_resource"] == "/F0"
    assert layout["observed"]["font_size_pt"] == 24
    assert layout["observed"]["line_leading_pt"] == 30
    result = reflow_like_original(
        original, modified, page=1,
        start_operator_index=block["start_operator_index"],
        expected_text="ABA",
        replacement="BABABABABABA",
    )
    assert result["after_line_count"] > 1
    assert (
        PdfReader(str(modified)).pages[0].extract_text().replace("\n", "")
        == "BABABABABABA"
    )
