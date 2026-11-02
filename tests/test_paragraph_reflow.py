"""Variable-length line wrapping by direct rewrite of existing PDF text operands."""
from pathlib import Path

import pikepdf
import pytest
import pypdfium2 as pdfium
from PIL import Image, ImageChops
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.paragraph_reflow import list_reflow_blocks, reflow_text_object


def _plain_document(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(612, 792))
    text = canvas.beginText(72, 700)
    text.setFont("Helvetica", 14)
    text.setLeading(19)
    text.textLine("Original paragraph")
    text.textLine("has two lines")
    canvas.drawText(text)
    canvas.drawString(72, 455, "Unrelated footer content")
    canvas.save()


def _picture(path: Path):
    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[0]
        try:
            return page.render(scale=1.2).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        doc.close()


def test_simple_paragraph_variable_length_rewrap_without_cover(tmp_path: Path) -> None:
    src, out = tmp_path / "original.pdf", tmp_path / "reflow.pdf"
    _plain_document(src)
    raw = src.read_bytes()
    options = list_reflow_blocks(src, 1)
    paragraph = next(entry for entry in options if "Original paragraph" in entry["text"])
    new_text = (
        "This completely different and longer paragraph should break into "
        "multiple physical PDF text lines without any covered text or "
        "new graphic drawing layers."
    )
    report = reflow_text_object(
        src, out, page=1, start_operator_index=paragraph["start_operator_index"],
        expected_text=paragraph["text"], replacement=new_text,
        width_pt=210, height_pt=225,
    )
    assert report["after_line_count"] > report["before_line_count"]
    assert report["height_used_pt"] <= 225
    assert report["source_sha256"] != report["output_sha256"]
    assert not (tmp_path / "reflow.pdf.changes.json").exists()
    assert src.read_bytes() == raw
    original_text = PdfReader(str(src)).pages[0].extract_text()
    edited_text = PdfReader(str(out)).pages[0].extract_text()
    assert "Original paragraph" in original_text
    assert "Original paragraph" not in edited_text
    assert "completely different" in edited_text
    assert "Unrelated footer content" in edited_text
    with pikepdf.Pdf.open(src) as before, pikepdf.Pdf.open(out) as after:
        previous = list(pikepdf.parse_content_stream(before.pages[0]))
        updated = list(pikepdf.parse_content_stream(after.pages[0]))
        assert [str(ins.operator) for ins in updated].count("Tj") > (
            [str(ins.operator) for ins in previous].count("Tj")
        )
        assert not any(str(ins.operator) in {"Do", "re"} for ins in updated)
        assert sum(
            "Unrelated footer content" in str(v)
            for ins in updated for v in ins.operands
        ) == 1
    assert ImageChops.difference(_picture(src), _picture(out)).getbbox()


def test_reflow_rejects_no_room_and_does_not_create_output(tmp_path: Path) -> None:
    src = tmp_path / "original.pdf"
    _plain_document(src)
    paragraph = next(e for e in list_reflow_blocks(src, 1)
                     if "Original paragraph" in e["text"])
    with pytest.raises(ValueError, match="does not fit|exceeds"):
        reflow_text_object(
            src, tmp_path / "bad.pdf", page=1,
            start_operator_index=paragraph["start_operator_index"],
            expected_text=paragraph["text"],
            replacement="hello " * 40,
            width_pt=65, height_pt=28,
        )
    assert not (tmp_path / "bad.pdf").exists()


def test_reflow_rejects_nonidentity_text_transform(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    _plain_document(src)
    with pikepdf.Pdf.open(src) as pdf:
        commands = list(pikepdf.parse_content_stream(pdf.pages[0]))
        for index, ins in enumerate(commands):
            if str(ins.operator) == "Tm":
                operands = list(ins.operands)
                operands[0] = pikepdf.Integer(0)
                commands[index] = pikepdf.ContentStreamInstruction(
                    operands, ins.operator,
                )
                break
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(commands))
        pdf.save(tmp_path / "rotated-text.pdf")
    assert not any("Original paragraph" in e["text"]
                   for e in list_reflow_blocks(tmp_path / "rotated-text.pdf", 1))


def test_consecutive_tj_without_line_break_is_not_mistaken_for_a_paragraph(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    _plain_document(source)
    with pikepdf.Pdf.open(source) as doc:
        doc.pages[0].obj["/Contents"] = doc.make_stream(
            b"BT /F1 14 Tf 19 TL 1 0 0 1 72 700 Tm "
            b"(Hello) Tj (World) Tj ET"
        )
        doc.save(tmp_path / "joined.pdf")
    assert not list_reflow_blocks(tmp_path / "joined.pdf", 1)


def test_signed_pdf_can_be_reflowed_to_separate_output(tmp_path: Path) -> None:
    from pdf_studio.keygen import generate_personal_credentials
    from pdf_studio.signing import sign_document

    source = tmp_path / "original.pdf"
    _plain_document(source)
    original_block = next(b for b in list_reflow_blocks(source, 1)
                          if "Original paragraph" in b["text"])
    password = "Synthetic test-only reflow password 2026!"
    directory = tmp_path / "personal-key"
    generate_personal_credentials(
        directory, label="Synthetic Reflow Test", password=password,
    )
    signed = sign_document(
        source, tmp_path / "signed.pdf", pfx=directory / "personal-signing.p12",
        password=password.encode("utf-8"),
    )
    reflow_text_object(
        signed, tmp_path / "must-not-write.pdf", page=1,
        start_operator_index=original_block["start_operator_index"],
        expected_text=original_block["text"],
        replacement="Changed after signing",
        width_pt=220, height_pt=180,
    )
    assert (tmp_path / "must-not-write.pdf").exists()


def test_type0_identity_h_reflows_only_available_glyphs(tmp_path: Path) -> None:
    from test_type0_cid_and_tj_span import make_actual_type0_pdf
    src = tmp_path / "cid-source.pdf"
    prepared = tmp_path / "cid-paragraph.pdf"
    out = tmp_path / "cid-edited.pdf"
    make_actual_type0_pdf(src)
    with pikepdf.Pdf.open(src) as pdf:
        commands = list(pikepdf.parse_content_stream(pdf.pages[0]))
        tj = next(ins for ins in commands if str(ins.operator) == "Tj")
        old_binary = bytes(tj.operands[0])
        sheet = pdf.pages[0]
        sheet.obj["/Contents"] = pdf.make_stream(
            b"BT /F0 24 Tf 29 TL 1 0 0 1 80 680 Tm <"
            + old_binary.hex().encode("ascii") + b"> Tj ET"
        )
        pdf.save(prepared)
    para = next(e for e in list_reflow_blocks(prepared, 1) if "ABA" in e["text"])
    report = reflow_text_object(
        prepared, out, page=1,
        start_operator_index=para["start_operator_index"],
        expected_text="ABA", replacement="BABABABABABABA",
        width_pt=90, height_pt=200,
    )
    assert report["after_line_count"] >= 4
    normalized = PdfReader(str(out)).pages[0].extract_text().replace("\n", "")
    assert normalized == "BABABABABABABA", normalized
    assert ImageChops.difference(_picture(prepared), _picture(out)).getbbox()
    with pytest.raises(ValueError, match="需要嵌入"):
        reflow_text_object(
            prepared, tmp_path / "unsupported.pdf", page=1,
            start_operator_index=para["start_operator_index"],
            expected_text="ABA", replacement="AB中",
            width_pt=90, height_pt=200,
        )
