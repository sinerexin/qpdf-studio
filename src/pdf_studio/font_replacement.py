"""嵌入本机字体替换真实 Tj/TJ 文字，保留原文字定位、颜色和周边内容。"""
from __future__ import annotations

import hashlib
import io
import math
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import pikepdf
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .editing import page_contents_ready, save_edited_pdf
from .replace_text import _collect_runs, _hash, pdf_font_size


_FAMILY_FONTS = {
    'hei': {
        'regular': ('msyh.ttc', 'simhei.ttf', 'simsun.ttc'),
        'bold': ('msyhbd.ttc', 'simhei.ttf', 'msyh.ttc'),
    },
    'song': {
        'regular': ('simsun.ttc', 'msyh.ttc', 'simhei.ttf'),
        'bold': ('simsunb.ttf', 'msyhbd.ttc', 'simhei.ttf'),
    },
    'kai': {
        'regular': ('simkai.ttf', 'msyh.ttc', 'simsun.ttc'),
        'bold': ('msyhbd.ttc', 'simhei.ttf', 'simkai.ttf'),
    },
    'fang': {
        'regular': ('simfang.ttf', 'simsun.ttc', 'msyh.ttc'),
        'bold': ('msyhbd.ttc', 'simhei.ttf', 'simfang.ttf'),
    },
}
_REGULAR_FONTS = _FAMILY_FONTS['hei']['regular']
_BOLD_FONTS = _FAMILY_FONTS['hei']['bold']


def default_chinese_font(weight: str = 'regular', kind: str = 'hei') -> Path:
    """Pick a local CJK face matching the original weight and family."""
    folder = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    family = _FAMILY_FONTS.get(kind, _FAMILY_FONTS['hei'])
    names = family.get(weight, family['regular'])
    for name in names:
        font = folder / name
        if font.is_file():
            return font
    raise ValueError('原字体缺少中文字形，且本机未找到可用的中文字体；未生成修改文件。')


def font_weight(page: pikepdf.Page, font_key: str) -> str:
    """Read bold vs regular from the font name and descriptor. Unknown stays regular."""
    try:
        font = page.resources['/Font'][font_key]
    except (AttributeError, KeyError, TypeError, ValueError):
        return 'regular'
    described = font
    if str(font.get('/Subtype', '')) == '/Type0':
        try:
            described = font['/DescendantFonts'][0]
        except (KeyError, IndexError, TypeError, ValueError):
            described = font
    names = ' '.join(str(item.get('/BaseFont', '')) for item in (font, described)).lower()
    if any(token in names for token in ('bold', 'black', 'heavy', 'semibold', 'demibold')):
        return 'bold'
    descriptor = described.get('/FontDescriptor') or font.get('/FontDescriptor')
    if descriptor is None:
        return 'regular'
    try:
        if float(descriptor.get('/FontWeight', 400)) >= 600:
            return 'bold'
        if float(descriptor.get('/StemV', 0)) >= 110:
            return 'bold'
        if int(descriptor.get('/Flags', 0)) & (1 << 18):
            return 'bold'
    except (TypeError, ValueError):
        return 'regular'
    return 'regular'


def font_kind(page: pikepdf.Page, font_key: str) -> str:
    """Classify the original face so the fallback can stay in the same family."""
    from .replace_text import _font_supported
    try:
        font = page.resources['/Font'][font_key]
    except (AttributeError, KeyError, TypeError, ValueError):
        return 'hei'
    described = font
    if str(font.get('/Subtype', '')) == '/Type0':
        try:
            described = font['/DescendantFonts'][0]
        except (KeyError, IndexError, TypeError, ValueError):
            described = font
    names = ' '.join(str(item.get('/BaseFont', '')) for item in (font, described)).lower()
    if any(token in names for token in ('kai', 'kaiti')):
        return 'kai'
    if any(token in names for token in ('fangsong', 'fang')):
        return 'fang'
    if any(token in names for token in ('song', 'ming', 'mincho', 'simsun', 'stsong', 'serif')):
        return 'song'
    if any(token in names for token in (
            'hei', 'gothic', 'yahei', 'msyh', 'simhei', 'deng', 'sourcehansans',
            'notosans', 'pingfang', 'jhenghei')):
        return 'hei'
    if _font_supported(page, font_key) or any(token in names for token in (
            'helvetica', 'arial', 'calibri', 'verdana', 'tahoma', 'times', 'courier')):
        return 'latin'
    return 'hei'


def _original_can_encode(page: pikepdf.Page, candidate: dict, runs: list[dict], text: str) -> bool:
    from .font_cmap import FontEmbeddingRequired
    from .replace_text import _encode_for_run
    try:
        _encode_for_run(page, runs, candidate, candidate['text'], text)
        return True
    except (FontEmbeddingRequired, ValueError, KeyError, TypeError, UnicodeError):
        return False


def _text_segments(page: pikepdf.Page, candidate: dict, runs: list[dict], text: str) -> list[tuple[bool, str]]:
    segments: list[tuple[bool, str]] = []
    for char in text:
        use_original = _original_can_encode(page, candidate, runs, char)
        if segments and segments[-1][0] == use_original:
            segments[-1] = (use_original, segments[-1][1] + char)
        else:
            segments.append((use_original, char))
    return segments


def _join_tj_text(parts, runs_by_index) -> str | None:
    texts = []
    for index, item in enumerate(parts):
        if isinstance(item, pikepdf.String):
            row = runs_by_index.get(index)
            if row is None or row.get('text') is None:
                return None
            texts.append(row['text'])
    return ''.join(texts)


_SUBSET_FONT = re.compile(r'^/[A-Z]{6}\+')


def extract_complete_font(page: pikepdf.Page, font_key: str, folder: Path) -> Path | None:
    """Reuse a fully embedded TrueType/OpenType face. Subset fonts are skipped."""
    try:
        font = page.resources['/Font'][font_key]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    names = [str(font.get('/BaseFont', ''))]
    target = font
    if str(font.get('/Subtype', '')) == '/Type0':
        try:
            target = font['/DescendantFonts'][0]
            names.append(str(target.get('/BaseFont', '')))
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    if any(_SUBSET_FONT.match(name if name.startswith('/') else '/' + name) for name in names):
        return None
    descriptor = target.get('/FontDescriptor') or font.get('/FontDescriptor')
    if descriptor is None:
        return None
    payload = None
    suffix = '.ttf'
    if descriptor.get('/FontFile2') is not None:
        payload = bytes(descriptor['/FontFile2'].read_bytes())
        suffix = '.ttf'
    elif descriptor.get('/FontFile3') is not None:
        payload = bytes(descriptor['/FontFile3'].read_bytes())
        suffix = '.otf'
    if not payload or len(payload) < 256:
        return None
    if payload[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'ttcf'):
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'extracted-{hashlib.sha256(payload).hexdigest()[:12]}{suffix}'
    if not path.exists():
        path.write_bytes(payload)
    try:
        probe = 'PdfStudioExtract_' + hashlib.sha256(payload).hexdigest()[:10]
        pdfmetrics.registerFont(TTFont(probe, str(path)))
    except Exception:
        path.unlink(missing_ok=True)
        return None
    return path


def _uniform_text_scale(operands) -> float | None:
    """Return the absolute text scale when Tm only scales, without rotation or skew."""
    if len(operands) != 6:
        return None
    try:
        a, b, c, d = (_number(operands[0]), _number(operands[1]),
                      _number(operands[2]), _number(operands[3]))
    except (TypeError, ValueError):
        return None
    if abs(b) > 1e-3 or abs(c) > 1e-3 or a == 0 or d == 0:
        return None
    if abs(abs(a) - abs(d)) > 0.02 * max(abs(a), 1):
        return None
    return abs(d)


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('页面含非有限数值，不能安全换字体。')  # 限制：页面含非有限数值时不能换字体
    return number


def _inherited(page_object, key, default):
    node = page_object
    for _ in range(128):
        if key in node:
            return node[key]
        parent = node.get('/Parent')
        if parent is None:
            return default
        node = parent
    raise ValueError('页面继承链过深或循环，不能安全换字体。')  # 限制：页面继承过深或循环时不能换字体


def _isolated_object(page, commands, operator_index):
    state = {'font': None, 'size': 0, 'leading': 0, 'Tc': 0, 'Tw': 0,
             'Tz': 100, 'Ts': 0, 'Tr': 0, 'transformed': False, 'external_state': False,
             'fill': None}
    stack = []
    begin = None
    selected = None
    for i, command in enumerate(commands):
        op, args = str(command.operator), list(command.operands)
        if op == 'q':
            stack.append(dict(state))
        elif op == 'Q':
            if not stack:
                raise ValueError('页面图形状态不配对，不能安全换字体。')  # 限制：页面图形状态不配对时不能换字体
            state = stack.pop()
        elif op == 'cm':
            if begin is not None and [_number(x) for x in args] != [1, 0, 0, 1, 0, 0]:
                state['transformed'] = True
        elif op == 'rg' and len(args) == 3:
            state['fill'] = (_number(args[0]), _number(args[1]), _number(args[2]))
        elif op == 'g' and len(args) == 1:
            gray = _number(args[0])
            state['fill'] = (gray, gray, gray)
        elif op == 'Tf':
            state['font'], state['size'] = str(args[0]), _number(args[1])
        elif op == 'TL':
            state['leading'] = _number(args[0])
        elif op in ('Tc', 'Tw', 'Tz', 'Ts', 'Tr'):
            state[op] = _number(args[0])
        elif op == 'BT':
            if begin is not None:
                raise ValueError('页面文字对象嵌套，不能安全换字体。')  # 限制：嵌套文字对象不能换字体
            begin = i
        if i == operator_index:
            if begin is None:
                raise ValueError('选中文字不在独立文字对象中。')
            selected = (begin, dict(state))
        if op == 'ET':
            if selected is not None:
                start, effective = selected
                part = commands[start + 1:i]
                names = [str(x.operator) for x in part]
                # 银行类 PDF 常把文字存成 TJ，并在同一对象里设置颜色、线帽、虚线。
                # 这些设置原样保留，不再因不是简化 Tj 就中断字体替换。
                allowed = {'Tm', 'Tf', 'TL', 'Tj', 'TJ', 'T*', 'rg', 'RG', 'g', 'G',
                           'k', 'K', 'J', 'j', 'w', 'M', 'd', 'Tc', 'Tw', 'Tz', 'Ts', 'Tr'}
                if (any(name not in allowed for name in names)
                        or names.count('Tj') + names.count('TJ') != 1 or names.count('Tm') != 1):
                    raise ValueError('自动换字体尚未支持此对象的多行或复杂排版；未生成结果。')
                tm_index = names.index('Tm')
                text_index = next(n for n, name in enumerate(names) if name in {'Tj', 'TJ'})
                if tm_index > text_index:
                    raise ValueError('文字定位不属于支持的单行格式。')
                matrix = [_number(x) for x in part[tm_index].operands]
                visual = pdf_font_size(page, effective['font'], effective['size'], matrix)
                if (effective['transformed']
                        or not effective['font'] or not 4 <= visual <= 96
                        or effective.get('Tr', 0) not in (0, 3)
                        or any(name in {'Tf', 'TL'} for name in names[text_index + 1:])):
                    raise ValueError('该文字含隐形描边或不支持的状态，不能自动换字体。')
                effective['visual_size'] = visual
                effective['matrix'] = matrix
                return start, i, matrix[4], matrix[5], effective
            begin = None
    raise ValueError('无法定位完整的独立文字对象。')


def _embedded_text_ops(text: str, *, pdf: pikepdf.Pdf, fonts: pikepdf.Dictionary,
                       mapping: dict, font_name: str, size: float, box: list[float],
                       tz: float = 100, tc: float = 0, tw: float = 0, fill=None):
    generated_bytes = io.BytesIO()
    canvas = Canvas(generated_bytes, pagesize=(box[2], box[3]))
    if fill is not None:
        canvas.setFillColorRGB(*fill)
    cursor = canvas.beginText(0, 0)
    cursor.setFont(font_name, size)
    if abs(tz - 100) > 0.05:
        cursor.setHorizScale(tz)
    if abs(tc) > 1e-6:
        cursor.setCharSpace(tc)
    if abs(tw) > 1e-6:
        cursor.setWordSpace(tw)
    cursor.textOut(text)
    canvas.drawText(cursor)
    canvas.save()
    generated_bytes.seek(0)
    ops = []
    with pikepdf.Pdf.open(generated_bytes) as generated:
        for key, value in generated.pages[0].resources['/Font'].items():
            if str(key) not in mapping:
                n = 1
                while f'/PdfStudioReplacement{n}' in fonts:
                    n += 1
                name = f'/PdfStudioReplacement{n}'
                fonts[name] = pdf.copy_foreign(value)
                mapping[str(key)] = name
        generated_ops = list(pikepdf.parse_content_stream(generated.pages[0]))
        text_begin = 0
        text_end = None
        for index, op in enumerate(generated_ops):
            if str(op.operator) == 'BT':
                text_begin = index
            elif str(op.operator) in {'Tj', 'TJ'}:
                text_end = next(n for n in range(index + 1, len(generated_ops))
                                if str(generated_ops[n].operator) == 'ET')
                break
        if text_end is None:
            raise ValueError('嵌入字体未生成可用的文字操作数。')
        for op in generated_ops[text_begin + 1:text_end]:
            if str(op.operator) == 'Tf':
                args = list(op.operands)
                args[0] = pikepdf.Name(mapping[str(args[0])])
                op = pikepdf.ContentStreamInstruction(args, op.operator)
            if str(op.operator) in {'Tf', 'Tj', 'TJ', 'Tz', 'Tc', 'Tw', 'rg', 'g'}:
                ops.append(op)
    return ops


def replace_isolated_text_with_font(source: Path, destination: Path, *, page: int,
                                    operator_index: int, expected_text: str,
                                    replacement: str, font_path: Path,
                                    array_index: int | None = None) -> dict:
    src, dst = Path(source).resolve(strict=True), Path(destination).resolve()
    if src == dst or dst.exists():
        raise ValueError('必须另存为不存在的新 PDF，不能覆盖原稿。')  # 限制：换字体必须另存为新 PDF，不能覆盖原稿
    if (not replacement.strip() or replacement == expected_text or len(replacement) > 3000
            or any(ord(c) < 32 or ord(c) == 127 for c in replacement)):
        raise ValueError('请输入与原文不同的单行文字（最多3000字，不含控制字符）。')
    if any(unicodedata.bidirectional(c) in {'R', 'AL', 'RLE', 'RLO', 'LRE', 'LRO', 'PDF', 'RLI', 'LRI', 'FSI', 'PDI'}
           or unicodedata.combining(c) or c in '\u200c\u200d' for c in replacement):
        raise ValueError('此模式暂不支持需要复杂塑形、双向排序或组合符号的文字。')  # 限制：复杂塑形、双向文字和组合符号不能用这个模式
    requested_font = Path(font_path)
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        if not 1 <= page <= len(pdf.pages):
            raise ValueError('页码超出文档范围。')
        sheet = pdf.pages[page - 1]
        if float(sheet.obj.get('/UserUnit', 1)) != 1:
            raise ValueError('暂不支持自定义页面单位的中文换字体。')
        if not page_contents_ready(sheet):
            raise ValueError('这一页没有可改写的内容流。')
        candidate = next((row for row in _collect_runs(sheet)
                          if row['operator_index'] == operator_index
                          and row['operator'] in {'Tj', 'TJ'} and row['array_index'] == array_index), None)
        if not candidate or not candidate['editable'] or candidate['text'] != expected_text:
            raise ValueError('原文字或字体映射已变化，请重新选择。')
        commands = list(pikepdf.parse_content_stream(sheet))
        start, end, x, y, state = _isolated_object(sheet, commands, operator_index)
        state['weight'] = font_weight(sheet, state['font'])
        state['kind'] = font_kind(sheet, state['font'])
        auto_names = {name.lower() for family in _FAMILY_FONTS.values()
                      for names in family.values() for name in names}
        fallback_kind = 'hei' if state['kind'] == 'latin' else state['kind']
        cache = Path(tempfile.mkdtemp(prefix='qpdf-font-'))
        extracted = None
        if requested_font.name.lower() in auto_names:
            extracted = extract_complete_font(sheet, state['font'], cache)
        font_path = extracted or (
            default_chinese_font(state['weight'], fallback_kind)
            if requested_font.name.lower() in auto_names else requested_font)
        font_path = Path(font_path).resolve(strict=True)

        def _load_face(path: Path):
            name = 'PdfStudioReplacement_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
            loaded = TTFont(name, str(path))
            absent = sorted({ord(c) for c in replacement if not loaded.face.charToGlyph.get(ord(c))})
            return name, loaded, absent

        font_name, face, missing = _load_face(font_path)
        if missing and extracted is not None:
            font_path = default_chinese_font(state['weight'], fallback_kind)
            font_name, face, missing = _load_face(font_path)
        if missing:
            raise ValueError('所选中文字体仍缺少字形：' + ', '.join(f'U+{c:04X}' for c in missing[:8]))
        pdfmetrics.registerFont(face)
        chosen = commands[operator_index]
        parts = list(chosen.operands[0]) if str(chosen.operator) == 'TJ' else None
        prefix = parts[:array_index] if parts is not None else []
        suffix = parts[array_index + 1:] if parts is not None else []
        page_runs = _collect_runs(sheet)
        runs_here = {row['array_index']: row for row in page_runs
                     if row['operator_index'] == operator_index}
        wanted = replacement
        if state['kind'] != 'latin' and not all(
                _original_can_encode(sheet, candidate, page_runs, char) for char in replacement):
            prefix_text = _join_tj_text(prefix, runs_here)
            suffix_text = _join_tj_text(suffix, runs_here)
            if prefix_text is not None and suffix_text is not None:
                replacement = prefix_text + replacement + suffix_text
                prefix, suffix = [], []
        # TJ 数字本来就是字距，前缀仍按原字体计算位移；不把它当正文删除。
        matrix = state.get('matrix') or [1, 0, 0, 1, x, y]
        from .paragraph_reflow import _measure_font
        from .replace_text import _encode_for_run
        if prefix:
            for index, item in enumerate(prefix):
                if isinstance(item, pikepdf.String):
                    row = runs_here.get(index)
                    if row is None or row['text'] is None:
                        raise ValueError('TJ 前缀文字无法测量，未完成字体替换。')
                    step = _measure_font(sheet, state['font'], row['text'], state['size'], bytes(item))
                    x += matrix[0] * step
                    y += matrix[1] * step
                else:
                    step = _number(item) * state['size'] / 1000
                    x -= matrix[0] * step
                    y -= matrix[1] * step
        box = [_number(n) for n in sheet.MediaBox]
        segments = _text_segments(sheet, candidate, page_runs, replacement)
        keep_width = 0.0
        embed_width = 0.0
        for use_original, chunk in segments:
            if use_original:
                encoded = _encode_for_run(sheet, page_runs, candidate, candidate['text'], chunk)
                keep_width += _measure_font(sheet, state['font'], chunk, state['size'], encoded)
            else:
                embed_width += pdfmetrics.stringWidth(chunk, font_name, state['size'])
        old_width = _measure_font(sheet, state['font'], expected_text, state['size'],
                                  bytes.fromhex(candidate['source_bytes_hex']))
        tz_extra = 100.0
        if embed_width > 0.5 and old_width > keep_width:
            tz_extra = min(150, max(50, (old_width - keep_width) / embed_width * 100))
        text_width = keep_width + embed_width * (tz_extra / 100)
        end_x = matrix[0] * text_width + x
        end_y = matrix[1] * text_width + y
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]
                and box[0] <= end_x <= box[2] and box[1] <= end_y <= box[3]):
            raise ValueError('新文字会超出页面边界；请缩短文字。此模式不自动重排。')
        original_resources = sheet.resources
        resources = pikepdf.Dictionary({str(k): v for k, v in original_resources.items()})
        fonts = pikepdf.Dictionary({str(k): v for k, v in original_resources['/Font'].items()})
        mapping = {}
        original_tf = pikepdf.ContentStreamInstruction(
            [pikepdf.Name(state['font']), state['size']], pikepdf.Operator('Tf'))
        rewritten = []
        if prefix:
            rewritten.append(pikepdf.ContentStreamInstruction([pikepdf.Array(prefix)], pikepdf.Operator('TJ')))
        for use_original, chunk in segments:
            if use_original:
                encoded = _encode_for_run(sheet, page_runs, candidate, candidate['text'], chunk)
                rewritten.append(original_tf)
                rewritten.append(pikepdf.ContentStreamInstruction(
                    [pikepdf.String(encoded)], pikepdf.Operator('Tj')))
            else:
                rewritten.extend(_embedded_text_ops(
                    chunk, pdf=pdf, fonts=fonts, mapping=mapping, font_name=font_name,
                    size=state['size'], box=box, tz=tz_extra, tc=state.get('Tc', 0),
                    tw=state.get('Tw', 0), fill=state.get('fill')))
        if abs(tz_extra - 100) > 0.05:
            rewritten.append(pikepdf.ContentStreamInstruction(
                [state.get('Tz', 100)], pikepdf.Operator('Tz')))
        rewritten.append(original_tf)
        if suffix:
            old_width = _measure_font(sheet, state['font'], expected_text, state['size'],
                                      bytes.fromhex(candidate['source_bytes_hex']))
            compensation = (text_width - old_width) * 1000 / state['size']
            rewritten.append(pikepdf.ContentStreamInstruction(
                [pikepdf.Array([compensation])], pikepdf.Operator('TJ')))
            rewritten.append(pikepdf.ContentStreamInstruction([pikepdf.Array(suffix)], pikepdf.Operator('TJ')))
        sheet.obj['/Contents'] = pdf.make_stream(pikepdf.unparse_content_stream(
            commands[:operator_index] + rewritten + commands[operator_index + 1:]))
        resources['/Font'] = fonts
        sheet.obj['/Resources'] = resources
        preserved = False
        try:
            preserved = save_edited_pdf(src, pdf, dst, page=page)
            if wanted not in PdfReader(str(dst)).pages[page - 1].extract_text():
                raise ValueError('换字体结果未能提取出目标文字，未交付此文件。')
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    limitation = '字体外观改变；当前支持独立水平单行 Tj 或 TJ 片段，不自动重排段落'
    if preserved:
        limitation += '。原签名的字节范围保持不变，只覆盖签署时的修订，不覆盖这次换字后的页面'
    report = {
        'operation': 'replace-isolated-text-object-with-embedded-font',
        'source_sha256': _hash(src), 'output_sha256': _hash(dst), 'page': page,
        'operator_index': operator_index, 'array_index': array_index, 'original_text': expected_text,
        'replacement': wanted, 'embedded_font': font_path.name,
        'origin_pdf': [x, y], 'font_size_pt': state['visual_size'],
        'font_weight': state['weight'], 'font_kind': state['kind'], 'source_font': state['font'],
        'original_bytes_preserved': preserved,
        'method': '替换选中文字的真实显示操作并嵌入字体；保留原定位、颜色，不添加遮盖矩形',
        'limitation': limitation,
    }
    return report
