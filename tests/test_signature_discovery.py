"""仅使用合成对象和模拟读取器；不包含用户 PDF、证书或正文。"""
import hashlib
import io
from types import SimpleNamespace

import pytest
from asn1crypto import cms, x509
from pyhanko.pdf_utils import generic

from pdf_studio import signature_discovery as module
from pdf_studio.signature_presentation import status_cells


def _dictionary(**values):
    # 测试字典使用真实解析库对象，避免字符串字典掩盖类型判断错误。
    return generic.DictionaryObject({generic.pdf_name('/' + key): value for key, value in values.items()})


def _reader(objects):
    class Xrefs:
        total_revisions = 1
        def explicit_refs_in_revision(self, revision):
            return {generic.Reference(i, pdf=reader) for i in objects}
        def __getitem__(self, ref):
            return ref.idnum * 10
    reader = SimpleNamespace(xrefs=Xrefs(), get_object=lambda ref: objects[ref.idnum])
    return reader


def _signature():
    return _dictionary(Type=generic.pdf_name('/Sig'),
                       ByteRange=generic.ArrayObject([generic.NumberObject(v) for v in [0, 3, 6, 3]]),
                       Contents=generic.ByteStringObject(b'synthetic CMS stand-in'))


def _signed_data(digest):
    # 容器仅为摘要对照用例，伪造的签名字节不能当成数学签名通过。
    return cms.SignedData({'version': 'v1', 'digest_algorithms': [{'algorithm': 'sha256'}],
        'encap_content_info': {'content_type': 'data'},
        'signer_infos': [{'version': 'v1', 'sid': {'issuer_and_serial_number': {
            'issuer': x509.Name.build({'common_name': 'Synthetic test'}), 'serial_number': 1}},
            'digest_algorithm': {'algorithm': 'sha256'},
            'signed_attrs': [{'type': 'message_digest', 'values': [digest]}],
            'signature_algorithm': {'algorithm': 'rsassa_pkcs1v15'}, 'signature': b'not-a-real-signature'}]})


def test_scan_finds_signature_outside_empty_field_tree():
    reader = _reader({17: _signature(), 18: _dictionary(Type=generic.pdf_name('/Page'))})
    result = module.discover_signature_objects(reader)
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['location'] == '17 0 R'
    assert result['objects_scanned'] == 2 and not result['errors']


def test_scan_deduplicates_known_field_signature_by_reference():
    result = module.discover_signature_objects(_reader({17: _signature()}), {(17, 0)})
    assert result['candidates'] == []


def test_direct_signature_location_is_not_claimed_as_own_xref():
    sig = _signature()
    reader = _reader({20: _dictionary(V=sig)})
    result = module.discover_signature_objects(reader)
    candidate = result['candidates'][0]
    assert candidate['container_reference'] == '20 0 R'
    assert candidate['direct_path'] == '/V'
    assert module.discover_signature_objects(reader, known_dictionary_ids={id(sig)})['candidates'] == []


def test_candidate_budget_is_reported_not_unsigned():
    result = module.discover_signature_objects(_reader({17: _signature()}), max_candidates=0)
    assert not result['candidates'] and result['limits']


def test_broken_object_does_not_hide_other_signature():
    reader = _reader({16: None, 17: _signature()})
    get = reader.get_object
    def read(ref):
        if ref.idnum == 16:
            raise ValueError('synthetic damaged object')
        return get(ref)
    reader.get_object = read
    result = module.discover_signature_objects(reader)
    assert result['errors'] and len(result['candidates']) == 1


@pytest.mark.parametrize('expected_match', [True, False])
def test_digest_comparison_preserves_position_without_validating_signature(expected_match):
    stream = io.BytesIO(b'abcGAPdefTAIL')
    stream.seek(2)
    digest = hashlib.sha256(b'abcdef' if expected_match else b'wrong bytes').digest()
    result = module.detached_digest_evidence(stream, [0, 3, 6, 3], _signed_data(digest), '/adbe.pkcs7.detached')
    assert result['digest_matches'] is expected_match
    assert result['bytes_after_declared_range'] == 4 and stream.tell() == 2
    assert 'signature_valid' not in result


@pytest.mark.parametrize('ranges', [[0, 99, 100, 1], [0, 4, 3, 2], [0, -1, 3, 2], [0, 3.5, 6, 2], [0, 2, 4]])
def test_bad_byte_range_is_unknown_not_pass(ranges):
    result = module.detached_digest_evidence(io.BytesIO(b'abcdef'), ranges,
        _signed_data(b'fake-digest'), '/adbe.pkcs7.detached')
    assert result['digest_matches'] is None and result['error']


def test_legacy_subfilter_is_not_misinterpreted_as_detached():
    result = module.detached_digest_evidence(io.BytesIO(b'abcdef'), [0, 2, 4, 2],
        _signed_data(b'fake-digest'), '/adbe.pkcs7.sha1')
    assert result['digest_matches'] is None


def test_unlinked_digest_match_is_not_displayed_as_verified_signature():
    audit = {'unlinked_object': True, 'supplemental_digest': {'digest_matches': True}}
    cells = status_cells({'count': 0}, {'signatures': [{}]}, audit)
    # 新版两条识别路径共用五项标题；摘要匹配仍不能替代数学验签。
    assert cells[2][:2] == ('字节摘要', '一致')
    assert cells[3][:2] == ('数学签名', '未验证')
    assert all(value != '通过' for _, value, _ in cells)


def test_partial_scan_cannot_display_unsigned():
    cells = status_cells({'count': 0}, {'limits': ['synthetic incomplete scan']}, {})
    assert cells[-1][1] != '无数字签名'
