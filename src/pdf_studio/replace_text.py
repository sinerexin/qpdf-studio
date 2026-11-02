"""Direct PDF content-stream text replacement with strict font support.

This changes actual text-showing operands; it never paints a covering rectangle
or overlays replacement text. Supports WinAnsi standard Type1 faces and a
strict subset of embedded TrueType / Identity-H composite ToUnicode fonts.
Unsupported glyph encodings are reported explicitly.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from pypdf import PdfReader

from .editing import _signed_or_certified, page_contents_ready, save_edited_pdf
from .font_cmap import FontEmbeddingRequired, GlyphCodec, codec_for_font
from .text_metrics import describe_layout_change


STANDARD_FONTS = {
    "/Helvetica", "/Helvetica-Bold", "/Helvetica-Oblique", "/Helvetica-BoldOblique",
    "/Times-Roman", "/Times-Bold", "/Times-Italic", "/Times-BoldItalic",
    "/Courier", "/Courier-Bold", "/Courier-Oblique", "/Courier-BoldOblique",
}


def _font_supported(page: pikepdf.Page, font_key: str) -> bool:
    try:
        font = page.resources["/Font"][font_key]
        base = str(font.get("/BaseFont", ""))
        if str(font.get("/Subtype", "")) != "/Type1" or base not in STANDARD_FONTS:
            return False
        if font.get("/ToUnicode") is not None:
            return False
        encoding = font.get("/Encoding", pikepdf.Name("/StandardEncoding"))
        # StandardEncoding is *not* equivalent to cp1252. Do not guess its mapping.
        if str(encoding) != "/WinAnsiEncoding":
            return False
        descriptor = font.get("/FontDescriptor")
        if descriptor is not None and any(
            descriptor.get(part) is not None
            for part in ("/FontFile", "/FontFile2", "/FontFile3")
        ):
            return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _decode_ansi_string(obj: object) -> str | None:
    if not isinstance(obj, pikepdf.String):
        return None
    try:
        binary = bytes(obj)
        if not binary or any(value in (0x00, 0x7f) for value in binary):
            return None
        decoded = binary.decode("cp1252", errors="strict")
        if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in decoded):
            return None
        return decoded
    except (UnicodeError, TypeError, ValueError):
        return None


def _text_operand(instruction: pikepdf.ContentStreamInstruction) -> int | None:
    operator = str(instruction.operator)
    if operator in ("Tj", "'") and len(instruction.operands) == 1:
        return 0
    if operator == '"' and len(instruction.operands) == 3:
        return 2
    return None


def _font_matrix_scale(page: pikepdf.Page, font_key: str) -> float:
    """Type1/TrueType default FontMatrix is 0.001. Other values change the drawn em size."""
    if not font_key:
        return 1.0
    try:
        font = page.resources["/Font"][font_key]
        if str(font.get("/Subtype", "")) == "/Type0":
            font = font["/DescendantFonts"][0]
        matrix = font.get("/FontMatrix")
        if matrix is None:
            return 1.0
        scale = abs(float(matrix[3]))
        return 1.0 if scale == 0 else scale / 0.001
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return 1.0


def _tm_em_scale(operands) -> float:
    """Em height in user space from Tm's y-axis. Identity Tm yields 1."""
    if len(operands) != 6:
        return 1.0
    try:
        length = math.hypot(float(operands[2]), float(operands[3]))
    except (TypeError, ValueError):
        return 1.0
    return length if length else 1.0


def pdf_font_size(page: pikepdf.Page, font_key: str, tf_size: float, tm_operands=None) -> float:
    """Visual size of the identified run, from Tf, Tm and FontMatrix only."""
    try:
        tf_size = float(tf_size)
    except (TypeError, ValueError):
        return 0.0
    return abs(tf_size * _tm_em_scale(tm_operands or (1, 0, 0, 1, 0, 0)) * _font_matrix_scale(page, font_key))


def _collect_runs(page: pikepdf.Page) -> list[dict]:
    """Return physical Tj/TJ/'/\" string runs, including unsupported ones."""
    runs = []
    current_font = ""
    current_size = 0.0
    current_tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    current_weight = "regular"
    current_codec: GlyphCodec | None = None
    supported_winansi = False
    for index, command in enumerate(pikepdf.parse_content_stream(page)):
        op = str(command.operator)
        if op == "Tf" and len(command.operands) >= 2:
            current_font = str(command.operands[0])
            try:
                current_size = float(command.operands[1])
            except (TypeError, ValueError):
                current_size = 0.0
            try:
                from .font_replacement import font_weight
                current_weight = font_weight(page, current_font)
            except (AttributeError, KeyError, TypeError, ValueError):
                current_weight = "regular"
            supported_winansi = _font_supported(page, current_font)
            current_codec = (
                codec_for_font(page, current_font) if not supported_winansi else None
            )
        elif op == "Tm" and len(command.operands) == 6:
            try:
                current_tm = tuple(float(value) for value in command.operands)
            except (TypeError, ValueError):
                current_tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif op == "BT":
            current_tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        def decode_operand(literal):
            return (
                current_codec.decode(bytes(literal))
                if current_codec is not None else _decode_ansi_string(literal)
                if supported_winansi else None
            )
        font_usable = supported_winansi or current_codec is not None
        codec_kind = (
            "winansi-type1" if supported_winansi else
            "embedded-to-unicode-cmap" if current_codec else "unsupported"
        )
        operand_index = _text_operand(command)
        if op == "TJ" and len(command.operands) == 1 and isinstance(command.operands[0], pikepdf.Array):
            for subindex, chunk in enumerate(command.operands[0]):
                if not isinstance(chunk, pikepdf.String):
                    continue
                text = decode_operand(chunk)
                runs.append({
                    "operator_index": index,
                    "operator": op,
                    "operand_index": 0,
                    "array_index": subindex,
                    "font_resource": current_font,
                    "tf_size": current_size,
                    "font_size": pdf_font_size(page, current_font, current_size, current_tm),
                    "font_weight": current_weight,
                    "font_supported": font_usable,
                    "font_encoding": codec_kind,
                    "source_bytes_hex": bytes(chunk).hex(),
                    "text": text,
                    "editable": text is not None and font_usable,
                })
            continue
        if operand_index is None:
            continue
        literal = command.operands[operand_index]
        text = decode_operand(literal)
        runs.append({
            "operator_index": index,
            "operator": op,
            "operand_index": operand_index,
            "array_index": None,
            "font_resource": current_font,
            "tf_size": current_size,
            "font_size": pdf_font_size(page, current_font, current_size, current_tm),
            "font_weight": current_weight,
            "font_supported": font_usable,
            "font_encoding": codec_kind,
            "source_bytes_hex": bytes(literal).hex() if isinstance(literal, pikepdf.String) else "",
            "text": text,
            "editable": text is not None and font_usable,
        })
    return runs


def list_text_runs(source: Path, page: int, *, max_runs: int = 10000) -> dict:
    """Identify safely selectable, physically stored PDF text-showing operands."""
    src = Path(source).resolve(strict=True)
    with pikepdf.Pdf.open(src) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        runs = _collect_runs(pdf.pages[page - 1])
        return {
            "page": page,
            "source": src.name,
            "signed_or_certified": _signed_or_certified(pdf),  # 限制标记：为真时表示原文件已签名或已认证
            "runs": runs[:max_runs],
            "truncated": len(runs) > max_runs,
            "note": "Supports WinAnsi, ToUnicode including mixed code widths, embedded CMaps, "
                    "and the standard CJK encodings a viewer already uses; no overlay or reflow.",
        }


def glyph_near_pdf_point(source: Path, *, page: int, x: float, y: float) -> dict | None:
    """Find PDFium's visible text word covering clicked PDF user-space position.

    Used for candidate *selection only*. Actual edits require the user to
    select a verified content-stream operand from list_text_runs().
    """
    document = pdfium.PdfDocument(str(Path(source).resolve(strict=True)))
    try:
        if not 1 <= page <= len(document):
            raise ValueError("Page outside document")
        pdf_page = document[page - 1]
        try:
            textpage = pdf_page.get_textpage()
            try:
                char_index = None
                for i in range(min(textpage.count_chars(), 100_000)):
                    try:
                        left, bottom, right, top = textpage.get_charbox(i)
                    except (IndexError, ValueError, RuntimeError):
                        continue
                    if left - 1.5 <= x <= right + 1.5 and bottom - 1.5 <= y <= top + 1.5:
                        char_index = i
                        break
                if char_index is None:
                    return None
                all_text = textpage.get_text_range()
                # Character mapping may normalize some glyphs; use only as a
                # candidate hint and never as proof of direct stream identity.
                if not all_text or char_index >= len(all_text):
                    return None
                left_index = char_index
                while left_index and not all_text[left_index - 1].isspace():
                    left_index -= 1
                right_index = char_index + 1
                while right_index < len(all_text) and not all_text[right_index].isspace():
                    right_index += 1
                return {
                    "page": page,
                    "text_hint": all_text[left_index:right_index],
                    "char_index": char_index,
                    "position_pdf": [round(x, 3), round(y, 3)],
                }
            finally:
                textpage.close()
        finally:
            pdf_page.close()
    finally:
        document.close()


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _encode_for_run(page: pikepdf.Page, runs: list[dict], candidate: dict,
                    original: str, replacement: str) -> bytes:
    """Encode characters that have one internal code in the current font."""
    codec = (
        codec_for_font(page, candidate["font_resource"])
        if candidate["font_encoding"] == "embedded-to-unicode-cmap" else None
    )
    if codec is not None:
        if candidate["text"] != original:
            raise ValueError("Embedded font source does not match the expected text")
        observed = set()
        for run in runs:
            if run["font_resource"] != candidate["font_resource"] or not run["editable"]:
                continue
            parts = codec.split(bytes.fromhex(run["source_bytes_hex"]))
            if not parts:
                continue
            observed.update(parts)
        return codec.encode(replacement, observed_codes=observed)
    if candidate["font_encoding"] != "winansi-type1":
        raise ValueError("Unsupported or ambiguous PDF font encoding")
    try:
        encoded = replacement.encode("cp1252", errors="strict")
    except UnicodeEncodeError as error:
        # 与中文字体子集共用换字体信号，自动模式不再把它当成最终失败。
        raise FontEmbeddingRequired(
            "原 WinAnsi 字体不能编码当前文字，需要嵌入可用字体。"
        ) from error
    if any(value in (0x00, 0x7f) or value < 32 for value in encoded):
        raise ValueError("Replacement includes control characters")
    return encoded


def replace_text_run(source: Path, destination: Path, *, page: int,
                     operator_index: int, expected_text: str, replacement: str,
                     array_index: int | None = None) -> dict:
    """Replace one exact PDF text operand, and preserve the source unchanged.

    A preview may be produced with the same method into a temporary new PDF.
    Returns a report with both hashes, selected operator, and extraction results.
    """
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if src == dst or dst.exists():
        raise ValueError("Output must be a new, unused PDF filename")  # 限制：文字替换必须另存为新的未使用 PDF
    if not isinstance(operator_index, int) or operator_index < 0:
        raise ValueError("Invalid content operator index")
    if (not expected_text or not replacement or expected_text == replacement
            or len(replacement) > 3000 or "\n" in replacement or "\r" in replacement):
        raise ValueError("Replacement must be nonempty, distinct, one line and <=3000 characters")
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported by direct text editing")  # 限制：加密 PDF 不能直接替换文字
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        commands = list(pikepdf.parse_content_stream(sheet))
        if operator_index >= len(commands):
            raise ValueError("The selected content-stream operator no longer exists")
        all_runs = _collect_runs(sheet)
        candidate = next((
            run for run in all_runs
            if run["operator_index"] == operator_index and run["array_index"] == array_index
        ), None)
        if not candidate or not candidate["editable"] or candidate["text"] != expected_text:
            raise ValueError("Target text/font no longer matches a supported editable PDF run")
        chosen = commands[operator_index]
        if array_index is None:
            operand = chosen.operands[candidate["operand_index"]]
        else:
            operand = chosen.operands[candidate["operand_index"]][array_index]
        raw = bytes(operand)
        codec = (
            codec_for_font(sheet, candidate["font_resource"])
            if candidate["font_encoding"] == "embedded-to-unicode-cmap" else None
        )
        if codec is not None:
            if codec.decode(raw) != expected_text:
                raise ValueError("Embedded font source bytes do not decode to expected text")
        elif candidate["font_encoding"] == "winansi-type1":
            if raw.decode("cp1252", errors="strict") != expected_text:
                raise ValueError("Source bytes do not match the expected text")
        encoded = _encode_for_run(sheet, all_runs, candidate, expected_text, replacement)
        # pikepdf serializes the string operand according to PDF rules.
        # We never add a paint operation or a covering rectangle.
        updated = list(chosen.operands)
        if array_index is None:
            updated[candidate["operand_index"]] = pikepdf.String(
                encoded if codec is not None else replacement
            )
            encoded_value = updated[candidate["operand_index"]]
        else:
            parts = list(chosen.operands[candidate["operand_index"]])
            parts[array_index] = pikepdf.String(
                encoded if codec is not None else replacement
            )
            updated[candidate["operand_index"]] = pikepdf.Array(parts)
            encoded_value = parts[array_index]
        if bytes(encoded_value) != encoded:
            raise ValueError("Text encoding was not preserved; refusing unsafe replacement")
        commands[operator_index] = pikepdf.ContentStreamInstruction(updated, chosen.operator)
        rebuilt_stream = pikepdf.unparse_content_stream(commands)
        sheet.obj["/Contents"] = pdf.make_stream(rebuilt_stream)
        try:
            save_edited_pdf(src, pdf, dst, page=page)
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    # Validate the output's actual page object rather than relying on an overlay.
    result_runs = list_text_runs(dst, page)["runs"]
    replaced = next((row for row in result_runs
                     if row["operator_index"] == operator_index and row["array_index"] == array_index), None)
    if replaced is None or replaced["text"] != replacement:
        dst.unlink(missing_ok=True)
        raise ValueError("Output failed text-stream replacement verification")
    text_extracted = PdfReader(str(dst)).pages[page - 1].extract_text()
    result = {
        "operation": "replace-existing-pdf-text-operand",
        "source": src.name,
        "source_sha256": _hash(src),
        "output": dst.name,
        "output_sha256": _hash(dst),
        "page": page,
        "operator_index": operator_index,
        "array_index": array_index,
        "font_resource": candidate["font_resource"],
        "font_encoding": candidate["font_encoding"],
        "old_text": expected_text,
        "new_text": replacement,
        "text_visible_in_extraction": replacement in text_extracted,
        "method": "direct replacement of existing content-stream text operand; no cover/overlay",
        "limitation": "No automatic reflow; replacing text can change line width",
        "layout_diagnostics": describe_layout_change(
            src, dst, page=page, original=expected_text, replacement=replacement,
        ),
    }
    return result


def list_tj_spans(source: Path, page: int) -> list[dict]:
    """List complete editable text of a single TJ array, not unrelated operators."""
    inventory = list_text_runs(source, page)
    grouped: dict[int, list[dict]] = {}
    for run in inventory["runs"]:
        if run["operator"] == "TJ":
            grouped.setdefault(run["operator_index"], []).append(run)
    result = []
    for operator, pieces in grouped.items():
        pieces.sort(key=lambda item: item["array_index"])
        if len(pieces) < 2 or not all(item["editable"] for item in pieces):
            continue
        if len({item["font_resource"] for item in pieces}) != 1:
            continue
        result.append({
            "operator_index": operator,
            "text": "".join(item["text"] for item in pieces),
            "parts": len(pieces),
            "font_resource": pieces[0]["font_resource"],
            "font_encoding": pieces[0]["font_encoding"],
            "editable": True,
            "restriction": "Same Unicode character count; preserve TJ numeric spacing and text operators",  # 限制：完整 TJ 替换要求字符数相同，并保留间距操作数
        })
    return result


def replace_tj_span(source: Path, destination: Path, *, page: int,
                    operator_index: int, expected_text: str, replacement: str) -> dict:
    """Atomically replace all strings of one TJ array, preserving its numbers.

    We preserve the number of Unicode scalar values assigned to each segment,
    and the non-string TJ spacing values. Reflow or cross-font replacement is
    NOT attempted; unsupported fonts/characters cause an explicit failure.
    """
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if dst.exists() or src == dst:
        raise ValueError("Destination must be a new unused PDF path")  # 限制：整段替换必须另存为新的未使用 PDF
    if (not expected_text or not replacement or expected_text == replacement
            or len(expected_text) > 3000 or len(expected_text) != len(replacement)
            or "\n" in replacement or "\r" in replacement):
        raise ValueError("TJ span replacement requires equal nonzero character counts and one line")  # 限制：完整 TJ 替换必须字符数相同，且只能一行
    if not isinstance(operator_index, int) or operator_index < 0:
        raise ValueError("Invalid TJ operator index")
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        if not page_contents_ready(sheet):
            raise ValueError("This page has no usable content stream")
        operations = list(pikepdf.parse_content_stream(sheet))
        if operator_index >= len(operations) or str(operations[operator_index].operator) != "TJ":
            raise ValueError("Target is not an existing TJ array operation")
        chosen = operations[operator_index]
        if len(chosen.operands) != 1 or not isinstance(chosen.operands[0], pikepdf.Array):
            raise ValueError("Malformed TJ content operation")
        all_runs = _collect_runs(sheet)
        parts = sorted(
            (row for row in all_runs if row["operator_index"] == operator_index
             and row["operator"] == "TJ"),
            key=lambda row: row["array_index"],
        )
        if len(parts) < 2 or not all(row["editable"] for row in parts):
            raise ValueError("TJ fragments not all safely editable")
        if len({row["font_resource"] for row in parts}) != 1:
            raise ValueError("Mixed font resources in a TJ array are not safely editable")
        old_text = "".join(row["text"] for row in parts)
        if old_text != expected_text:
            raise ValueError("Source TJ concatenated text no longer matches expected text")
        array = list(chosen.operands[0])
        cursor = 0
        modifications = []
        for part in parts:
            length = len(part["text"])
            new_text = replacement[cursor:cursor + length]
            cursor += length
            original_bytes = bytes(array[part["array_index"]])
            if original_bytes != bytes.fromhex(part["source_bytes_hex"]):
                raise ValueError("TJ source bytes mismatch")
            encoded = _encode_for_run(sheet, all_runs, part, part["text"], new_text)
            new_object = pikepdf.String(
                encoded if part["font_encoding"] == "embedded-to-unicode-cmap"
                else new_text
            )
            if bytes(new_object) != encoded:
                raise ValueError("Font encoding mismatch; refusing TJ replacement")
            array[part["array_index"]] = new_object
            modifications.append({
                "array_index": part["array_index"],
                "before": part["text"], "after": new_text,
            })
        operations[operator_index] = pikepdf.ContentStreamInstruction(
            [pikepdf.Array(array)], chosen.operator,
        )
        sheet.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(operations))
        try:
            save_edited_pdf(src, pdf, dst, page=page)
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    resulting = list_tj_spans(dst, page)
    selected = next((row for row in resulting
                     if row["operator_index"] == operator_index), None)
    if selected is None or selected["text"] != replacement:
        dst.unlink(missing_ok=True)
        raise ValueError("Serialized TJ text did not match replacement")
    report = {
        "operation": "replace-multi-fragment-TJ-text",
        "source": src.name, "source_sha256": _hash(src),
        "output": dst.name, "output_sha256": _hash(dst),
        "page": page, "operator_index": operator_index,
        "old_text": expected_text, "new_text": replacement,
        "parts": modifications,
        "method": "Direct TJ array string operands; numerical spacing preserved; no cover or overlay",
        "limitation": "No automatic paragraph reflow, fixed number of text characters",
        "layout_diagnostics": describe_layout_change(
            src, dst, page=page, original=expected_text, replacement=replacement,
        ),
    }
    return report
