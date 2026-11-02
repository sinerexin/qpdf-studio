"""No GUI windows or real credentials; actual operations are replaced with fakes."""
from pdf_studio import automatic_signing as module


def test_no_a_signature_uses_adjustable_defaults():
    result = module.reference_defaults()
    assert result['algorithm'] == 'RSA-3072'
    assert result['digest'] == 'sha256'
    assert '不影响' in result['note']


def test_a_rsa2048_and_digest_used_without_copying_identity():
    entry = {'field': 'SignatureA', 'signer_certificate': {'subject': 'Original signer',
             'public_key': {'type': 'RSAPublicKey', 'bits': 2048}}}
    result = module.reference_defaults(entry, {'digest_algorithm': 'sha384'})
    assert result['algorithm'] == 'RSA-2048' and result['digest'] == 'sha384'
    assert 'Original signer' not in str(result)


def test_ec_curve_reference():
    entry = {'signer_certificate': {'public_key': {'type': 'ECPublicKey',
             'parameters': {'curve': 'secp384r1'}}}}
    assert module.reference_defaults(entry)['algorithm'] == 'ECDSA-P384'


def test_read_completion_distinguishes_absent_partial_and_error():
    assert '未发现已有' in module.read_completion_text('A', {'count': 0})
    assert '不完整' in module.read_completion_text('B', {'count': 0, 'validation_error': 'parser error'})
    assert '失败' in module.read_completion_text('B', error='bad file')
    assert '数量未确定' in module.read_completion_text('A', {})
    assert '执行中' not in module.read_completion_text('A', {'count': 1})


def test_preflight_does_not_require_a_generated_password(tmp_path):
    source = tmp_path / 'B.pdf'
    source.write_bytes(b'synthetic')
    module.validate_generation_request(source, tmp_path / 'signed.pdf', tmp_path / 'credentials',
        label='B signer', algorithm='RSA-3072', days_valid=365)
