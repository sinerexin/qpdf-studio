"""文件句柄泄漏回归用例；只用临时虚构字节，不创建密钥或签署 PDF。"""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyhanko.pdf_utils import generic

from pdf_studio.automatic_signing import generation_reference
from pdf_studio.signature_templates import build_reference_template
from pdf_studio.signature_values import detached_values


def attach_reader(value, stream):
    # 模拟真实 pyHanko 标量：值本身是数字/字符串，但 container_ref 关联打开的文件。
    value.container_ref = SimpleNamespace(pdf=SimpleNamespace(stream=stream))
    return value


def test_pdf_scalar_values_do_not_copy_reader_attributes(tmp_path):
    path = tmp_path / 'synthetic.txt'
    path.write_bytes(b'synthetic bytes only')
    with path.open('rb') as stream:
        number = attach_reader(generic.NumberObject(123), stream)
        name = attach_reader(generic.TextStringObject('CN=Example@OLD'), stream)
        decimal = attach_reader(generic.FloatObject('1.250'), stream)
        with pytest.raises(TypeError, match='BufferedReader'):
            deepcopy({'range': [number]})
        result = detached_values({'range': [number], 'name': name, 'decimal': decimal,
                                  'valid': False, 'unknown': None})
        assert result['range'] == [123] and type(result['range'][0]) is int
        assert result['name'] == 'CN=Example@OLD' and type(result['name']) is str
        assert result['decimal'] == Decimal('1.250') and type(result['decimal']) is Decimal
        assert result['valid'] is False and result['unknown'] is None
        assert deepcopy(result) == result
        assert not stream.closed and stream.tell() == 0
        assert number.container_ref.pdf.stream is stream


def test_generation_reference_accepts_pdf_numbers_with_reader_refs(tmp_path):
    path = tmp_path / 'synthetic.txt'
    path.write_bytes(b'not a real PDF')
    with path.open('rb') as stream:
        entry = {'field': 'orphan signature', 'byte_range': [attach_reader(generic.NumberObject(0), stream)],
                 'signer_certificate': {'subject': 'CN=Example@OLD',
                    'public_key': {'type': 'RSAPublicKey', 'bits': 2048}},
                 'audit': {'digest_algorithm': 'sha256', 'intact': False}}
        reference = generation_reference({'full_evidence': {'signatures': [entry]}})
        assert reference['entry']['byte_range'] == [0]
        assert type(reference['entry']['byte_range'][0]) is int
        assert reference['audit']['intact'] is False
        reference['entry']['signer_certificate']['subject'] = 'CN=changed copy'
        assert entry['signer_certificate']['subject'] == 'CN=Example@OLD'


def test_whole_template_can_be_detached_without_copying_file_handle(tmp_path):
    path = tmp_path / 'synthetic.txt'
    path.write_bytes(b'synthetic')
    with path.open('rb') as stream:
        entry = {'field': 'A', 'byte_range': [attach_reader(generic.NumberObject(42), stream)],
                 'signer_certificate': {'subject': 'CN=Example@OLD'}}
        template = build_reference_template(entry, {'intact': False})
        snapshot = detached_values(template)
        assert snapshot['reference_entry']['byte_range'] == [42]
        assert type(snapshot['reference_entry']['byte_range'][0]) is int
        assert snapshot['label'] == template['label'] == 'Example@OLD'
        assert 'identifier' not in snapshot
        assert snapshot['preview_audit']['intact'] is False
        assert deepcopy(snapshot) == snapshot


def test_runtime_object_is_labelled_not_read_or_pickled():
    class RuntimeOnly:
        def __init__(self):
            self.read = Mock(side_effect=AssertionError('must not read'))

        def __deepcopy__(self, memo):
            raise AssertionError('must not deepcopy')

    value = RuntimeOnly()
    result = detached_values(value)
    assert result['runtime_object_not_copied'].endswith('RuntimeOnly')
    value.read.assert_not_called()


def test_pdf_booleans_null_and_bytes_keep_their_value_types():
    assert detached_values(generic.BooleanObject(False)) is False
    assert detached_values(generic.BooleanObject(True)) is True
    assert detached_values(generic.NullObject()) is None
    assert type(detached_values(generic.ByteStringObject(b'example'))) is bytes
