"""Focused font-fallback regression cases. Not automatically executed."""
from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.font_replacement import default_chinese_font, replace_isolated_text_with_font
from pdf_studio.replace_text import list_text_runs


def _sample(path):
    canvas = Canvas(str(path), pagesize=(612, 792))
    canvas.setFont('Helvetica', 17)
    canvas.drawString(64, 556, 'Project: Alpha Lab')
    canvas.drawString(64, 496, 'Status: DRAFT')
    canvas.save()
    return next(row for row in list_text_runs(path, 1)['runs'] if row['text'] == 'Project: Alpha Lab')


def _font():
    try:
        return default_chinese_font()
    except ValueError:
        pytest.skip('Local Windows CJK TrueType font not present')


def test_cjk_replacement_removes_original_not_covers(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    row = _sample(source)
    original = source.read_bytes()
    report = replace_isolated_text_with_font(
        source, target, page=1, operator_index=row['operator_index'],
        expected_text=row['text'], replacement='Project: Alpha 是中文', font_path=_font())
    text = PdfReader(str(target)).pages[0].extract_text()
    assert 'Project: Alpha 是中文' in text
    assert 'Project: Alpha Lab' not in text
    assert 'Status: DRAFT' in text
    assert source.read_bytes() == original
    assert report['embedded_font']
    with pikepdf.Pdf.open(source) as a, pikepdf.Pdf.open(target) as b:
        before = [str(x.operator) for x in pikepdf.parse_content_stream(a.pages[0])]
        after = [str(x.operator) for x in pikepdf.parse_content_stream(b.pages[0])]
        assert after.count('re') == before.count('re')
        assert after.count('Do') == before.count('Do')
        assert any(str(key).startswith('/PdfStudioReplacement') for key in b.pages[0].resources['/Font'].keys())


def test_existing_output_and_source_overwrite_rejected(tmp_path):
    source = tmp_path / 'source.pdf'
    row = _sample(source)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='不能覆盖'):
        replace_isolated_text_with_font(
            source, source, page=1, operator_index=row['operator_index'],
            expected_text=row['text'], replacement='中文', font_path=_font())
    assert source.read_bytes() == original


def test_signature_structure_does_not_block_font_replacement(tmp_path):
    source, signed, target = (tmp_path / x for x in ['source.pdf', 'signed.pdf', 'target.pdf'])
    row = _sample(source)
    with pikepdf.Pdf.open(source) as pdf:
        pdf.Root['/Perms'] = pikepdf.Dictionary()
        pdf.save(signed)
    replace_isolated_text_with_font(
        signed, target, page=1, operator_index=row['operator_index'],
        expected_text=row['text'], replacement='中文', font_path=_font())
    assert target.exists()


def test_rotated_text_matrix_keeps_original_tm(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    draw = Canvas(str(source))
    draw.setFont('Helvetica', 12)
    draw.drawString(80, 500, 'RotateMe')
    draw.save()
    row = next(item for item in list_text_runs(source, 1)['runs'] if item['text'] == 'RotateMe')
    with pikepdf.Pdf.open(source, allow_overwriting_input=True) as pdf:
        font = row['font_resource']
        pdf.pages[0].obj['/Contents'] = pdf.make_stream(
            f'BT {font} 12 Tf 0 1 -1 0 120 400 Tm (RotateMe) Tj ET'.encode('latin1'))
        pdf.save(source)
    row = next(item for item in list_text_runs(source, 1)['runs'] if item['text'] == 'RotateMe')
    assert abs(row['font_size'] - 12) < 0.2
    replace_isolated_text_with_font(
        source, target, page=1, operator_index=row['operator_index'],
        expected_text='RotateMe', replacement='中文', font_path=_font())
    with pikepdf.Pdf.open(target) as pdf:
        names = [str(op.operator) for op in pikepdf.parse_content_stream(pdf.pages[0])]
        assert 'Tm' in names
        assert '中文' in PdfReader(str(target)).pages[0].extract_text()


def test_extract_complete_font_skips_subset_prefix(tmp_path):
    from pdf_studio.font_replacement import extract_complete_font
    source = tmp_path / 'subset.pdf'
    canvas = Canvas(str(source))
    canvas.drawString(72, 72, 'x')
    canvas.save()
    with pikepdf.Pdf.open(source) as pdf:
        assert extract_complete_font(pdf.pages[0], '/F1', tmp_path) is None


def test_multiple_text_show_operations_are_not_guessed(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    canvas = Canvas(str(source))
    text = canvas.beginText(64, 556)
    text.setFont('Helvetica', 17)
    text.textLine('Project: Alpha Lab')
    text.textLine('Following line')
    canvas.drawText(text)
    canvas.save()
    row = next(x for x in list_text_runs(source, 1)['runs'] if x['text'] == 'Project: Alpha Lab')
    with pytest.raises(ValueError, match='复杂排版'):
        replace_isolated_text_with_font(
            source, target, page=1, operator_index=row['operator_index'],
            expected_text=row['text'], replacement='中文', font_path=_font())
    assert not target.exists()


def test_context_editor_automatic_and_strict_font_modes(tmp_path):
    # The GUI module is imported but no QApplication or window is created.
    from pdf_studio.qt_context_editor import ContextSelectionEditor
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    row = _sample(source)
    selection = {'source': source, 'page': 1, 'entry': {**row, 'kind': 'run'},
                 'text': 'Project: 中文', 'image': None, 'color': '#167C80', 'font_policy': 'original'}
    with pytest.raises(ValueError, match='需要嵌入'):
        ContextSelectionEditor._apply(target, selection)
    assert not target.exists()
    _font()
    selection['font_policy'] = 'auto'
    ContextSelectionEditor._apply(target, selection)
    assert 'Project: 中文' in PdfReader(str(target)).pages[0].extract_text()


def test_automatic_subset_signal_continues_to_embedding(monkeypatch, tmp_path):
    # 只验证调度：中文子集缺字信号被自动入口捕获，TJ 数组下标不丢失。
    from pdf_studio import qt_context_editor as module
    from pdf_studio.font_cmap import FontEmbeddingRequired
    calls = []

    def direct(*args, **kwargs):
        raise FontEmbeddingRequired('需要嵌入')

    def embed(*args, **kwargs):
        calls.append(kwargs)
        return {'embedded_font': 'synthetic.ttf'}

    monkeypatch.setattr(module, 'replace_text_run', direct)
    monkeypatch.setattr(module, 'default_chinese_font', lambda: tmp_path / 'synthetic.ttf')
    monkeypatch.setattr(module, 'replace_isolated_text_with_font', embed)
    selection = {'source': tmp_path / 'source.pdf', 'page': 1,
                 'entry': {'kind': 'run', 'font_encoding': 'embedded-to-unicode-cmap',
                           'operator_index': 23, 'array_index': 2, 'text': '原文'},
                 'text': '新文字', 'font_policy': 'auto'}
    assert module.ContextSelectionEditor._apply(tmp_path / 'target.pdf', selection)['embedded_font']
    assert calls[0]['array_index'] == 2 and calls[0]['replacement'] == '新文字'
    selection['font_policy'] = 'original'
    with pytest.raises(FontEmbeddingRequired):
        module.ContextSelectionEditor._apply(tmp_path / 'strict.pdf', selection)
    assert len(calls) == 1


def test_non_encoding_error_is_not_hidden_by_font_fallback(monkeypatch, tmp_path):
    # 路径、权限、对象变化等错误保留真实原因，不冒充缺字反复重试。
    from pdf_studio import qt_context_editor as module

    def direct(*args, **kwargs):
        raise OSError('synthetic read failure')

    monkeypatch.setattr(module, 'replace_text_run', direct)
    selection = {'source': tmp_path / 'source.pdf', 'page': 1,
                 'entry': {'kind': 'run', 'operator_index': 1, 'array_index': 0, 'text': '原文'},
                 'text': '新文', 'font_policy': 'auto'}
    with pytest.raises(OSError, match='synthetic read failure'):
        module.ContextSelectionEditor._apply(tmp_path / 'target.pdf', selection)


def _subset_tj_sample(path, *, fragments=False):
    # 构造带嵌入子集和 TJ 的合成样本；不用真实银行数据。
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    name = 'FontFallbackSyntheticSubset'
    pdfmetrics.registerFont(TTFont(name, str(_font())))
    canvas = Canvas(str(path), pagesize=(612, 792))
    canvas.setFillColorRGB(0.2, 0.4, 0.6)
    canvas.setFont(name, 13)
    canvas.drawString(64, 556, '原文')
    canvas.setFont('Helvetica', 11)
    canvas.drawString(64, 496, 'KEEP')
    canvas.save()
    row = next(run for run in list_text_runs(path, 1)['runs'] if run['text'] == '原文')
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        sheet = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(sheet))
        old = bytes(ops[row['operator_index']].operands[0])
        parts = [pikepdf.String(old[:1]), -25, pikepdf.String(old[1:])] if fragments else [pikepdf.String(old)]
        ops[row['operator_index']] = pikepdf.ContentStreamInstruction([pikepdf.Array(parts)], pikepdf.Operator('TJ'))
        sheet.obj['/Contents'] = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)
    rows = [run for run in list_text_runs(path, 1)['runs'] if run['operator_index'] == row['operator_index']]
    return rows


def test_subset_tj_new_chinese_glyph_uses_real_embedded_text(tmp_path):
    # 对标当前失败形态：原中文子集没有新字，选中的字符串在 TJ 数组中。
    from pdf_studio.qt_context_editor import ContextSelectionEditor
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    row = _subset_tj_sample(source)[0]
    original = source.read_bytes()
    selection = {'source': source, 'page': 1, 'entry': {**row, 'kind': 'run'},
                 'text': '原文源', 'font_policy': 'auto'}
    report = ContextSelectionEditor._apply(target, selection)
    text = PdfReader(str(target)).pages[0].extract_text()
    assert '原文源' in text and 'KEEP' in text and report['embedded_font']
    assert source.read_bytes() == original
    with pikepdf.Pdf.open(source) as a, pikepdf.Pdf.open(target) as b:
        before, after = ([str(op.operator) for op in pikepdf.parse_content_stream(pdf.pages[0])] for pdf in (a, b))
        assert before.count('re') == after.count('re') and before.count('Do') == after.count('Do')
        assert before.count('rg') == after.count('rg')


def test_tj_fragment_preserves_other_string_bytes_and_spacing(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'target.pdf'
    row = _subset_tj_sample(source, fragments=True)[0]
    report = replace_isolated_text_with_font(
        source, target, page=1, operator_index=row['operator_index'], array_index=row['array_index'],
        expected_text=row['text'], replacement='新', font_path=_font())
    with pikepdf.Pdf.open(target) as pdf:
        arrays = [list(op.operands[0]) for op in pikepdf.parse_content_stream(pdf.pages[0]) if str(op.operator) == 'TJ']
        assert any(any(not isinstance(item, pikepdf.String) and float(item) == -25 for item in array) for array in arrays)
    text = PdfReader(str(target)).pages[0].extract_text()
    assert '新' in text and '文' in text and 'KEEP' in text and report['embedded_font']
