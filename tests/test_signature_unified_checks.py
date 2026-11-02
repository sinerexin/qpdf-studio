"""统一状态与原始位置映射的回归用例；不使用真实 PDF、证书或私钥。"""
import io
from types import SimpleNamespace

from pyhanko.pdf_utils import generic

from pdf_studio.signature_presentation import STATUS_TITLES, status_cells
from pdf_studio.signature_structure import inspect_signature_structure


def _fixture(*, mismatch=False, repeated=False, literal=False, encoded_name=False):
    # 模拟当前 xref 对应的对象；前面可以有同号历史对象，绝不按首次正则命中选取。
    prefix = b'17 0 obj << /Contents <ffff> >> endobj\n' if repeated else b'%PDF-1.4\n'
    string = b'(ab\\(c\\))' if literal else b'<616263>'
    key = b'/Con#74ents' if encoded_name else b'/Contents'
    template = b'17 0 obj << /Type /Sig /ByteRange [0 %010d %010d %010d] ' + key + b' ' + string + b' >> endobj\n'
    stub = prefix + template % (0, 0, 0)
    start = stub.index(key, len(prefix)) + len(key) + 1
    end = start + len(string)
    gap_start = start + 1 if mismatch else start
    data = prefix + template % (gap_start, end, len(stub) - end)
    reader = SimpleNamespace(encrypted=False)
    ref = generic.Reference(17, 0, pdf=reader)
    reader.xrefs = {ref: len(prefix)}
    source = io.BytesIO(data)
    source.seek(len(prefix) + len(b'17 0 obj '))
    dictionary = generic.read_object(source, ref)
    return reader, source, dictionary, generic.IndirectObject(17, 0, reader), start, end


def test_same_five_titles_for_every_discovery_path():
    # 无 A/B 参数：相同证据必须给出相同结果，与文件角色无关。
    evidence = {'signatures': [{}]}
    for unlinked in (False, True):
        audit = {'unlinked_object': unlinked, 'supplemental_digest': {'digest_matches': True}}
        a = status_cells({'count': 1}, evidence, audit)
        b = status_cells({'count': 1}, evidence, dict(audit))
        assert a == b and tuple(row[0] for row in a) == STATUS_TITLES


def test_trust_does_not_change_digest_math_or_structure():
    evidence = {'signatures': [{}]}
    audit = {'intact': True, 'signature_valid': True, 'signer_trusted': False,
             'signature_structure': {'state': 'normal'}}
    cells = status_cells({'count': 1, 'trust_roots_supplied': 1}, evidence, audit)
    assert [row[1] for row in cells] == ['完整', '正常', '一致', '有效', '未建立']


def test_partial_read_is_not_crypto_failure():
    cells = status_cells({'count': 1, 'validation_error': 'validation unavailable'},
                         {'errors': ['synthetic read failure'], 'signatures': [{}]}, {})
    assert [row[1] for row in cells] == ['部分读取', '无法确定', '无法计算', '未验证', '未检查']


def test_structure_failure_does_not_replace_digest_evidence():
    audit = {'signature_structure': {'state': 'abnormal'},
             'supplemental_digest': {'digest_matches': False}}
    cells = status_cells({'count': 1}, {'signatures': [{}]}, audit)
    assert cells[1][1] == '异常' and cells[2][1] == '不一致' and cells[3][1] == '未验证'


def test_current_xref_exact_position_and_cursor_restore():
    for options in ({}, {'repeated': True}, {'literal': True}, {'encoded_name': True}):
        reader, source, dictionary, ref, start, end = _fixture(**options)
        source.seek(3)
        result = inspect_signature_structure(reader, source, dictionary, ref)
        assert result['state'] == 'normal'
        assert result['contents_span_half_open'] == [start, end]
        assert result['contents_gap_matches'] is True and source.tell() == 3


def test_gap_mismatch_reports_positions_without_claiming_cause():
    reader, source, dictionary, ref, start, end = _fixture(mismatch=True)
    result = inspect_signature_structure(reader, source, dictionary, ref)
    assert result['state'] == 'abnormal' and result['range_valid'] is True
    assert result['contents_span_half_open'] == [start, end]
    assert result['declared_gap_half_open'] == [start + 1, end]
    assert result['contents_gap_matches'] is False


def test_object_stream_and_nested_dictionary_positions_remain_unknown():
    reader, source, dictionary, ref, *_ = _fixture()
    reader.xrefs[ref.reference] = object()  # 非普通偏移，模拟对象流引用。
    assert inspect_signature_structure(reader, source, dictionary, ref)['state'] == 'unknown'
    assert inspect_signature_structure(reader, source, dictionary)['state'] == 'unknown'


def test_invalid_ranges_and_duplicate_keys_do_not_pass():
    reader, source, dictionary, ref, *_ = _fixture()
    dictionary['/ByteRange'] = generic.ArrayObject([generic.NumberObject(v) for v in [0, -1, 20, 10]])
    result = inspect_signature_structure(reader, source, dictionary, ref)
    assert result['state'] == 'abnormal' and result['range_valid'] is False
    reader, source, dictionary, ref, *_ = _fixture()
    raw = source.getvalue().replace(b' /Type /Sig', b' /Type /Sig /Type /Sig')
    result = inspect_signature_structure(reader, io.BytesIO(raw), dictionary, ref)
    assert result['state'] == 'unknown' and result['contents_gap_matches'] is None
