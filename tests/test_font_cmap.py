"""Exercise direct text editing of genuine embedded font & ToUnicode samples."""
from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
import reportlab

from pdf_studio.font_cmap import (
    FontEmbeddingRequired, _cff_gid_to_cid, _sfnt_unicode_to_gid, _standard_codec,
    _tounicode_width, parse_to_unicode,
)
from pdf_studio.replace_text import list_text_runs, replace_text_run


def test_embedded_truetype_subset_reorders_existing_glyphs_without_cover(tmp_path: Path) -> None:
    font = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
    assert font.exists()
    pdfmetrics.registerFont(TTFont("VeraFontCMapTest", str(font)))
    src, target = tmp_path / "source.pdf", tmp_path / "replaced.pdf"
    c = Canvas(str(src))
    c.setFont("VeraFontCMapTest", 17)
    c.drawString(75, 720, "Alpha Beta")
    c.save()
    before = src.read_bytes()
    inventory = list_text_runs(src, 1)
    match = next(run for run in inventory["runs"] if run["text"] == "Alpha Beta")
    assert match["font_encoding"] == "embedded-to-unicode-cmap", inventory
    assert match["editable"] is True
    result = replace_text_run(
        src, target, page=1, operator_index=match["operator_index"],
        expected_text="Alpha Beta", replacement="Beta Alpha",
    )
    assert result["font_encoding"] == "embedded-to-unicode-cmap"
    assert result["text_visible_in_extraction"]
    assert src.read_bytes() == before
    text = PdfReader(str(target)).pages[0].extract_text()
    assert "Beta Alpha" in text
    assert "Alpha Beta" not in text
    with pikepdf.Pdf.open(src) as a, pikepdf.Pdf.open(target) as b:
        source_ops = list(pikepdf.parse_content_stream(a.pages[0]))
        result_ops = list(pikepdf.parse_content_stream(b.pages[0]))
        assert [str(op.operator) for op in source_ops] == [
            str(op.operator) for op in result_ops
        ]  # no extra masking or drawing operations


def test_recognized_internal_code_is_used_when_not_painted(tmp_path: Path) -> None:
    src = tmp_path / "original.pdf"
    c = Canvas(str(src))
    c.setFont("VeraFontCMapTest", 16)
    c.drawString(75, 700, "Alpha Beta")
    c.save()
    row = next(run for run in list_text_runs(src, 1)["runs"]
               if run["text"] == "Alpha Beta")
    # G、m、Z 没出现在原文里，但原字体 ToUnicode 已给出内部代码，直接写回。
    replaced = tmp_path / "replaced.pdf"
    result = replace_text_run(
        src, replaced, page=1, operator_index=row["operator_index"],
        expected_text=row["text"], replacement="Gamma Z",
    )
    assert result["text_visible_in_extraction"]
    assert "Gamma Z" in PdfReader(str(replaced)).pages[0].extract_text()
    with pytest.raises(FontEmbeddingRequired, match="需要嵌入"):
        replace_text_run(
            src, tmp_path / "rejected.pdf", page=1,
            operator_index=row["operator_index"],
            expected_text=row["text"], replacement="中文",
        )
    assert not (tmp_path / "rejected.pdf").exists()


def test_two_byte_cmap_bfchar_and_bfrange_codecs() -> None:
    cmap = b"""begincmap
2 beginbfchar
<0001> <4E2D>
<0002> <6587>
endbfchar
1 beginbfrange
<0003> <0004> <0041>
endbfrange
endcmap"""
    codec = parse_to_unicode(cmap, code_width=2)
    assert codec is not None
    assert codec.decode(bytes.fromhex("0001000200030004")) == "中文AB"
    assert codec.encode("文中", observed_codes={
        bytes.fromhex("0001"), bytes.fromhex("0002"),
    }) == bytes.fromhex("00020001")
    # A 的内部代码在 bfrange 里，即使本页没画过这个代码也直接使用。
    assert codec.encode_if_available('A', observed_codes={bytes.fromhex('0001')}) == bytes.fromhex('0003')
    assert codec.encode("A", observed_codes={bytes.fromhex("0001")}) == bytes.fromhex("0003")
    assert codec.encode_if_available('缺', observed_codes=set()) is None
    with pytest.raises(FontEmbeddingRequired, match="需要嵌入"):
        codec.encode("缺", observed_codes=set())


def _index(blobs: list[bytes]) -> bytes:
    if not blobs:
        return b"\x00\x00"
    offsets = [1]
    for blob in blobs:
        offsets.append(offsets[-1] + len(blob))
    assert offsets[-1] < 256
    return (
        len(blobs).to_bytes(2, "big") + bytes([1])
        + b"".join(bytes([item]) for item in offsets) + b"".join(blobs)
    )


def _cff_font(extra_cids: list[int]) -> bytes:
    charset = bytes([0]) + b"".join(cid.to_bytes(2, "big") for cid in extra_cids)
    header = bytes([1, 0, 4, 1])
    name = _index([b"A"])
    prefix_without_dict = len(header) + len(name) + 22 + 4
    charset_at = prefix_without_dict
    charstrings_at = charset_at + len(charset)

    def long_op(value: int, operator: int) -> bytes:
        return bytes([29]) + value.to_bytes(4, "big") + bytes([operator])

    dictionary = long_op(charset_at, 15) + long_op(charstrings_at, 17) + bytes([139, 139, 139, 12, 30])
    assert len(dictionary) == 17
    return (
        header + name + _index([dictionary]) + b"\x00\x00\x00\x00"
        + charset + _index([b"\x0e"] * (1 + len(extra_cids)))
    )


def _sfnt(tables: dict[bytes, bytes]) -> bytes:
    tags = list(tables)
    directory = 12 + len(tags) * 16
    header = b"OTTO" + len(tags).to_bytes(2, "big") + b"\x00" * 6
    records = b""
    body = b""
    offset = directory
    for tag in tags:
        if offset % 4:
            body += b"\x00" * (4 - offset % 4)
            offset += 4 - offset % 4
        data = tables[tag]
        records += tag + b"\x00\x00\x00\x00" + offset.to_bytes(4, "big") + len(data).to_bytes(4, "big")
        body += data
        offset += len(data)
    return header + records + body


def _format4_cmap(mapping: dict[int, int]) -> bytes:
    ordered = sorted(mapping)
    starts = ordered + [0xFFFF]
    ends = ordered + [0xFFFF]
    deltas = [(mapping[code] - code) & 0xFFFF for code in ordered] + [1]
    seg_count = len(starts)
    sub = bytearray(b"\x00\x04")
    body = bytearray()
    body += (seg_count * 2).to_bytes(2, "big")
    body += (4).to_bytes(2, "big") + (1).to_bytes(2, "big") + (seg_count * 2 - 4).to_bytes(2, "big")
    for code in ends:
        body += code.to_bytes(2, "big")
    body += b"\x00\x00"
    for code in starts:
        body += code.to_bytes(2, "big")
    for delta in deltas:
        body += delta.to_bytes(2, "big")
    body += b"\x00\x00" * seg_count
    sub += (len(body) + 6).to_bytes(2, "big") + b"\x00\x00" + body
    record = b"\x00\x03\x00\x01" + (12).to_bytes(4, "big")
    return b"\x00\x00\x00\x01" + record + bytes(sub)


def test_cff_charset_and_cmap_are_the_display_codes() -> None:
    cff = _cff_font([10, 20])
    assert _cff_gid_to_cid(cff) == [0, 10, 20]
    font = _sfnt({b"cmap": _format4_cmap({0x41: 2, 0x42: 1}), b"CFF ": cff})
    assert _sfnt_unicode_to_gid(font)[0x41] == 2
    assert _sfnt_unicode_to_gid(font)[0x42] == 1


def test_cff_font_without_tounicode_uses_embedded_codes(tmp_path: Path) -> None:
    font = _sfnt({b"cmap": _format4_cmap({0x41: 2, 0x42: 1}), b"CFF ": _cff_font([10, 20])})
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    canvas = Canvas(str(tmp_path / "base.pdf"))
    canvas.drawString(20, 20, "x")
    canvas.save()
    with pikepdf.Pdf.open(tmp_path / "base.pdf") as doc:
        page = doc.pages[0]
        descriptor = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/CustomCFF"),
            "/Flags": 4, "/FontBBox": pikepdf.Array([0, 0, 1000, 1000]),
            "/ItalicAngle": 0, "/Ascent": 800, "/Descent": -200,
            "/CapHeight": 700, "/StemV": 80,
            "/FontFile3": doc.make_stream(font),
        })
        descendant = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType0"),
            "/BaseFont": pikepdf.Name("/CustomCFF"),
            "/CIDSystemInfo": pikepdf.Dictionary({
                "/Registry": "Adobe", "/Ordering": "Identity", "/Supplement": 0,
            }),
            "/FontDescriptor": doc.make_indirect(descriptor),
            "/DW": 1000,
        })
        type0 = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/CustomCFF"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([doc.make_indirect(descendant)]),
        })
        fonts = pikepdf.Dictionary({str(key): value for key, value in page.resources["/Font"].items()})
        fonts["/F9"] = doc.make_indirect(type0)
        page.obj["/Resources"]["/Font"] = fonts
        page.obj["/Contents"] = doc.make_stream(b"BT /F9 24 Tf 80 700 Td <0014> Tj ET")
        doc.save(src)
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "A")
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="A", replacement="B")
    assert next(row["text"] for row in list_text_runs(dst, 1)["runs"] if row["text"] == "B") == "B"
    with pikepdf.Pdf.open(dst) as pdf:
        shown = next(item for item in pikepdf.parse_content_stream(pdf.pages[0]) if str(item.operator) == "Tj")
        assert bytes(shown.operands[0]) == bytes.fromhex("000A")


def test_cff_tounicode_is_read_for_any_encoding(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    canvas = Canvas(str(tmp_path / "base.pdf"))
    canvas.drawString(20, 20, "x")
    canvas.save()
    cmap = (
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"2 beginbfchar\n<000A> <0041>\n<000B> <0042>\nendbfchar\n"
    )
    with pikepdf.Pdf.open(tmp_path / "base.pdf") as doc:
        page = doc.pages[0]
        descriptor = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/NamedCMapFace"),
            "/Flags": 4, "/FontBBox": pikepdf.Array([0, 0, 1000, 1000]),
            "/ItalicAngle": 0, "/Ascent": 800, "/Descent": -200,
            "/CapHeight": 700, "/StemV": 80,
            "/FontFile3": doc.make_stream(b"\x01\x00raw-cff"),
        })
        descendant = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType0"),
            "/BaseFont": pikepdf.Name("/NamedCMapFace"),
            "/FontDescriptor": doc.make_indirect(descriptor),
        })
        type0 = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/NamedCMapFace"),
            "/Encoding": pikepdf.Name("/GBK-EUC-H"),
            "/DescendantFonts": pikepdf.Array([doc.make_indirect(descendant)]),
            "/ToUnicode": doc.make_stream(cmap),
        })
        fonts = pikepdf.Dictionary({str(key): value for key, value in page.resources["/Font"].items()})
        fonts["/F9"] = doc.make_indirect(type0)
        page.obj["/Resources"]["/Font"] = fonts
        page.obj["/Contents"] = doc.make_stream(b"BT /F9 24 Tf 80 700 Td <000A000B> Tj ET")
        doc.save(src)
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "AB")
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="AB", replacement="BA")
    written = next(row for row in list_text_runs(dst, 1)["runs"] if row["text"] == "BA")
    assert written["source_bytes_hex"] == "000b000a"


def test_mixed_width_tounicode_uses_each_code_size() -> None:
    raw = b"""
2 begincodespacerange
<00> <80>
<8140> <FEFE>
endcodespacerange
2 beginbfchar
<41> <0041>
<D6D0> <4E2D>
endbfchar
"""
    assert _tounicode_width(raw) == 0
    codec = parse_to_unicode(raw, code_width=0)
    assert codec is not None
    assert codec.decode(bytes.fromhex("41D6D0")) == "A中"
    assert codec.encode("中A", observed_codes=set()) == bytes.fromhex("D6D041")


def test_standard_encoding_respects_embedded_font_coverage() -> None:
    codec = _standard_codec("gbk", frozenset("中"))
    assert codec.decode("中".encode("gbk")) == "中"
    assert codec.encode("中", observed_codes=set()) == "中".encode("gbk")
    assert codec.encode_if_available("文", observed_codes=set()) is None


def _attach_type0(path: Path, *, encoding, font_bytes: bytes, tounicode: bytes | None,
                  content: bytes, font_file: str = "/FontFile3", subtype: str = "/CIDFontType0") -> None:
    canvas = Canvas(str(path.parent / "base.pdf"))
    canvas.drawString(20, 20, "x")
    canvas.save()
    with pikepdf.Pdf.open(path.parent / "base.pdf") as doc:
        page = doc.pages[0]
        descriptor = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/OptimizedFace"),
            "/Flags": 4, "/FontBBox": pikepdf.Array([0, 0, 1000, 1000]),
            "/ItalicAngle": 0, "/Ascent": 800, "/Descent": -200,
            "/CapHeight": 700, "/StemV": 80,
            font_file: doc.make_stream(font_bytes),
        })
        descendant = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name(subtype),
            "/BaseFont": pikepdf.Name("/OptimizedFace"),
            "/FontDescriptor": doc.make_indirect(descriptor),
            "/DW": 1000,
        })
        encoded = encoding if not isinstance(encoding, (bytes, str)) else (
            doc.make_stream(encoding) if isinstance(encoding, bytes) else pikepdf.Name(encoding)
        )
        type0 = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/OptimizedFace"),
            "/Encoding": encoded,
            "/DescendantFonts": pikepdf.Array([doc.make_indirect(descendant)]),
        })
        if tounicode is not None:
            type0["/ToUnicode"] = doc.make_stream(tounicode)
        fonts = pikepdf.Dictionary({str(key): value for key, value in page.resources["/Font"].items()})
        fonts["/F9"] = doc.make_indirect(type0)
        page.obj["/Resources"]["/Font"] = fonts
        page.obj["/Contents"] = doc.make_stream(content)
        doc.save(path)


def test_embedded_cmap_stream_supplies_character_code(tmp_path: Path) -> None:
    font = _sfnt({b"cmap": _format4_cmap({0x41: 2, 0x42: 1}), b"CFF ": _cff_font([10, 20])})
    encoding = (
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"2 beginbfchar\n<000A> <0014>\n<000B> <000A>\nendbfchar\n"
    )
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    _attach_type0(src, encoding=encoding, font_bytes=font, tounicode=None,
                  content=b"BT /F9 24 Tf 80 700 Td <000A> Tj ET")
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "A")
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="A", replacement="B")
    with pikepdf.Pdf.open(dst) as pdf:
        shown = next(item for item in pikepdf.parse_content_stream(pdf.pages[0]) if str(item.operator) == "Tj")
        assert bytes(shown.operands[0]) == bytes.fromhex("000B")


def test_external_gbk_encoding_uses_viewer_standard(tmp_path: Path) -> None:
    src, dst = tmp_path / "source.pdf", tmp_path / "changed.pdf"
    coded = "中文".encode("gbk")
    _attach_type0(
        src, encoding="/GBK-EUC-H", font_bytes=b"\x01\x00raw-cff", tounicode=None,
        content=b"BT /F9 24 Tf 80 700 Td <" + coded.hex().encode("ascii") + b"> Tj ET",
    )
    run = next(row for row in list_text_runs(src, 1)["runs"] if row["text"] == "中文")
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text="中文", replacement="文字")
    with pikepdf.Pdf.open(dst) as pdf:
        shown = next(item for item in pikepdf.parse_content_stream(pdf.pages[0]) if str(item.operator) == "Tj")
        assert bytes(shown.operands[0]) == "文字".encode("gbk")
    with pytest.raises(FontEmbeddingRequired):
        replace_text_run(src, tmp_path / "rejected.pdf", page=1,
                         operator_index=run["operator_index"],
                         expected_text="中文", replacement="😀")
