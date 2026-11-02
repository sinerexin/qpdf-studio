"""Actual CIDFontType2 Identity-H PDF and multi-fragment TJ integration tests.

Uses a bundled synthetic local font only as test input. The production editor
does not require creating a new font or drawing cover layers.
"""
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
import reportlab
from PIL import Image, ImageChops
from pypdf import PdfReader
from reportlab.pdfbase.ttfonts import TTFontFace
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.replace_text import (
    list_text_runs, list_tj_spans, replace_text_run, replace_tj_span,
)
from pdf_studio.adjacent_text import (
    list_adjacent_text_spans, replace_adjacent_text_span,
)


def make_actual_type0_pdf(path: Path, *, as_tj: bool = False, extra_unpainted: str = "",
                          tounicode: bool = True) -> None:
    face_path = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
    face = TTFontFace(str(face_path))
    cid_a = face.charToGlyph[ord("A")]
    cid_b = face.charToGlyph[ord("B")]
    assert cid_a != cid_b and 0 < cid_a < 65536 and 0 < cid_b < 65536
    mapped = [("A", cid_a), ("B", cid_b)]
    for char in extra_unpainted:
        cid = face.charToGlyph[ord(char)]
        assert 0 < cid < 65536 and all(cid != existing for _, existing in mapped)
        mapped.append((char, cid))
    base = path.parent / (path.stem + "_base.pdf")
    canvas = Canvas(str(base))
    canvas.drawString(70, 770, "Base text")
    canvas.save()
    with pikepdf.Pdf.open(base) as doc:
        page = doc.pages[0]
        descriptor = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/TestCIDEmbeddedFont"),
            "/Flags": 4,
            "/FontBBox": pikepdf.Array([-500, -500, 2500, 2500]),
            "/ItalicAngle": 0, "/Ascent": 760,
            "/Descent": -240, "/CapHeight": 700,
            "/StemV": 80,
            "/FontFile2": doc.make_stream(face_path.read_bytes()),
        })
        descendant = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/TestCIDEmbeddedFont"),
            "/CIDSystemInfo": pikepdf.Dictionary({
                "/Registry": pikepdf.String("Adobe"),
                "/Ordering": pikepdf.String("Identity"),
                "/Supplement": 0,
            }),
            "/FontDescriptor": doc.make_indirect(descriptor),
            "/DW": 1000, "/CIDToGIDMap": pikepdf.Name("/Identity"),
        })
        cmap = (
            b"/CIDInit /ProcSet findresource begin\n"
            b"12 dict begin begincmap\n"
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def\n"
            b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
            b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            + f"{len(mapped)} beginbfchar\n".encode()
            + "".join(f"<{cid:04X}> <{ord(char):04X}>\n" for char, cid in mapped).encode()
            + b"endbfchar\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end"
        )
        type0 = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/TestCIDEmbeddedFont"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([doc.make_indirect(descendant)]),
        })
        if tounicode:
            type0["/ToUnicode"] = doc.make_stream(cmap)
        resources = pikepdf.Dictionary({
            str(k): v for k, v in page.resources.items()
        })
        fonts = pikepdf.Dictionary({
            str(k): v for k, v in resources["/Font"].items()
        })
        fonts["/F0"] = doc.make_indirect(type0)
        resources["/Font"] = fonts
        page.obj["/Resources"] = resources
        if as_tj:
            encoded = (
                f"BT /F0 32 Tf 80 680 Td "
                f"[<{cid_a:04X}{cid_b:04X}> -60 <{cid_a:04X}{cid_b:04X}>] TJ ET"
            )
        else:
            encoded = (
                f"BT /F0 32 Tf 80 680 Td <{cid_a:04X}{cid_b:04X}{cid_a:04X}> Tj ET"
            )
        page.obj["/Contents"] = doc.make_stream(encoded.encode("ascii"))
        doc.save(path)


def _visible_page(path: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(path))
    try:
        page = document[0]
        try:
            rendered = page.render(scale=1.3).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()
    assert ImageChops.difference(
        rendered, Image.new("RGB", rendered.size, "white")
    ).getbbox(), "Expected genuine embedded-font glyphs visible on PDF page"
    return rendered


def test_real_type0_cidfont2_direct_single_operand_edit(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    make_actual_type0_pdf(src)
    assert "ABA" in PdfReader(str(src)).pages[0].extract_text()
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "ABA")
    assert run["editable"]
    assert run["font_encoding"] == "embedded-to-unicode-cmap"
    original = src.read_bytes()
    before_image = _visible_page(src)
    report = replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                              expected_text="ABA", replacement="BAB")
    assert report["text_visible_in_extraction"]
    assert src.read_bytes() == original
    assert "BAB" in PdfReader(str(dst)).pages[0].extract_text()
    assert "ABA" not in PdfReader(str(dst)).pages[0].extract_text()
    with pikepdf.Pdf.open(src) as a, pikepdf.Pdf.open(dst) as b:
        old_ops = list(pikepdf.parse_content_stream(a.pages[0]))
        new_ops = list(pikepdf.parse_content_stream(b.pages[0]))
        assert [str(x.operator) for x in old_ops] == [str(x.operator) for x in new_ops]
        assert not any(str(x.operator) in ("Do", "re") for x in new_ops)
    after_image = _visible_page(dst)
    assert ImageChops.difference(before_image, after_image).getbbox()


def test_actual_type0_cidfont2_complete_tj_span(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    make_actual_type0_pdf(src, as_tj=True)
    assert "ABAB" in PdfReader(str(src)).pages[0].extract_text()
    runs = list_tj_spans(src, 1)
    assert len(runs) == 1 and runs[0]["text"] == "ABAB"
    result = replace_tj_span(src, dst, page=1,
                             operator_index=runs[0]["operator_index"],
                             expected_text="ABAB", replacement="BABA")
    assert result["new_text"] == "BABA"
    assert "BABA" in PdfReader(str(dst)).pages[0].extract_text()
    with pikepdf.Pdf.open(src) as a, pikepdf.Pdf.open(dst) as b:
        original = list(pikepdf.parse_content_stream(a.pages[0]))
        edited = list(pikepdf.parse_content_stream(b.pages[0]))
        assert [str(x.operator) for x in original] == [str(x.operator) for x in edited]
        old_tj = next(x for x in original if str(x.operator) == "TJ")
        new_tj = next(x for x in edited if str(x.operator) == "TJ")
        assert int(old_tj.operands[0][1]) == int(new_tj.operands[0][1]) == -60
    assert ImageChops.difference(_visible_page(src), _visible_page(dst)).getbbox()


def test_recognized_unpainted_internal_code_is_written(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    make_actual_type0_pdf(src, extra_unpainted="C")
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "ABA")
    assert "C" not in run["text"]
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="ABA", replacement="C")
    assert PdfReader(str(dst)).pages[0].extract_text().strip() == "C"


def test_embedded_cmap_supplies_display_code_without_tounicode(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    make_actual_type0_pdf(src, tounicode=False)
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "ABA")
    assert run["editable"]
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="ABA", replacement="CCC")
    written = next(row for row in list_text_runs(dst, 1)["runs"] if row["text"] == "CCC")
    assert written["editable"]
    with pikepdf.Pdf.open(dst) as pdf:
        shown = next(item for item in pikepdf.parse_content_stream(pdf.pages[0])
                     if str(item.operator) == "Tj")
        face = TTFontFace(str(Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"))
        expected = face.charToGlyph[ord("C")].to_bytes(2, "big") * 3
        assert bytes(shown.operands[0]) == expected


def test_tj_span_rejects_unsafe_length_and_unmapped_chars(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    make_actual_type0_pdf(src, as_tj=True)
    index = list_tj_spans(src, 1)[0]["operator_index"]
    with pytest.raises(ValueError, match="equal"):
        replace_tj_span(src, tmp_path / "not-saved.pdf", page=1,
                        operator_index=index, expected_text="ABAB", replacement="BAB")
    # 低层原字体 API 发出可接续的换字体信号，自动编辑入口负责嵌入处理。
    with pytest.raises(ValueError, match="需要嵌入"):
        replace_tj_span(src, tmp_path / "not-saved.pdf", page=1,
                        operator_index=index, expected_text="ABAB", replacement="AB中文")
    assert not (tmp_path / "not-saved.pdf").exists()


def test_real_type0_cidfont2_consecutive_text_operators(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    make_actual_type0_pdf(src, as_tj=True)
    with pikepdf.Pdf.open(src) as pdf:
        operations = list(pikepdf.parse_content_stream(pdf.pages[0]))
        tj = next(ins for ins in operations if str(ins.operator) == "TJ")
        first = bytes(tj.operands[0][0])
        second = bytes(tj.operands[0][2])
        stream = (
            b"BT /F0 32 Tf 80 680 Td <" + first.hex().encode() +
            b"> Tj <" + second.hex().encode() + b"> Tj ET"
        )
        pdf.pages[0].obj["/Contents"] = pdf.make_stream(stream)
        pdf.save(tmp_path / "consecutive.pdf")
    prepared = tmp_path / "consecutive.pdf"
    assert "ABAB" in PdfReader(str(prepared)).pages[0].extract_text()
    spans = list_adjacent_text_spans(prepared, 1)
    assert len(spans) == 1 and spans[0]["text"] == "ABAB"
    report = replace_adjacent_text_span(
        prepared, dst, page=1,
        start_operator_index=spans[0]["start_operator_index"],
        expected_text="ABAB", replacement="BABA",
    )
    assert report["new_text"] == "BABA"
    assert "BABA" in PdfReader(str(dst)).pages[0].extract_text()
    assert ImageChops.difference(_visible_page(prepared), _visible_page(dst)).getbbox()
