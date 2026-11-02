"""Conservative paragraph reflow by REWRITING an existing PDF text object.

No painting a rectangle over text, overlaying a replacement, or forging font
resources. Applies only to isolated BT ... ET objects with a fixed horizontal
text origin, one supported font, one leading value and Tj/T* line operations.
More complex transforms or text operators are rejected, not guessed.
"""
from __future__ import annotations

import math
from pathlib import Path

import pikepdf
from reportlab.pdfbase import pdfmetrics

from .editing import page_contents_ready
from .font_cmap import codec_for_font
from .replace_text import _collect_runs, _font_supported, _hash


MAX_PARAGRAPH_CHARS = 6000
MAX_REFLOWED_LINES = 80
_ALLOWED_SETUP = {"Tm", "Tf", "TL"}


def _number(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite PDF text numeric operand")
    return number


def _measure_font(page: pikepdf.Page, font_resource: str, text: str,
                  font_size: float, raw: bytes) -> float:
    """Calculate the advance from PDF font metrics without drawing fake text."""
    font = page.resources["/Font"][font_resource]
    if _font_supported(page, font_resource):
        font_name = str(font["/BaseFont"])[1:]
        return float(pdfmetrics.stringWidth(text, font_name, font_size))

    codec = codec_for_font(page, font_resource)
    if codec is None or codec.decode(raw) != text:
        raise ValueError("Unsupported or ambiguous font mapping for paragraph reflow")
    parts = codec.split(raw)
    if not parts:
        raise ValueError("Unsupported or ambiguous font mapping for paragraph reflow")
    numbers = [int.from_bytes(part, "big") for part in parts]
    if str(font.get("/Subtype", "")) == "/TrueType":
        first = int(font.get("/FirstChar", 0))
        widths = font.get("/Widths")
        if widths is None:
            raise ValueError("Embedded TrueType font has no widths array")
        measured = 0.0
        for code in numbers:
            offset = code - first
            if offset < 0 or offset >= len(widths):
                raise ValueError("Character lacks embedded TrueType width")
            measured += _number(widths[offset])
        return measured * font_size / 1000
    if str(font.get("/Subtype", "")) == "/Type0":
        descendants = font["/DescendantFonts"]
        sub = descendants[0]
        default_width = _number(sub.get("/DW", 1000))
        explicit: dict[int, float] = {}
        entries = list(sub.get("/W", []))
        pos = 0
        while pos < len(entries):
            start = int(entries[pos]); pos += 1
            if pos >= len(entries):
                raise ValueError("Malformed CIDFont /W array")
            next_item = entries[pos]; pos += 1
            if isinstance(next_item, pikepdf.Array):
                for index, item in enumerate(next_item):
                    explicit[start + index] = _number(item)
            else:
                end = int(next_item)
                if pos >= len(entries) or end < start or end - start > 65536:
                    raise ValueError("Malformed CIDFont width range")
                width = _number(entries[pos]); pos += 1
                for code in range(start, end + 1):
                    explicit[code] = width
        return sum(explicit.get(code, default_width) for code in numbers) * font_size / 1000
    raise ValueError("Unsupported font type for measured paragraph reflow")


def _font_encoder(page: pikepdf.Page, font_resource: str, observed_runs: list[dict]):
    if _font_supported(page, font_resource):
        def encode(text: str) -> bytes:
            try:
                raw = text.encode("cp1252")
            except UnicodeEncodeError as error:
                raise ValueError("This WinAnsi font cannot encode the requested characters") from error  # 限制：这个 WinAnsi 字体不能编码所请求的字符
            if any(char < 32 or char == 127 for char in raw):
                raise ValueError("Control characters are not supported in this font")  # 限制：这个字体不支持控制字符
            return raw
        return encode
    codec = codec_for_font(page, font_resource)
    if codec is None:
        raise ValueError("No usable ToUnicode reverse mapping for this font")
    observed: set[bytes] = set()
    for run in observed_runs:
        if run["font_resource"] != font_resource or not run["editable"]:
            continue
        parts = codec.split(bytes.fromhex(run["source_bytes_hex"]))
        if not parts:
            continue
        observed.update(parts)
    def encode(text: str) -> bytes:
        return codec.encode(text, observed_codes=observed)
    return encode


def _inspect_objects(page: pikepdf.Page) -> list[dict]:
    """Identify strictly supported independent text paragraphs on this page."""
    ops = list(pikepdf.parse_content_stream(page))
    # Refuse a transformed page entirely until CTM inversion and clipping are
    # modelled. ReportLab's identity setup cm remains supported.
    for op in ops:
        if str(op.operator) == "cm":
            try:
                if [_number(value) for value in op.operands] != [
                    1., 0., 0., 1., 0., 0.,
                ]:
                    return []
            except (TypeError, ValueError):
                return []
    runs_by_op = {run["operator_index"]: run for run in _collect_runs(page)
                  if run["operator"] == "Tj" and run["array_index"] is None}
    blocks = []
    start: int | None = None
    for index, instruction in enumerate(ops):
        name = str(instruction.operator)
        if name == "BT":
            start = index
        elif name == "ET" and start is not None:
            portion = ops[start + 1:index]
            seen_text = False
            x = y = leading = font_size = None
            font = ""
            old_lines = []
            valid = True
            pending_line_break = False
            for offset, item in enumerate(portion, start=start + 1):
                op_name = str(item.operator)
                if not seen_text:
                    if op_name == "Tm" and len(item.operands) == 6:
                        matrix = [_number(n) for n in item.operands]
                        if matrix[:4] != [1.0, 0.0, 0.0, 1.0] or x is not None:
                            valid = False; break
                        x, y = matrix[4], matrix[5]
                    elif op_name == "Tf" and len(item.operands) == 2:
                        if font:
                            valid = False; break
                        font = str(item.operands[0])
                        font_size = _number(item.operands[1])
                    elif op_name == "TL" and len(item.operands) == 1:
                        leading = _number(item.operands[0])
                    elif op_name == "Tj" and len(item.operands) == 1:
                        seen_text = True
                        run = runs_by_op.get(offset)
                        if not run or not run["editable"]:
                            valid = False; break
                        old_lines.append(run["text"])
                        pending_line_break = False
                    else:
                        valid = False; break
                elif op_name == "Tj" and len(item.operands) == 1:
                    if not pending_line_break:
                        valid = False; break
                    run = runs_by_op.get(offset)
                    if not run or not run["editable"] or run["font_resource"] != font:
                        valid = False; break
                    old_lines.append(run["text"])
                    pending_line_break = False
                elif op_name == "T*" and not item.operands:
                    if pending_line_break:
                        valid = False; break
                    pending_line_break = True
                else:
                    valid = False; break
            if valid and old_lines and None not in (x, y, leading, font_size):
                if 0 < font_size <= 96 and font and leading >= font_size:
                    blocks.append({
                        "start_operator_index": start,
                        "end_operator_index": index,
                        "text": "\n".join(old_lines),
                        "font_resource": font,
                        "font_size_pt": font_size,
                        "leading_pt": leading,
                        "start_x_pt": x, "start_y_pt": y,
                        "original_lines": len(old_lines),
                        "direct_edit": True,
                        "scope": "isolated BT/ET text object; one font, identity text matrix, Tj/T* only",
                    })
            start = None
    return blocks


def list_reflow_blocks(source: Path, page: int) -> list[dict]:
    with pikepdf.Pdf.open(Path(source).resolve(strict=True)) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        return _inspect_objects(pdf.pages[page - 1])


def _wrapped_lines(text: str, measure, max_width: float) -> list[str]:
    lines: list[str] = []
    # Explicit line breaks are retained as paragraphs; soft wraps normalize
    # run-of-whitespace into a single space.
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = (current + " " + word) if current else word
            if measure(candidate) <= max_width + 0.001:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            piece = ""
            for character in word:
                expanded = piece + character
                if measure(expanded) <= max_width + 0.001:
                    piece = expanded
                    continue
                if not piece:
                    raise ValueError("Single glyph is wider than the specified paragraph width")
                lines.append(piece)
                piece = character
            current = piece
        lines.append(current)
        if len(lines) > MAX_REFLOWED_LINES:
            raise ValueError("Reflowed paragraph exceeds line count limit")  # 限制：重排后的段落超过行数上限
    return lines


def reflow_text_object(
    source: Path, destination: Path, *, page: int,
    start_operator_index: int, expected_text: str, replacement: str,
    width_pt: float, height_pt: float,
) -> dict:
    """Directly rewrite an existing isolated text object's line operations."""
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if src == dst or dst.exists():
        raise ValueError("Output must be a new PDF file; no overwrites")  # 限制：段落重排必须另存为新 PDF，不能覆盖
    if (not replacement.strip() or len(replacement) > MAX_PARAGRAPH_CHARS
            or len(expected_text) > MAX_PARAGRAPH_CHARS
            or "\r" in replacement.replace("\r\n", "")
            or expected_text == replacement):
        raise ValueError("Replacement text is empty, unchanged or exceeds bounded paragraph limits")  # 限制：替换文字为空、未变化，或超过段落长度上限
    if not (math.isfinite(width_pt) and 20 <= width_pt <= 3000
            and math.isfinite(height_pt) and 20 <= height_pt <= 3000):
        raise ValueError("Paragraph width/height must be bounded positive PDF points")
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        block = next((row for row in _inspect_objects(sheet)
                      if row["start_operator_index"] == start_operator_index), None)
        if block is None or block["text"] != expected_text:
            raise ValueError("Paragraph text/font or operation index no longer matches")
        media = [float(n) for n in sheet.MediaBox]
        x, y = block["start_x_pt"], block["start_y_pt"]
        if (not media[0] <= x <= media[2] or not media[1] <= y <= media[3]
                or x + width_pt > media[2] or y - height_pt < media[1]):
            raise ValueError("Paragraph bounding area exceeds page MediaBox")
        font = block["font_resource"]
        size = block["font_size_pt"]
        leading = block["leading_pt"]
        encode = _font_encoder(sheet, font, _collect_runs(sheet))
        def measure(text: str) -> float:
            if not text:
                return 0.0
            return _measure_font(sheet, font, text, size, encode(text))
        lines = _wrapped_lines(replacement, measure, width_pt)
        if len(lines) > MAX_REFLOWED_LINES:
            raise ValueError("Paragraph exceeds maximum supported line count")
        height_used = size + (len(lines) - 1) * leading
        if height_used > height_pt + 0.001:
            raise ValueError("Text does not fit inside the requested paragraph height")
        instructions = list(pikepdf.parse_content_stream(sheet))
        begin = block["start_operator_index"]
        finish = block["end_operator_index"]
        preceding = instructions[begin + 1:finish]
        first_text = next(i for i, ins in enumerate(preceding)
                          if str(ins.operator) == "Tj")
        rebuilt = list(preceding[:first_text])
        for index, line in enumerate(lines):
            binary = encode(line) if line else b""
            if index:
                rebuilt.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*")))
            rebuilt.append(pikepdf.ContentStreamInstruction(
                [pikepdf.String(binary)], pikepdf.Operator("Tj")
            ))
        instructions[begin + 1:finish] = rebuilt
        sheet.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf.save(dst)
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    saved = next((row for row in list_reflow_blocks(dst, page)
                  if row["start_operator_index"] == start_operator_index), None)
    if saved is None or saved["text"] != "\n".join(lines):
        dst.unlink(missing_ok=True)
        raise ValueError("Saved PDF text stream did not match computed wrapped lines")
    report = {
        "operation": "reflow-existing-isolated-PDF-text-object",
        "source": src.name, "source_sha256": _hash(src),
        "output": dst.name, "output_sha256": _hash(dst),
        "page": page, "start_operator_index": start_operator_index,
        "previous_text": expected_text, "requested_text": replacement,
        "wrapped_lines": lines, "before_line_count": block["original_lines"],
        "after_line_count": len(lines), "font_resource": font,
        "font_size_pt": size, "leading_pt": leading,
        "width_pt": width_pt, "height_pt": height_pt,
        "height_used_pt": round(height_used, 3),
        "method": "Replace original BT/ET internal text drawing operands; no overlay or cover",
        "scope_warning": "Only isolated text object is reflowed. Nearby unrelated content is not automatically moved.",
    }
    return report
