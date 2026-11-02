from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.replace_text import list_text_runs, replace_text_run, glyph_near_pdf_point, pdf_font_size


def test_direct_text_stream_replacement_not_visual_cover(tmp_path: Path) -> None:
    src, dest = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    draw = Canvas(str(src))
    draw.drawString(80, 700, "Alpha Beta phrase")
    draw.save()
    original = src.read_bytes()
    rows = list_text_runs(src, 1)
    candidates = [row for row in rows["runs"] if row["editable"]]
    assert candidates, rows
    candidate = next(row for row in candidates if row["text"] == "Alpha Beta phrase")
    hint = glyph_near_pdf_point(src, page=1, x=82, y=702)
    assert hint and "Alpha" in hint["text_hint"], hint
    report = replace_text_run(
        src, dest, page=1, operator_index=candidate["operator_index"],
        expected_text=candidate["text"], replacement="Gamma Delta phrase",
    )
    assert report["text_visible_in_extraction"]
    assert src.read_bytes() == original
    assert "Gamma Delta phrase" in PdfReader(str(dest)).pages[0].extract_text()
    assert "Alpha Beta phrase" not in PdfReader(str(dest)).pages[0].extract_text()
    with pikepdf.Pdf.open(dest) as pdf:
        operators = list(pikepdf.parse_content_stream(pdf.pages[0]))
        texts = [str(inst.operands[0]) for inst in operators
                 if str(inst.operator) == "Tj"]
        assert "Alpha Beta phrase" not in texts
        assert "Gamma Delta phrase" in texts
    assert not (tmp_path / "changed.pdf.changes.json").exists()
    with pytest.raises(ValueError):
        replace_text_run(src, src, page=1, operator_index=candidate["operator_index"],
                         expected_text=candidate["text"], replacement="overwrite")


def test_font_size_is_read_from_pdf_tf_and_tm(tmp_path: Path) -> None:
    src = tmp_path / "sized.pdf"
    draw = Canvas(str(src))
    draw.setFont("Helvetica", 17)
    draw.drawString(80, 700, "SizedText")
    draw.save()
    row = next(item for item in list_text_runs(src, 1)["runs"] if item["text"] == "SizedText")
    assert abs(row["font_size"] - 17) < 0.05
    scaled = tmp_path / "scaled.pdf"
    with pikepdf.Pdf.open(src) as pdf:
        font_name = row["font_resource"]
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(
            f"BT {font_name} 1 Tf 15 0 0 15 80 700 Tm (SizedText) Tj ET".encode("latin1")
        )
        pdf.save(scaled)
    scaled_row = next(item for item in list_text_runs(scaled, 1)["runs"] if item["text"] == "SizedText")
    assert abs(scaled_row["tf_size"] - 1) < 0.05
    assert abs(scaled_row["font_size"] - 15) < 0.05
    with pikepdf.Pdf.open(scaled) as pdf:
        assert abs(pdf_font_size(pdf.pages[0], font_name, 1, (15, 0, 0, 15, 80, 700)) - 15) < 0.05


def test_array_of_content_streams_can_be_replaced(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    draw = Canvas(str(src))
    draw.drawString(80, 700, "Alpha Beta phrase")
    draw.save()
    split = tmp_path / "split.pdf"
    with pikepdf.Pdf.open(src) as pdf:
        data = pdf.pages[0].obj["/Contents"].read_bytes()
        marker = data.find(b"BT")
        assert marker > 0
        pdf.pages[0].obj["/Contents"] = pikepdf.Array([
            pdf.make_stream(data[:marker]),
            pdf.make_stream(data[marker:]),
        ])
        pdf.save(split)
    rows = list_text_runs(split, 1)
    candidate = next(row for row in rows["runs"] if row["text"] == "Alpha Beta phrase")
    dest = tmp_path / "changed.pdf"
    replace_text_run(
        split, dest, page=1, operator_index=candidate["operator_index"],
        expected_text=candidate["text"], replacement="Gamma Delta phrase",
    )
    assert "Gamma Delta phrase" in PdfReader(str(dest)).pages[0].extract_text()
    with pikepdf.Pdf.open(dest) as pdf:
        assert isinstance(pdf.pages[0].obj["/Contents"], pikepdf.Stream)


def test_replacement_with_unknown_glyphs_rejected(tmp_path: Path) -> None:
    src = tmp_path / "src.pdf"
    canvas = Canvas(str(src))
    canvas.drawString(75, 650, "Ordinary text")
    canvas.save()
    row = next(item for item in list_text_runs(src, 1)["runs"] if item["editable"])
    with pytest.raises(ValueError, match="不能编码|glyph"):
        replace_text_run(src, tmp_path / "unsafe.pdf", page=1,
                         operator_index=row["operator_index"],
                         expected_text=row["text"], replacement="中文替换")


def test_direct_edit_tj_array_preserves_other_text_and_spacing(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    dst = tmp_path / "result.pdf"
    draw = Canvas(str(src))
    draw.drawString(60, 600, "Original")
    draw.save()
    with pikepdf.Pdf.open(src) as pdf:
        contents = b"BT /F1 12 Tf 72 540 Td [(Alpha) -100 ( Beta)] TJ ET"
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(contents)
        pdf.save(tmp_path / "prepared.pdf")
    prepared = tmp_path / "prepared.pdf"
    candidates = list_text_runs(prepared, 1)["runs"]
    choice = next(item for item in candidates if item["text"] == "Alpha")
    assert choice["array_index"] == 0
    result = replace_text_run(prepared, dst, page=1,
                              operator_index=choice["operator_index"],
                              array_index=choice["array_index"],
                              expected_text="Alpha", replacement="Gamma")
    assert result["array_index"] == 0
    assert "Gamma Beta" in PdfReader(str(dst)).pages[0].extract_text()
    with pikepdf.Pdf.open(dst) as pdf:
        stream = pikepdf.parse_content_stream(pdf.pages[0])
        op = next(obj for obj in stream if str(obj.operator) == "TJ")
        assert str(op.operands[0][0]) == "Gamma"
        assert float(op.operands[0][1]) == -100
        assert str(op.operands[0][2]) == " Beta"
