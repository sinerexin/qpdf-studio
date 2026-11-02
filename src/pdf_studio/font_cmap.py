"""Character codes taken from the PDF font's own data.

ToUnicode maps a character code to Unicode. An embedded TrueType cmap plus
CIDToGIDMap is the mapping the viewer uses to draw a custom font. A character
with one code from either source is written back with that code. Characters
with no code, or with more than one code, are not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import re

import pikepdf
from reportlab.pdfbase.ttfonts import TTFError, TTFontFile

MAX_CMAP_BYTES = 2_000_000  # 限制：ToUnicode 映射超过 2MB 就不再解析
MAX_MAPPED_CODES = 65_536  # 限制：映射条目超过 65536 就停止解析


class FontEmbeddingRequired(ValueError):
    """原字体不能编码当前输入；自动模式捕获此信号并继续嵌入新字体。"""


@dataclass(frozen=True)
class GlyphCodec:
    kind: str
    width: int
    codes: dict[bytes, str]
    inverse: dict[str, bytes]
    code_lengths: tuple[int, ...] = ()
    standard: str = ""
    limit: frozenset[str] | None = None

    def split(self, raw: bytes) -> list[bytes] | None:
        """Split a content-stream string into the font's character codes."""
        if self.codes:
            if self.width:
                if len(raw) % self.width == 0 and raw:
                    return [raw[index:index + self.width] for index in range(0, len(raw), self.width)]
            else:
                lengths = self.code_lengths or tuple(sorted({len(code) for code in self.codes}, reverse=True))
                parts: list[bytes] = []
                pos = 0
                matched_all = True
                while pos < len(raw):
                    matched = None
                    for length in lengths:
                        piece = raw[pos:pos + length]
                        if len(piece) == length and piece in self.codes:
                            matched = piece
                            break
                    if matched is None:
                        matched_all = False
                        break
                    parts.append(matched)
                    pos += len(matched)
                if matched_all and parts:
                    return parts
        if self.standard:
            return _split_standard(self.standard, raw)
        return None

    def decode(self, raw: bytes) -> str | None:
        if self.codes:
            parts = self.split(raw)
            if parts:
                result = []
                for part in parts:
                    char = self.codes.get(part)
                    if not char or len(char) != 1 or ord(char) < 32 or ord(char) == 127:
                        result = []
                        break
                    result.append(char)
                if result and len(result) == len(parts):
                    return "".join(result)
        if self.standard:
            return _decode_standard(self.standard, raw)
        return None

    def encode_if_available(self, value: str, *, observed_codes: set[bytes]) -> bytes | None:
        """已识别且内部代码唯一的字直接写回。没有内部代码时返回 None。"""
        del observed_codes  # 唯一内部代码不再要求这个代码在本页出现过
        if not value:
            raise ValueError("Replacement text is empty")
        result = bytearray()
        for char in value:
            code = self.inverse.get(char)
            if code is None and self.standard:
                if self.limit is not None and char not in self.limit:
                    return None
                code = _encode_standard_char(self.standard, char)
            if code is None:  # 限制：文件和标准编码都没有唯一字符码时不伪造
                return None
            result.extend(code)
        return bytes(result)

    def encode(self, value: str, *, observed_codes: set[bytes]) -> bytes:
        # 低层原字体编码接口保留明确的换字体信号；自动编辑入口会接住并继续处理。
        encoded = self.encode_if_available(value, observed_codes=observed_codes)
        if encoded is None:
            raise FontEmbeddingRequired('原字体缺字或映射不唯一，需要嵌入可用字体。')
        return encoded


def _single_char(hex_bytes: bytes) -> str | None:
    try:
        text = bytes.fromhex(hex_bytes.decode("ascii")).decode("utf-16-be")
        if len(text) != 1 or ord(text) < 32 or ord(text) == 127:
            return None
        return text
    except (ValueError, UnicodeError):
        return None


def _entry_width(hex_text: bytes, code_width: int) -> int | None:
    if len(hex_text) not in {2, 4}:
        return None
    width = len(hex_text) // 2
    if code_width and width != code_width:
        return None
    return width


def parse_to_unicode(raw: bytes, *, code_width: int) -> GlyphCodec | None:
    """Parse simple Adobe ToUnicode bfchar/bfrange entries with strict bounds."""
    if code_width not in (0, 1, 2) or len(raw) > MAX_CMAP_BYTES:  # 限制：不支持的码宽或超大 CMap 直接放弃解析
        return None  # 限制：解析失败时关闭，不退回普通 WinAnsi 猜测
    # PDF CMaps can contain comments and other operators. Unsupported mapping
    # constructs do not silently fall through to the ordinary WinAnsi path.
    data = re.sub(rb"%[^\r\n]*", b"", raw)
    glyph_map: dict[bytes, str] = {}
    chunks = re.findall(rb"\b\d+\s+beginbfchar\b(.*?)\bendbfchar\b", data, re.S)
    for chunk in chunks:
        for code_hex, unicode_hex in re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk
        ):
            width = _entry_width(code_hex, code_width)
            if width is None:
                continue
            char = _single_char(unicode_hex)
            if char:
                glyph_map[bytes.fromhex(code_hex.decode())] = char
            if len(glyph_map) > MAX_MAPPED_CODES:
                return None
    chunks = re.findall(rb"\b\d+\s+beginbfrange\b(.*?)\bendbfrange\b", data, re.S)
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if not line or line.startswith(b"%"):
                continue
            item = re.match(
                rb"^<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(.*)$", line
            )
            if not item:
                continue
            start_hex, end_hex, rest = item.groups()
            width = _entry_width(start_hex, code_width)
            if width is None or _entry_width(end_hex, code_width) != width:
                continue
            start = int(start_hex, 16)
            end = int(end_hex, 16)
            if end < start or end - start > MAX_MAPPED_CODES:
                return None
            if rest.startswith(b"<"):
                match = re.match(rb"<([0-9A-Fa-f]+)>", rest)
                if not match:
                    continue
                first = bytes.fromhex(match.group(1).decode())
                if len(first) not in (2, 4):
                    continue
                number = int.from_bytes(first, "big")
                for delta in range(end - start + 1):
                    dest_bytes = (number + delta).to_bytes(len(first), "big")
                    char = _single_char(dest_bytes.hex().encode("ascii"))
                    if char:
                        glyph_map[(start + delta).to_bytes(width, "big")] = char
            elif rest.startswith(b"["):
                match = re.search(rb"\[(.*?)\]", rest)
                if not match:
                    continue
                values = re.findall(rb"<([0-9A-Fa-f]+)>", match.group(1))
                if len(values) != end - start + 1:
                    return None
                for delta, item in enumerate(values):
                    char = _single_char(item)
                    if char:
                        glyph_map[(start + delta).to_bytes(width, "big")] = char
            if len(glyph_map) > MAX_MAPPED_CODES:
                return None
    if not glyph_map:
        return None
    inverse = {}
    ambiguous = set()
    for code, character in glyph_map.items():
        previous = inverse.get(character)
        if previous is not None and previous != code:
            ambiguous.add(character)
        else:
            inverse[character] = code
    for character in ambiguous:
        inverse.pop(character, None)
    widths = {len(code) for code in glyph_map}
    stored_width = next(iter(widths)) if len(widths) == 1 else 0
    return GlyphCodec(
        kind="to-unicode-cmap", width=stored_width, codes=glyph_map,
        inverse=inverse, code_lengths=tuple(sorted(widths, reverse=True)),
    )


_FACE_CODES: dict[str, dict[int, int] | None] = {}


def _usable_gid(code: int, gid: int) -> bool:
    return gid > 0 and 32 <= code < 0x110000 and code != 127


def _ttf_unicode_to_gid(payload: bytes) -> dict[int, int]:
    try:
        face = TTFontFile(BytesIO(payload), charInfo=1, validate=0)
    except (TTFError, OSError, ValueError, KeyError, IndexError):
        return {}
    return {
        code: gid
        for code, gid in (face.charToGlyph or {}).items()
        if isinstance(code, int) and isinstance(gid, int) and _usable_gid(code, gid)
    }


def _sfnt_tables(payload: bytes) -> dict[bytes, bytes] | None:
    """Read an sfnt directory. CFF fonts use the same cmap table as TrueType."""
    if len(payload) < 12:
        return None
    if payload[:4] == b"ttcf":
        if len(payload) < 16:
            return None
        base = int.from_bytes(payload[12:16], "big")
    else:
        base = 0
    if payload[base:base + 4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"}:
        return None
    if len(payload) < base + 12:
        return None
    count = int.from_bytes(payload[base + 4:base + 6], "big")
    if count <= 0 or count > 128 or len(payload) < base + 12 + count * 16:
        return None
    tables: dict[bytes, bytes] = {}
    pos = base + 12
    for _ in range(count):
        tag = payload[pos:pos + 4]
        offset = int.from_bytes(payload[pos + 8:pos + 12], "big")
        length = int.from_bytes(payload[pos + 12:pos + 16], "big")
        if length < 0 or offset < 0 or offset + length > len(payload):
            return None
        tables[tag] = payload[offset:offset + length]
        pos += 16
    return tables


def _parse_cmap_subtable(table: bytes, start: int) -> dict[int, int]:
    if start + 6 > len(table):
        return {}
    fmt = int.from_bytes(table[start:start + 2], "big")
    found: dict[int, int] = {}
    if fmt == 4:
        length = int.from_bytes(table[start + 2:start + 4], "big")
        end = start + length
        if end > len(table) or length < 16:
            return {}
        seg_count = int.from_bytes(table[start + 6:start + 8], "big") // 2
        cursor = start + 14
        need = seg_count * 8 + 2
        if seg_count <= 0 or cursor + need > end:
            return {}
        ends = [int.from_bytes(table[cursor + i * 2:cursor + i * 2 + 2], "big")
                for i in range(seg_count)]
        cursor += seg_count * 2 + 2
        starts = [int.from_bytes(table[cursor + i * 2:cursor + i * 2 + 2], "big")
                  for i in range(seg_count)]
        cursor += seg_count * 2
        deltas = [int.from_bytes(table[cursor + i * 2:cursor + i * 2 + 2], "big", signed=True)
                  for i in range(seg_count)]
        cursor += seg_count * 2
        offsets = [int.from_bytes(table[cursor + i * 2:cursor + i * 2 + 2], "big")
                   for i in range(seg_count)]
        offset_at = cursor
        for index, code in enumerate(starts):
            if ends[index] < code or ends[index] - code > 65535:
                return {}
            while code <= ends[index]:
                if offsets[index] == 0:
                    gid = (code + deltas[index]) & 0xFFFF
                else:
                    pos = (offset_at + index * 2 + offsets[index]
                           + (code - starts[index]) * 2)
                    if pos + 2 > end:
                        gid = 0
                    else:
                        gid = int.from_bytes(table[pos:pos + 2], "big")
                        if gid:
                            gid = (gid + deltas[index]) & 0xFFFF
                if _usable_gid(code, gid):
                    found[code] = gid
                if code == 0xFFFF:
                    break
                code += 1
        return found
    if fmt == 12 and start + 16 <= len(table):
        length = int.from_bytes(table[start + 4:start + 8], "big")
        groups = int.from_bytes(table[start + 12:start + 16], "big")
        cursor = start + 16
        if groups > 100000 or cursor + groups * 12 > start + length or start + length > len(table):
            return {}
        for _ in range(groups):
            first = int.from_bytes(table[cursor:cursor + 4], "big")
            last = int.from_bytes(table[cursor + 4:cursor + 8], "big")
            glyph = int.from_bytes(table[cursor + 8:cursor + 12], "big")
            cursor += 12
            if last < first or last - first > 65535:
                return {}
            for code in range(first, last + 1):
                gid = glyph + (code - first)
                if _usable_gid(code, gid):
                    found[code] = gid
        return found
    return {}


def _sfnt_unicode_to_gid(payload: bytes) -> dict[int, int]:
    tables = _sfnt_tables(payload)
    table = None if tables is None else tables.get(b"cmap")
    if not table or len(table) < 4:
        return {}
    count = int.from_bytes(table[2:4], "big")
    if count <= 0 or count > 64 or len(table) < 4 + count * 8:
        return {}
    chosen: dict[int, int] = {}
    for index in range(count):
        pos = 4 + index * 8
        platform, encoding, offset = (
            int.from_bytes(table[pos:pos + 2], "big"),
            int.from_bytes(table[pos + 2:pos + 4], "big"),
            int.from_bytes(table[pos + 4:pos + 8], "big"),
        )
        if platform == 0 or (platform == 3 and encoding in {1, 10}):
            found = _parse_cmap_subtable(table, offset)
            for code, gid in found.items():
                previous = chosen.get(code)
                if previous is not None and previous != gid:
                    chosen.pop(code, None)
                elif previous is None:
                    chosen[code] = gid
    return chosen


def _unicode_to_gid(payload: bytes) -> dict[int, int] | None:
    """Read the embedded font Unicode cmap. CFF fonts keep this table too."""
    key = hashlib.sha256(payload).hexdigest()
    if key in _FACE_CODES:
        return _FACE_CODES[key]
    mapping = _ttf_unicode_to_gid(payload) or _sfnt_unicode_to_gid(payload)
    _FACE_CODES[key] = mapping or None
    return _FACE_CODES[key]


def _read_index(data: bytes, pos: int) -> tuple[list[bytes], int] | None:
    if pos + 2 > len(data):
        return None
    count = int.from_bytes(data[pos:pos + 2], "big")
    pos += 2
    if count == 0:
        return [], pos
    if count > 65535 or pos >= len(data):
        return None
    off_size = data[pos]
    pos += 1
    if off_size not in {1, 2, 3, 4} or pos + (count + 1) * off_size > len(data):
        return None
    offsets = []
    for _ in range(count + 1):
        offsets.append(int.from_bytes(data[pos:pos + off_size], "big"))
        pos += off_size
    if offsets[0] != 1 or any(b < a for a, b in zip(offsets, offsets[1:])):
        return None
    end = pos + offsets[-1] - 1
    if end > len(data):
        return None
    blobs = [data[pos + offsets[i] - 1:pos + offsets[i + 1] - 1] for i in range(count)]
    return blobs, end


def _dict_operand(data: bytes) -> list[tuple[list[int], tuple[int, ...]]]:
    items: list[tuple[list[int], tuple[int, ...]]] = []
    stack: list[int] = []
    pos = 0
    while pos < len(data):
        byte = data[pos]
        if byte == 28 and pos + 3 <= len(data):
            stack.append(int.from_bytes(data[pos + 1:pos + 3], "big", signed=True))
            pos += 3
        elif byte == 29 and pos + 5 <= len(data):
            stack.append(int.from_bytes(data[pos + 1:pos + 5], "big", signed=True))
            pos += 5
        elif byte == 30:
            pos += 1
            while pos < len(data):
                nibbles = data[pos]
                pos += 1
                if (nibbles & 0x0F) == 0x0F or (nibbles >> 4) == 0x0F:
                    break
        elif 32 <= byte <= 246:
            stack.append(byte - 139)
            pos += 1
        elif 247 <= byte <= 250 and pos + 2 <= len(data):
            stack.append((byte - 247) * 256 + data[pos + 1] + 108)
            pos += 2
        elif 251 <= byte <= 254 and pos + 2 <= len(data):
            stack.append(-((byte - 251) * 256) - data[pos + 1] - 108)
            pos += 2
        elif byte == 12 and pos + 2 <= len(data):
            items.append((stack, (12, data[pos + 1])))
            stack = []
            pos += 2
        elif byte < 28:
            # 0–21 是 CFF 操作符，28–30 已在上面按数字读过。
            items.append((stack, (byte,)))
            stack = []
            pos += 1
        else:
            return []
    return items


def _cff_gid_to_cid(payload: bytes) -> list[int] | None:
    """CIDFontType0 charset: glyph index to the CID the viewer draws."""
    if len(payload) < 4 or payload[0] != 1:
        return None
    pos = payload[2]
    name = _read_index(payload, pos)
    if name is None:
        return None
    top = _read_index(payload, name[1])
    if top is None or not top[0]:
        return None
    operators = _dict_operand(top[0][0])
    charset = next((nums[-1] for nums, op in operators if op == (15,) and nums), None)
    charstrings = next((nums[-1] for nums, op in operators if op == (17,) and nums), None)
    if charset is None or charstrings is None or charstrings < 0 or charset < 0:
        return None
    strings = _read_index(payload, charstrings)
    if strings is None or not strings[0]:
        return None
    count = len(strings[0])
    if charset >= len(payload) or count > 65536:
        return None
    fmt = payload[charset]
    cids = [0]
    cursor = charset + 1
    try:
        if fmt == 0:
            for _ in range(count - 1):
                cids.append(int.from_bytes(payload[cursor:cursor + 2], "big"))
                cursor += 2
        elif fmt in {1, 2}:
            width = 1 if fmt == 1 else 2
            while len(cids) < count:
                first = int.from_bytes(payload[cursor:cursor + 2], "big")
                left = int.from_bytes(payload[cursor + 2:cursor + 2 + width], "big")
                cursor += 2 + width
                if left > 65535 or len(cids) + left + 1 > count:
                    return None
                cids.extend(range(first, first + left + 1))
        else:
            return None
    except (IndexError, ValueError):
        return None
    if len(cids) != count or any(cid > 65535 for cid in cids):
        return None
    return cids


def _cff_payload(payload: bytes) -> bytes | None:
    if len(payload) >= 4 and payload[0] == 1 and payload[1] == 0:
        return payload
    tables = _sfnt_tables(payload)
    if tables is None:
        return None
    return tables.get(b"CFF ")


def _gid_to_cid(cid_to_gid) -> dict[int, int] | None:
    """Reverse the viewer's CIDToGIDMap. None means the PDF uses Identity."""
    if cid_to_gid is None or str(cid_to_gid) == "/Identity":
        return None
    if not isinstance(cid_to_gid, pikepdf.Stream):
        raise ValueError("CIDToGIDMap is neither Identity nor a stream")
    data = cid_to_gid.read_bytes()
    if len(data) % 2:
        raise ValueError("CIDToGIDMap length is not a whole number of glyph ids")
    found: dict[int, int] = {}
    duplicated: set[int] = set()
    for cid in range(len(data) // 2):
        gid = int.from_bytes(data[cid * 2:cid * 2 + 2], "big")
        if gid == 0:
            continue
        previous = found.get(gid)
        if previous is not None and previous != cid:
            duplicated.add(gid)
        else:
            found[gid] = cid
    for gid in duplicated:
        found.pop(gid, None)
    return found


def _tounicode_width(raw: bytes) -> int | None:
    """Use the code width written in the CMap. Do not assume Identity-H."""
    if len(raw) > MAX_CMAP_BYTES:
        return None
    data = re.sub(rb"%[^\r\n]*", b"", raw)
    widths: set[int] = set()
    chunks = re.findall(rb"begincodespacerange\b(.*?)\bendcodespacerange\b", data, re.S)
    for chunk in chunks:
        for start, end in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk):
            if len(start) != len(end) or len(start) not in {2, 4}:
                return None
            widths.add(len(start) // 2)
    if widths and widths <= {1, 2}:
        return widths.pop() if len(widths) == 1 else 0
    if widths:
        return None
    for chunk in re.findall(rb"beginbfchar\b(.*?)\bendbfchar\b", data, re.S):
        for code_hex, _unicode_hex in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk):
            if len(code_hex) not in {2, 4}:
                return None
            widths.add(len(code_hex) // 2)
    if widths and widths <= {1, 2}:
        return widths.pop() if len(widths) == 1 else 0
    return None


def _font_program(descendant: pikepdf.Dictionary) -> bytes | None:
    descriptor = descendant.get("/FontDescriptor")
    if descriptor is None:
        return None
    for key in ("/FontFile2", "/FontFile3"):
        program = descriptor.get(key)
        if isinstance(program, pikepdf.Stream):
            return program.read_bytes()
    return None


def _identity_display_codes(font: pikepdf.Dictionary) -> dict[bytes, str]:
    """Unicode to the 2-byte CID that Identity-H/V uses to draw the embedded glyph."""
    if str(font.get("/Subtype", "")) != "/Type0":
        return {}
    if str(font.get("/Encoding", "")) not in {"/Identity-H", "/Identity-V"}:
        return {}
    descendants = font.get("/DescendantFonts")
    if not descendants or len(descendants) != 1:
        return {}
    descendant = descendants[0]
    subtype = str(descendant.get("/Subtype", ""))
    if subtype not in {"/CIDFontType0", "/CIDFontType2"}:
        return {}
    payload = _font_program(descendant)
    if not payload:
        return {}
    unicode_to_gid = _unicode_to_gid(payload)
    if not unicode_to_gid:
        return {}
    gid_to_cid = _gid_to_cid(descendant.get("/CIDToGIDMap")) if subtype == "/CIDFontType2" else {}
    cff_cids = _cff_gid_to_cid(_cff_payload(payload) or b"") if subtype == "/CIDFontType0" else None
    codes: dict[bytes, str] = {}
    claimed: set[str] = set()
    for codepoint, gid in unicode_to_gid.items():
        if subtype == "/CIDFontType2":
            cid = gid if gid_to_cid is None else gid_to_cid.get(gid)
        elif cff_cids is not None and gid < len(cff_cids):
            cid = cff_cids[gid] or None
        else:
            cid = None
        if cid is None or not 0 < cid < 65536:
            continue
        char = chr(codepoint)
        if char in claimed:
            for key, value in list(codes.items()):
                if value == char:
                    codes.pop(key, None)
            continue
        raw = cid.to_bytes(2, "big")
        if raw in codes and codes[raw] != char:
            continue
        codes[raw] = char
        claimed.add(char)
    return codes


_STANDARD_ENCODINGS = {
    "/GBK-EUC-H": "gbk", "/GBK-EUC-V": "gbk",
    "/GBKp-EUC-H": "gbk", "/GBKp-EUC-V": "gbk",
    "/GBpc-EUC-H": "gb2312", "/GBpc-EUC-V": "gb2312",
    "/ETen-B5-H": "big5", "/ETen-B5-V": "big5",
    "/90ms-RKSJ-H": "cp932", "/90ms-RKSJ-V": "cp932",
    "/KSCms-UHC-H": "cp949", "/KSCms-UHC-V": "cp949",
    "/UniGB-UCS2-H": "ucs2", "/UniGB-UCS2-V": "ucs2",
    "/UniGB-UTF16-H": "utf16", "/UniGB-UTF16-V": "utf16",
    "/UniCNS-UCS2-H": "ucs2", "/UniCNS-UCS2-V": "ucs2",
    "/UniCNS-UTF16-H": "utf16", "/UniCNS-UTF16-V": "utf16",
    "/UniJIS-UCS2-H": "ucs2", "/UniJIS-UCS2-V": "ucs2",
    "/UniJIS-UTF16-H": "utf16", "/UniJIS-UTF16-V": "utf16",
    "/UniKS-UCS2-H": "ucs2", "/UniKS-UCS2-V": "ucs2",
    "/UniKS-UTF16-H": "utf16", "/UniKS-UTF16-V": "utf16",
}


def _encode_standard_char(kind: str, char: str) -> bytes | None:
    if ord(char) < 32 or ord(char) == 127:
        return None
    if kind == "ucs2":
        if ord(char) > 0xFFFF or 0xD800 <= ord(char) <= 0xDFFF:
            return None
        return ord(char).to_bytes(2, "big")
    if kind == "utf16":
        return char.encode("utf-16-be")
    try:
        return char.encode(kind)
    except UnicodeEncodeError:
        return None


def _decode_standard(kind: str, raw: bytes) -> str | None:
    if not raw:
        return None
    try:
        if kind in {"ucs2", "utf16"}:
            if len(raw) % 2:
                return None
            text = raw.decode("utf-16-be")
            if kind == "ucs2" and any(0xD800 <= ord(char) <= 0xDFFF for char in text):
                return None
        else:
            text = raw.decode(kind)
    except UnicodeDecodeError:
        return None
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return None
    return text


def _split_standard(kind: str, raw: bytes) -> list[bytes] | None:
    if not raw:
        return None
    if kind in {"ucs2", "utf16"}:
        if len(raw) % 2:
            return None
        return [raw[index:index + 2] for index in range(0, len(raw), 2)]
    parts: list[bytes] = []
    pos = 0
    while pos < len(raw):
        length = 2 if raw[pos] >= 0x81 else 1
        if pos + length > len(raw):
            return None
        parts.append(raw[pos:pos + length])
        pos += length
    return parts


def _standard_codec(kind: str, limit: frozenset[str] | None) -> GlyphCodec:
    return GlyphCodec(
        kind=f"standard-{kind}", width=2 if kind in {"ucs2", "utf16"} else 0,
        codes={}, inverse={}, standard=kind, limit=limit,
        code_lengths=(2, 1) if kind not in {"ucs2", "utf16"} else (2,),
    )


def parse_code_to_cid(raw: bytes) -> dict[bytes, int] | None:
    """Read an embedded CMap from character code to CID."""
    if len(raw) > MAX_CMAP_BYTES:
        return None
    data = re.sub(rb"%[^\r\n]*", b"", raw)
    found: dict[bytes, int] = {}

    def remember(code: bytes, cid: int) -> bool:
        if not code or cid <= 0 or cid > 65535 or len(found) > MAX_MAPPED_CODES:
            return False
        previous = found.get(code)
        if previous is not None and previous != cid:
            return False
        found[code] = cid
        return True

    for chunk in re.findall(rb"\b\d+\s+beginbfchar\b(.*?)\bendbfchar\b", data, re.S):
        for code_hex, cid_hex in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", chunk):
            width = _entry_width(code_hex, 0)
            if width is None or len(cid_hex) not in {2, 4}:
                continue
            if not remember(bytes.fromhex(code_hex.decode()), int(cid_hex, 16)):
                return None
    for chunk in re.findall(rb"\b\d+\s+beginbfrange\b(.*?)\bendbfrange\b", data, re.S):
        for line in chunk.splitlines():
            line = line.strip()
            item = re.match(rb"^<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(.*)$", line)
            if not item:
                continue
            start_hex, end_hex, rest = item.groups()
            width = _entry_width(start_hex, 0)
            if width is None or _entry_width(end_hex, 0) != width:
                continue
            start, end = int(start_hex, 16), int(end_hex, 16)
            if end < start or end - start > MAX_MAPPED_CODES:
                return None
            if rest.startswith(b"<"):
                match = re.match(rb"<([0-9A-Fa-f]+)>", rest)
                if not match or len(match.group(1)) not in {2, 4}:
                    continue
                first = int(match.group(1), 16)
                for delta in range(end - start + 1):
                    if not remember((start + delta).to_bytes(width, "big"), first + delta):
                        return None
            elif rest.startswith(b"["):
                values = re.findall(rb"<([0-9A-Fa-f]+)>", rest)
                if len(values) != end - start + 1:
                    return None
                for delta, cid_hex in enumerate(values):
                    if len(cid_hex) not in {2, 4}:
                        return None
                    if not remember((start + delta).to_bytes(width, "big"), int(cid_hex, 16)):
                        return None
    return found or None


def _cid_for_glyph(descendant: pikepdf.Dictionary, payload: bytes, gid: int) -> int | None:
    subtype = str(descendant.get("/Subtype", ""))
    if subtype == "/CIDFontType2":
        mapping = _gid_to_cid(descendant.get("/CIDToGIDMap"))
        cid = gid if mapping is None else mapping.get(gid)
    elif subtype == "/CIDFontType0":
        cids = _cff_gid_to_cid(_cff_payload(payload) or b"")
        cid = cids[gid] if cids is not None and gid < len(cids) else None
    else:
        cid = None
    if cid is None or not 0 < cid < 65536:
        return None
    return cid


def _embedded_encoding_codes(font: pikepdf.Dictionary) -> dict[bytes, str]:
    """Use a CMap stream embedded in the PDF, plus the font's own glyph ids."""
    if str(font.get("/Subtype", "")) != "/Type0":
        return {}
    encoding = font.get("/Encoding")
    if not isinstance(encoding, pikepdf.Stream):
        return {}
    code_to_cid = parse_code_to_cid(encoding.read_bytes())
    if not code_to_cid:
        return {}
    descendants = font.get("/DescendantFonts")
    if not descendants or len(descendants) != 1:
        return {}
    descendant = descendants[0]
    payload = _font_program(descendant)
    if not payload:
        return {}
    unicode_to_gid = _unicode_to_gid(payload)
    if not unicode_to_gid:
        return {}
    cid_to_char: dict[int, str] = {}
    ambiguous: set[int] = set()
    for codepoint, gid in unicode_to_gid.items():
        cid = _cid_for_glyph(descendant, payload, gid)
        if cid is None:
            continue
        char = chr(codepoint)
        previous = cid_to_char.get(cid)
        if previous is not None and previous != char:
            ambiguous.add(cid)
        else:
            cid_to_char[cid] = char
    for cid in ambiguous:
        cid_to_char.pop(cid, None)
    codes: dict[bytes, str] = {}
    claimed: set[str] = set()
    for raw, cid in code_to_cid.items():
        char = cid_to_char.get(cid)
        if not char:
            continue
        if char in claimed:
            for key, value in list(codes.items()):
                if value == char:
                    codes.pop(key, None)
            continue
        codes[raw] = char
        claimed.add(char)
    return codes


def _embedded_unicode(font: pikepdf.Dictionary) -> frozenset[str] | None:
    """Characters the embedded font file itself can name. None when it has no cmap."""
    try:
        if str(font.get("/Subtype", "")) == "/Type0":
            descendants = font.get("/DescendantFonts")
            if not descendants or len(descendants) != 1:
                return None
            payload = _font_program(descendants[0])
        else:
            descriptor = font.get("/FontDescriptor")
            payload = None if descriptor is None else _font_program(font)
            if payload is None and descriptor is not None:
                for key in ("/FontFile2", "/FontFile3"):
                    program = descriptor.get(key)
                    if isinstance(program, pikepdf.Stream):
                        payload = program.read_bytes()
                        break
        if not payload:
            return None
        mapping = _unicode_to_gid(payload)
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError, OSError):
        return None
    if not mapping:
        return None
    return frozenset(chr(code) for code in mapping)


def _combine_codes(primary: GlyphCodec | None, display: dict[bytes, str]) -> GlyphCodec | None:
    """Keep already named characters, then add display codes for the rest."""
    if primary is not None and not display:
        return primary
    codes = dict(primary.codes) if primary is not None else {}
    inverse = dict(primary.inverse) if primary is not None else {}
    for raw, char in display.items():
        if char in inverse or raw in codes:
            continue
        codes[raw] = char
        inverse[char] = raw
    if not codes:
        return primary
    widths = {len(raw) for raw in codes}
    width = next(iter(widths)) if len(widths) == 1 else 0
    return GlyphCodec(
        kind=primary.kind if primary is not None else "embedded-display-code",
        width=width, codes=codes, inverse=inverse,
        code_lengths=tuple(sorted(widths, reverse=True)),
        standard=primary.standard if primary is not None else "",
        limit=primary.limit if primary is not None else None,
    )


def codec_for_font(page: pikepdf.Page, font_key: str) -> GlyphCodec | None:
    try:
        font = page.resources["/Font"][font_key]
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError):
        return None
    parsed = None
    try:
        cmap = font.get("/ToUnicode")
        # ToUnicode 里写着字符码。能显示的字，这段映射就在字体字典里，不看字体种类。
        if isinstance(cmap, pikepdf.Stream):
            raw = cmap.read_bytes()
            width = _tounicode_width(raw)
            if width is not None:
                parsed = parse_to_unicode(raw, code_width=width)
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError):
        parsed = None
    try:
        display = _identity_display_codes(font)
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError, OSError):
        display = {}
    try:
        streamed = _embedded_encoding_codes(font)
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError, OSError):
        streamed = {}
    combined = _combine_codes(_combine_codes(parsed, display), streamed)
    standard = _STANDARD_ENCODINGS.get(str(font.get("/Encoding", "")), "")
    if not standard:
        return combined
    try:
        limit = _embedded_unicode(font)
    except (KeyError, TypeError, ValueError, AttributeError, pikepdf.PdfError, OSError):
        limit = None
    if combined is None:
        return _standard_codec(standard, limit)
    return GlyphCodec(
        kind=combined.kind, width=combined.width, codes=combined.codes,
        inverse=combined.inverse, code_lengths=combined.code_lengths,
        standard=standard, limit=limit,
    )
