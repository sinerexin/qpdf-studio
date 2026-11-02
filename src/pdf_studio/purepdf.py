"""Independent, standard-library-only PDF structure parser (early development).

This module is purposefully separate from pikepdf/libqpdf. Its current scope is
classic and compressed xref tables, primitive PDF object syntax, incremental
xref /Prev chains and unencrypted streams. Unsupported inputs fail clearly
instead of silently claiming full PDF support or document authenticity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PdfSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Ref:
    number: int
    generation: int

    def __str__(self) -> str:
        return f"{self.number} {self.generation} R"


@dataclass(frozen=True)
class PdfName:
    value: str

    def __str__(self) -> str:
        return "/" + self.value


@dataclass(frozen=True)
class PdfStream:
    attributes: dict[str, Any]
    raw: bytes


WHITESPACE = b"\x00\x09\x0a\x0c\x0d\x20"
DELIMITERS = b"()<>[]{}/%"
MAX_BYTES = 150_000_000
MAX_STREAM_DECODED = 30_000_000


class Scanner:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.i = offset

    def skip(self) -> None:
        length = len(self.data)
        while self.i < length:
            c = self.data[self.i]
            if c in WHITESPACE:
                self.i += 1
            elif c == 37:  # PDF % comment
                end = self.data.find(b"\n", self.i)
                self.i = length if end < 0 else end + 1
            else:
                break

    def take(self, token: bytes) -> bool:
        self.skip()
        if self.data.startswith(token, self.i):
            end = self.i + len(token)
            if end == len(self.data) or self.data[end] in WHITESPACE + DELIMITERS:
                self.i = end
                return True
        return False

    def word(self) -> bytes:
        self.skip()
        start = self.i
        while self.i < len(self.data) and self.data[self.i] not in WHITESPACE + DELIMITERS:
            self.i += 1
        if self.i == start:
            raise PdfSyntaxError(f"Expected word at byte {self.i}")
        return self.data[start:self.i]

    def name(self) -> PdfName:
        if self.data[self.i] != 47:
            raise PdfSyntaxError("Expected /name")
        self.i += 1
        start = self.i
        while self.i < len(self.data) and self.data[self.i] not in WHITESPACE + DELIMITERS:
            self.i += 1
        encoded = self.data[start:self.i]
        encoded = re.sub(rb"#([0-9A-Fa-f]{2})",
                         lambda m: bytes([int(m.group(1), 16)]), encoded)
        return PdfName(encoded.decode("latin1"))

    def literal_string(self) -> bytes:
        self.i += 1
        output = bytearray()
        depth = 1
        while self.i < len(self.data):
            c = self.data[self.i]
            self.i += 1
            if c == 92:
                if self.i >= len(self.data):
                    break
                esc = self.data[self.i]
                self.i += 1
                replacements = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12}
                if esc in replacements:
                    output.append(replacements[esc])
                elif esc == 13:
                    if self.data[self.i:self.i + 1] == b"\n":
                        self.i += 1
                elif esc == 10:
                    continue
                elif 48 <= esc <= 55:
                    digits = bytearray([esc])
                    for _ in range(2):
                        if self.i < len(self.data) and 48 <= self.data[self.i] <= 55:
                            digits.append(self.data[self.i])
                            self.i += 1
                    output.append(int(digits, 8) % 256)
                else:
                    output.append(esc)
            elif c == 40:
                depth += 1
                output.append(c)
            elif c == 41:
                depth -= 1
                if depth == 0:
                    return bytes(output)
                output.append(c)
            else:
                output.append(c)
        raise PdfSyntaxError("Unterminated literal PDF string")

    def parse(self, depth: int = 0) -> Any:
        if depth > 60:
            raise PdfSyntaxError("PDF object nesting too deep")
        self.skip()
        if self.i >= len(self.data):
            raise PdfSyntaxError("Unexpected end of PDF")
        if self.data.startswith(b"<<", self.i):
            self.i += 2
            result = {}
            while True:
                self.skip()
                if self.data.startswith(b">>", self.i):
                    self.i += 2
                    return result
                if self.i >= len(self.data):
                    raise PdfSyntaxError("Unterminated PDF dictionary")
                key = self.name()
                result[str(key)] = self.parse(depth + 1)
                if len(result) > 100_000:
                    raise PdfSyntaxError("Excessively large PDF dictionary")
        c = self.data[self.i]
        if c == 91:  # array
            self.i += 1
            values = []
            while True:
                self.skip()
                if self.i >= len(self.data):
                    raise PdfSyntaxError("Unterminated PDF array")
                if self.data[self.i] == 93:
                    self.i += 1
                    return values
                values.append(self.parse(depth + 1))
                if len(values) > 100_000:
                    raise PdfSyntaxError("Excessively large PDF array")
        if c == 47:
            return self.name()
        if c == 40:
            return self.literal_string()
        if c == 60:  # hexadecimal string, not dictionary
            end = self.data.find(b">", self.i + 1)
            if end < 0:
                raise PdfSyntaxError("Unterminated hex string")
            literal = re.sub(rb"\s", b"", self.data[self.i + 1:end])
            self.i = end + 1
            try:
                return bytes.fromhex((literal + b"0" * (len(literal) % 2)).decode("ascii"))
            except ValueError as exc:
                raise PdfSyntaxError("Malformed PDF hex string") from exc
        token = self.word()
        if token == b"true":
            return True
        if token == b"false":
            return False
        if token == b"null":
            return None
        try:
            number = int(token)
        except ValueError:
            try:
                return float(token)
            except ValueError as exc:
                raise PdfSyntaxError(f"Unsupported PDF token {token[:32]!r}") from exc
        before_ref = self.i
        try:
            other = self.word()
            if other.isdigit() and self.take(b"R"):
                return Ref(number, int(other))
        except PdfSyntaxError:
            pass
        self.i = before_ref
        return number


class PdfDocument:
    def __init__(self, data: bytes):
        if len(data) > MAX_BYTES:
            raise PdfSyntaxError("PDF exceeds standalone parser byte limit")  # 限制：PDF 超过独立解析器的字节上限
        if not re.match(rb"%PDF-\d\.\d", data[:16]):
            raise PdfSyntaxError("Missing PDF header")
        self.data = data
        self.version = data[5:8].decode("ascii", "replace")
        self.xref: dict[Ref, int] = {}
        self.compressed: dict[Ref, tuple[int, int]] = {}
        self.revisions: list[dict[str, Any]] = []
        self._cache: dict[Ref, Any] = {}
        self._loading: set[Ref] = set()
        self._parse_xrefs()

    @classmethod
    def open(cls, path: Path) -> PdfDocument:
        path = Path(path)
        if path.stat().st_size > MAX_BYTES:
            raise PdfSyntaxError("File is too large for this parser")
        return cls(path.read_bytes())

    def _parse_xrefs(self) -> None:
        tail = self.data[-8192:]
        matches = list(re.finditer(rb"startxref\s+(\d+)\s*%%EOF", tail))
        if not matches:
            raise PdfSyntaxError("No valid classic startxref/EOF footer")
        current = int(matches[-1].group(1))
        visited: set[int] = set()
        seen_numbers: set[int] = set()
        for _ in range(100):
            if current in visited or current >= len(self.data) or current < 0:
                raise PdfSyntaxError("Cyclic or invalid xref offset")
            visited.add(current)
            scan = Scanner(self.data, current)
            if scan.take(b"xref"):
                while True:
                    scan.skip()
                    if scan.take(b"trailer"):
                        break
                    try:
                        start = int(scan.word())
                        count = int(scan.word())
                    except ValueError as exc:
                        raise PdfSyntaxError("Malformed xref subsection") from exc
                    if start < 0 or count < 0 or count > 1_000_000:
                        raise PdfSyntaxError("Unsafe xref subsection")
                    for number in range(start, start + count):
                        offset = scan.word()
                        generation = scan.word()
                        state = scan.word()
                        if number in seen_numbers:
                            continue
                        seen_numbers.add(number)
                        if state == b"n":
                            try:
                                self.xref[Ref(number, int(generation))] = int(offset)
                            except ValueError as exc:
                                raise PdfSyntaxError("Invalid xref entry") from exc
                        elif state != b"f":
                            raise PdfSyntaxError("Invalid xref entry status")
                trailer = scan.parse()
                if not isinstance(trailer, dict):
                    raise PdfSyntaxError("Expected xref trailer dictionary")
            else:
                _, obj = self._read_at(current)
                if not isinstance(obj, PdfStream) or str(obj.attributes.get("/Type")) != "/XRef":
                    raise PdfSyntaxError("Unsupported cross-reference format")
                trailer = obj.attributes
                widths = trailer.get("/W")
                index = trailer.get("/Index", [0, trailer.get("/Size")])
                if not isinstance(widths, list) or len(widths) != 3 or any(
                    not isinstance(w, int) or w < 0 or w > 8 for w in widths
                ):
                    raise PdfSyntaxError("Invalid XRef stream field widths")
                if not isinstance(index, list) or len(index) % 2:
                    raise PdfSyntaxError("Malformed /Index in XRef stream")
                row_length = sum(widths)
                if not row_length:
                    raise PdfSyntaxError("Empty XRef stream field widths")
                payload = self.decode(obj)
                cursor = 0
                for start, count in zip(index[::2], index[1::2]):
                    if not isinstance(start, int) or not isinstance(count, int) or start < 0 or count < 0:
                        raise PdfSyntaxError("Invalid XRef stream index")
                    if cursor + count * row_length > len(payload):
                        raise PdfSyntaxError("Truncated XRef stream data")
                    for number in range(start, start + count):
                        fields = []
                        for width in widths:
                            fields.append(int.from_bytes(payload[cursor:cursor + width], "big"))
                            cursor += width
                        if number in seen_numbers:
                            continue
                        seen_numbers.add(number)
                        kind = fields[0] if widths[0] else 1
                        if kind == 1:
                            self.xref[Ref(number, fields[2])] = fields[1]
                        elif kind == 2:
                            self.compressed[Ref(number, 0)] = (fields[1], fields[2])
                        elif kind != 0:
                            raise PdfSyntaxError("Invalid XRef stream entry kind")
            self.revisions.append({"xref_offset": current, "trailer": trailer})
            previous = trailer.get("/Prev")
            if previous is None:
                return
            if not isinstance(previous, int):
                raise PdfSyntaxError("Invalid /Prev revision pointer")
            current = previous
        raise PdfSyntaxError("Exceeded maximum incremental revision count")

    def _read_at(self, offset: int) -> tuple[Ref, Any]:
        scan = Scanner(self.data, offset)
        try:
            number, gen = int(scan.word()), int(scan.word())
        except ValueError as exc:
            raise PdfSyntaxError("Invalid object header") from exc
        if not scan.take(b"obj"):
            raise PdfSyntaxError("Expected indirect object at xref location")
        obj = scan.parse()
        if isinstance(obj, dict) and scan.take(b"stream"):
            # Consume the mandatory EOL after the stream keyword.
            if self.data.startswith(b"\r\n", scan.i):
                scan.i += 2
            elif scan.i < len(self.data) and self.data[scan.i] in (10, 13):
                scan.i += 1
            else:
                raise PdfSyntaxError("Missing line break after stream keyword")
            length = obj.get("/Length")
            if isinstance(length, Ref):
                length = self.get(length)
            if not isinstance(length, int) or length < 0 or length > MAX_BYTES:
                raise PdfSyntaxError("Unresolved or invalid PDF stream length")
            end = scan.i + length
            if end > len(self.data):
                raise PdfSyntaxError("Stream extends past PDF")
            raw = self.data[scan.i:end]
            scan.i = end
            if not scan.take(b"endstream"):
                raise PdfSyntaxError("PDF stream length does not match endstream")
            obj = PdfStream(obj, raw)
        if not scan.take(b"endobj"):
            raise PdfSyntaxError(f"Missing endobj for {number} {gen}")
        return Ref(number, gen), obj

    def get(self, ref: Ref) -> Any:
        if ref in self._cache:
            return self._cache[ref]
        if ref in self._loading:
            raise PdfSyntaxError(f"Cyclic indirect object reference {ref}")
        self._loading.add(ref)
        try:
            if ref in self.compressed:
                stream_number, index = self.compressed[ref]
                container = self.get(Ref(stream_number, 0))
                if not isinstance(container, PdfStream) or str(container.attributes.get("/Type")) != "/ObjStm":
                    raise PdfSyntaxError("Compressed object container is not an /ObjStm")
                n, first = container.attributes.get("/N"), container.attributes.get("/First")
                if not isinstance(n, int) or not isinstance(first, int) or not 0 <= index < n <= 100000:
                    raise PdfSyntaxError("Invalid compressed object index")
                raw = self.decode(container)
                head = Scanner(raw)
                pairs = []
                for _ in range(n):
                    try:
                        pairs.append((int(head.word()), int(head.word())))
                    except ValueError as exc:
                        raise PdfSyntaxError("Invalid /ObjStm table") from exc
                if pairs[index][0] != ref.number:
                    raise PdfSyntaxError("Compressed object number mismatch")
                relative = first + pairs[index][1]
                if not 0 <= relative < len(raw):
                    raise PdfSyntaxError("Compressed object offset out of bounds")
                value = Scanner(raw, relative).parse()
            else:
                offset = self.xref.get(ref)
                if offset is None:
                    raise KeyError(str(ref))
                actual, value = self._read_at(offset)
                if actual != ref:
                    raise PdfSyntaxError(f"Object header mismatch for {ref}")
            self._cache[ref] = value
            return value
        finally:
            self._loading.discard(ref)

    def decode(self, stream: PdfStream) -> bytes:
        filters = stream.attributes.get("/Filter")
        names = filters if isinstance(filters, list) else [filters] if filters else []
        parameters = stream.attributes.get("/DecodeParms")
        parameters = parameters if isinstance(parameters, list) else [parameters] * len(names)
        raw = stream.raw
        for filter_item, settings in zip(names, parameters):
            if str(filter_item) not in ("/FlateDecode", "/Fl"):
                raise PdfSyntaxError(f"Unsupported stream compression filter {filter_item}")
            try:
                decompressor = zlib.decompressobj()
                raw = decompressor.decompress(raw, MAX_STREAM_DECODED + 1)
                if len(raw) > MAX_STREAM_DECODED or not decompressor.eof:
                    raise PdfSyntaxError("Decoded stream exceeds size limit or is incomplete")  # 限制：解码后的流超过大小上限或不完整
            except zlib.error as exc:
                raise PdfSyntaxError("Invalid Flate-compressed stream") from exc
            if isinstance(settings, dict):
                predictor = settings.get("/Predictor", 1)
                if predictor in range(10, 16):
                    columns = settings.get("/Columns", 1)
                    colors = settings.get("/Colors", 1)
                    bits = settings.get("/BitsPerComponent", 8)
                    if any(not isinstance(v, int) or v < 1 for v in (columns, colors, bits)):
                        raise PdfSyntaxError("Invalid PNG predictor parameters")
                    row_length = (columns * colors * bits + 7) // 8
                    bytes_per_pixel = (colors * bits + 7) // 8
                    if row_length > MAX_STREAM_DECODED or (row_length + 1) <= 1:
                        raise PdfSyntaxError("Unsupported PNG predictor row size")
                    previous = bytearray(row_length)
                    decoded = bytearray()
                    if len(raw) % (row_length + 1):
                        raise PdfSyntaxError("Truncated PNG predictor data")
                    for offset in range(0, len(raw), row_length + 1):
                        method = raw[offset]
                        row = bytearray(raw[offset + 1:offset + row_length + 1])
                        if method not in (0, 1, 2, 3, 4):
                            raise PdfSyntaxError("Unknown PNG predictor filter")
                        for index in range(row_length):
                            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                            up = previous[index]
                            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                            if method == 1:
                                value = left
                            elif method == 2:
                                value = up
                            elif method == 3:
                                value = (left + up) // 2
                            elif method == 4:
                                predict = left + up - upper_left
                                distances = (abs(predict - left), abs(predict - up),
                                             abs(predict - upper_left))
                                value = (left, up, upper_left)[distances.index(min(distances))]
                            else:
                                value = 0
                            row[index] = (row[index] + value) % 256
                        decoded.extend(row)
                        previous = row
                        if len(decoded) > MAX_STREAM_DECODED:
                            raise PdfSyntaxError("Predictor output too large")
                    raw = bytes(decoded)
                elif predictor != 1:
                    raise PdfSyntaxError(f"Unsupported stream predictor {predictor}")
        return raw

    def report(self) -> dict[str, Any]:
        entries = []
        warnings = []
        page_count = None
        try:
            trailer = self.revisions[0]["trailer"]
            root = trailer.get("/Root")
            catalog = self.get(root) if isinstance(root, Ref) else root
            pages = catalog.get("/Pages") if isinstance(catalog, dict) else None
            tree = self.get(pages) if isinstance(pages, Ref) else pages
            if isinstance(tree, dict):
                page_count = tree.get("/Count")
        except (PdfSyntaxError, KeyError, AttributeError) as exc:
            warnings.append(f"Document catalog unavailable: {exc}")
        for ref in sorted(set(self.xref) | set(self.compressed),
                          key=lambda value: (value.number, value.generation)):
            try:
                value = self.get(ref)
                if isinstance(value, PdfStream):
                    entries.append({
                        "ref": str(ref), "type": "stream",
                        "attributes": to_json(value.attributes),
                        "raw_length": len(value.raw),
                        "raw_sha256": hashlib.sha256(value.raw).hexdigest(),
                    })
                else:
                    entries.append({"ref": str(ref), "type": "object", "value": to_json(value)})
            except (PdfSyntaxError, KeyError) as exc:
                warnings.append(f"{ref}: {exc}")
        return {
            "engine": "qpdf-studio-pure-python/0.1",
            "version": self.version,
            "size": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "page_count": page_count if isinstance(page_count, int) else None,
            "xref_entries": len(self.xref) + len(self.compressed),
            "compressed_objects": len(self.compressed),
            "revision_count": len(self.revisions),
            "trailers": [to_json(row) for row in self.revisions],
            "objects": entries,
            "warnings": warnings,
            "supports": ["classic xref", "XRef streams", "ObjStm objects",
                         "indirect objects", "primitive grammar", "FlateDecode",
                         "incremental /Prev references"],
            "unsupported": ["hybrid XRefStm supplemental index", "encrypted files",
                            "other compression filters", "complete rendering",
                            "font mapping", "signature trust"],
        }


def to_json(obj: Any) -> Any:
    if isinstance(obj, (Ref, PdfName)):
        return str(obj)
    if isinstance(obj, bytes):
        return {"byte_length": len(obj), "latin1_preview": obj[:128].decode("latin1")}
    if isinstance(obj, list):
        return [to_json(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    return obj


def main() -> None:
    parser = argparse.ArgumentParser(description="Early standalone pure-Python PDF structure parser")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()
    result = PdfDocument.open(arguments.pdf).report()
    body = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.out:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(body, encoding="utf-8")
    else:
        print(body)


if __name__ == "__main__":
    main()
