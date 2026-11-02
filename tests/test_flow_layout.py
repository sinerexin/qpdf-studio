"""Multi-object PDF text flow must change real Tm, never paint over text."""
from pathlib import Path

import pikepdf
import pytest
import pypdfium2 as pdfium
from PIL import ImageChops
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.flow_layout import (
    plan_text_flow, reflow_with_following_text,
)
from pdf_studio.paragraph_reflow import list_reflow_blocks
from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document


def _simple_page(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(612, 792))
    paragraph = canvas.beginText(72, 700)
    paragraph.setFont("Helvetica", 14)
    paragraph.setLeading(19)
    paragraph.textLine("Editable paragraph")
    paragraph.textLine("has two lines")
    canvas.drawText(paragraph)
    canvas.drawString(72, 455, "Following text object")
    canvas.save()


def _operators(path: Path):
    with pikepdf.Pdf.open(path) as pdf:
        return list(pikepdf.parse_content_stream(pdf.pages[0]))


def _glyph_bounds(path: Path, text: str) -> list[float]:
    from pdf_studio.text_metrics import unique_text_bounds
    result = unique_text_bounds(path, 1, text)
    assert result is not None, text
    return result


def test_reflow_and_following_text_direct_tm_shift(tmp_path: Path) -> None:
    src, dst = tmp_path / "original.pdf", tmp_path / "flow.pdf"
    _simple_page(src)
    paragraph = next(block for block in list_reflow_blocks(src, 1)
                     if "Editable paragraph" in block["text"])
    original = src.read_bytes()
    expanded = (
        "This paragraph is being expanded with many more words. It has to "
        "consume a lot more vertical space, so the next text object should "
        "move down by the precise difference in line heights."
    )
    result = reflow_with_following_text(
        src, dst, page=1,
        start_operator_index=paragraph["start_operator_index"],
        expected_text=paragraph["text"], replacement=expanded,
        width_pt=180, height_pt=235,
    )
    plan = result["flow_plan"]
    assert plan["shift_pt"] > 0
    assert len(plan["following"]) == 1
    after = _glyph_bounds(dst, "Following text object")
    before = _glyph_bounds(src, "Following text object")
    assert abs((before[1] - after[1]) - plan["shift_pt"]) < 1.2
    assert src.read_bytes() == original
    assert "Following text object" in PdfReader(str(dst)).pages[0].extract_text()
    assert "Editable paragraph" not in PdfReader(str(dst)).pages[0].extract_text()
    old_ops, new_ops = _operators(src), _operators(dst)
    assert not any(str(op.operator) in ("Do", "re") for op in new_ops)
    assert [str(op.operator) for op in old_ops].count("Tm") == (
        [str(op.operator) for op in new_ops].count("Tm")
    )
    assert not (tmp_path / "flow.pdf.changes.json").exists()


def test_graphics_or_image_refuses_flow_without_changing_source(tmp_path: Path) -> None:
    src, revised = tmp_path / "page.pdf", tmp_path / "graphical.pdf"
    _simple_page(src)
    with pikepdf.Pdf.open(src) as pdf:
        page = pdf.pages[0]
        stream = page.obj["/Contents"].read_bytes() + b"\nq 1 0 0 rg 100 300 25 25 re f Q"
        page.obj["/Contents"] = pdf.make_stream(stream)
        pdf.save(revised)
    anchor = next(x for x in list_reflow_blocks(revised, 1)
                  if "Editable paragraph" in x["text"])
    with pytest.raises(ValueError, match="Non-text|graphics|state"):
        reflow_with_following_text(
            revised, tmp_path / "refused.pdf", page=1,
            start_operator_index=anchor["start_operator_index"],
            expected_text=anchor["text"],
            replacement="Long text " * 15, width_pt=180, height_pt=235,
        )
    assert not (tmp_path / "refused.pdf").exists()


def test_following_text_cannot_shift_below_page(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    _simple_page(src)
    with pikepdf.Pdf.open(src) as pdf:
        instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
        for index, item in enumerate(instructions):
            if str(item.operator) == "Tm" and len(item.operands) == 6:
                ops = list(item.operands)
                if float(ops[5]) == 455:
                    ops[5] = pikepdf.Integer(48)
                    instructions[index] = pikepdf.ContentStreamInstruction(ops, item.operator)
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
        pdf.save(tmp_path / "low-footer.pdf")
    low = tmp_path / "low-footer.pdf"
    selected = next(x for x in list_reflow_blocks(low, 1)
                    if "Editable paragraph" in x["text"])
    with pytest.raises(ValueError, match="margin"):
        reflow_with_following_text(
            low, tmp_path / "bad.pdf", page=1,
            start_operator_index=selected["start_operator_index"],
            expected_text=selected["text"],
            replacement="Many more text words in the paragraph that need reflow " * 3,
            width_pt=180, height_pt=235,
        )
    assert not (tmp_path / "bad.pdf").exists()


def test_signed_pdf_rejects_cross_object_movement(tmp_path: Path) -> None:
    src = tmp_path / "original.pdf"
    _simple_page(src)
    password = "Synthetic credential solely for integration test"
    folder = tmp_path / "credentials"
    generate_personal_credentials(folder, label="Synthetic test", password=password)
    signed = sign_document(src, tmp_path / "signed.pdf",
                           pfx=folder / "personal-signing.p12", password=password.encode())
    paragraph = next(x for x in list_reflow_blocks(src, 1)
                     if "Editable paragraph" in x["text"])
    with pytest.raises(ValueError, match="signed|certified"):
        reflow_with_following_text(
            signed, tmp_path / "forbidden.pdf", page=1,
            start_operator_index=paragraph["start_operator_index"],
            expected_text=paragraph["text"],
            replacement="Some changed content", width_pt=180, height_pt=235,
        )
    assert not (tmp_path / "forbidden.pdf").exists()


def test_rotated_or_cropped_pages_reject_linked_flow(tmp_path: Path) -> None:
    src = tmp_path / "original.pdf"
    _simple_page(src)
    anchor = next(x for x in list_reflow_blocks(src, 1)
                  if "Editable paragraph" in x["text"])
    with pikepdf.Pdf.open(src) as pdf:
        pdf.pages[0].obj["/Rotate"] = 90
        pdf.save(tmp_path / "rotated.pdf")
    with pytest.raises(ValueError, match="Rotated"):
        plan_text_flow(tmp_path / "rotated.pdf", page=1,
                       start_operator_index=anchor["start_operator_index"],
                       new_height_pt=130)
    with pikepdf.Pdf.open(src) as pdf:
        pdf.pages[0].obj["/CropBox"] = pikepdf.Array([0, 0, 600, 780])
        pdf.save(tmp_path / "cropped.pdf")
    with pytest.raises(ValueError, match="Cropped"):
        plan_text_flow(tmp_path / "cropped.pdf", page=1,
                       start_operator_index=anchor["start_operator_index"],
                       new_height_pt=130)


def test_annotated_pages_cannot_move_text_around_annotations(tmp_path: Path) -> None:
    src, tagged = tmp_path / "source.pdf", tmp_path / "annotated.pdf"
    _simple_page(src)
    from pdf_studio.annotations import add_note
    add_note(src, tagged, page_number=1, x=75, y=350, contents="Independent note")
    block = next(x for x in list_reflow_blocks(tagged, 1)
                 if "Editable paragraph" in x["text"])
    with pytest.raises(ValueError, match="annotations"):
        reflow_with_following_text(
            tagged, tmp_path / "blocked.pdf", page=1,
            start_operator_index=block["start_operator_index"],
            expected_text=block["text"],
            replacement="New text that would otherwise alter following positions " * 3,
            width_pt=180, height_pt=235,
        )
    assert not (tmp_path / "blocked.pdf").exists()
