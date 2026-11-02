"""Focused regression cases; no GUI or signing credentials needed."""
from pdf_studio.signature_evidence import NativeView, public_key_details, PRIVATE_KEY_NOTE
from cryptography.hazmat.primitives.asymmetric import rsa, ec


def test_rsa_public_parameters_and_pem_no_private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    evidence = public_key_details(key)
    assert evidence['parameters']['e_decimal'] == 65537
    assert evidence['parameters']['n_hex'] == format(key.public_numbers().n, 'x')
    assert 'BEGIN PUBLIC KEY' in evidence['spki_pem']
    assert 'BEGIN PRIVATE KEY' not in evidence['spki_pem']
    assert evidence['private_key'] == PRIVATE_KEY_NOTE


def test_ec_public_parameters():
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    result = public_key_details(key)
    assert result['parameters']['curve'] == 'secp256r1'
    assert result['parameters']['x_hex'] == format(key.public_numbers().x, 'x')


def test_preview_limits_are_explicit():
    view = NativeView()
    view.nodes = 50000
    assert view.convert({'deep': 'data'}) == {'not_expanded': 'structure_limit'}
    assert view.limits


def test_no_signature_and_unreadable_are_different(tmp_path, monkeypatch):
    from pdf_studio import signature_evidence as module
    source = tmp_path / 'not-used-as-pdf.pdf'
    source.write_bytes(b'preserved source')
    class EmptyReader:
        root = {}
        def __init__(self, stream):
            pass
    monkeypatch.setattr(module, 'PdfFileReader', EmptyReader)
    monkeypatch.setattr(module, 'enumerate_sig_fields', lambda _: [])
    # 空读取器的扫描结果也显式模拟，避免测试替身缺少 xref API 导致误报。
    monkeypatch.setattr(module, 'discover_signature_objects', lambda *a, **k:
        {'candidates': [], 'errors': [], 'limits': []})
    original = source.read_bytes()
    result = module.collect_signature_evidence(source, {'signatures': []})
    assert result['signatures'] == [] and not result['errors']
    def fail(stream):
        raise ValueError('bad structure')
    monkeypatch.setattr(module, 'PdfFileReader', fail)
    result = module.collect_signature_evidence(source, {'signatures': []})
    assert result['errors'] and source.read_bytes() == original


def test_timestamp_and_identity_not_assumed_without_roots():
    from types import SimpleNamespace
    from pdf_studio.signature_evidence import validation_details
    status = SimpleNamespace(trusted=True, revocation_details=None, validation_path=None)
    details = validation_details(status, False)
    assert details['signature']['trusted_under_explicit_roots'] is None
    assert details['signature_timestamp']['state'] == 'not_available_or_not_evaluated'


def test_broken_field_enumeration_still_runs_object_scan(tmp_path, monkeypatch):
    from pdf_studio import signature_evidence as module
    source = tmp_path / 'synthetic-reader.pdf'
    source.write_bytes(b'synthetic preserved input')
    class Reader:
        root = {}
        def __init__(self, stream):
            pass
    seen = []
    def broken_fields(reader):
        raise ValueError('synthetic field tree error')
    def discover(*args, **kwargs):
        seen.append(True)
        return {'candidates': [], 'errors': [], 'limits': []}
    monkeypatch.setattr(module, 'PdfFileReader', Reader)
    monkeypatch.setattr(module, 'enumerate_sig_fields', broken_fields)
    monkeypatch.setattr(module, 'discover_signature_objects', discover)
    result = module.collect_signature_evidence(source, {'signatures': []})
    assert seen and result['errors']
    assert source.read_bytes() == b'synthetic preserved input'


def test_unlinked_metadata_does_not_fill_full_verification_flags():
    from io import BytesIO
    from pyhanko.pdf_utils import generic
    from pdf_studio.signature_evidence import _extract_unlinked
    # 错误 CMS 仍应保留对象与关联状态，不得把错误当成“没有签名”。
    dictionary = generic.DictionaryObject({generic.pdf_name('/Type'): generic.pdf_name('/Sig')})
    candidate = {'dictionary': dictionary, 'location': '17 0 R',
                 'container_reference': '17 0 R', 'direct_path': None}
    entry = _extract_unlinked(candidate, 1, b'not-CMS', BytesIO(b'input'))
    assert entry['errors'] and entry['discovery']['field_tree_linked'] is False
    assert entry['audit']['intact'] is None and entry['audit']['signature_valid'] is None
