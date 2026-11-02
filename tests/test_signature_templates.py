"""用虚构 A 信息构造参考模板，检查没有把 A 的密码、密钥或验签结果变成 B 实际值。"""
from copy import deepcopy

from pdf_studio.signature_templates import build_reference_template, template_section


def test_template_keeps_the_original_name_without_a_program_identifier():
    entry = {'field': 'DemoSignature', 'signer_certificate': {
        'subject': 'CN=Demo@示例机构@OLD,O=Example,C=CN', 'issuer': 'CN=Example Issuer',
        'serial_decimal': '1234', 'public_key': {'type': 'RSAPublicKey', 'bits': 2048, 'spki_sha256': 'source-only'}}}
    audit = {'digest_algorithm': 'sha384', 'signature_valid': True}
    before = deepcopy(entry)
    template = build_reference_template(entry, audit)
    assert template['label'] == 'Demo@示例机构@OLD'
    assert 'QPDF-' not in template['label'] and 'identifier' not in template
    assert template['parameters']['algorithm'] == 'RSA-2048'
    assert template['parameters']['digest'] == 'sha384'
    assert entry == before
    assert template['reference_entry'] is entry and 'A 参考模板' in template['note']
    assert template['preview_entry']['field'] == '尚未生成 B 签名'


def test_template_keeps_complete_a_reference_with_origin_label():
    entry = {'field': 'DemoSignature', 'signer_certificate': {'subject': 'CN=Demo', 'serial_decimal': '42'},
             'cms_der_base64': 'synthetic-not-a-real-container'}
    template = build_reference_template(entry, {})
    section = template_section(template, 5)
    assert section['参考信息']['完整 A 参考信息'] == entry
    assert '尚未生成' in section['显示来源']
    assert build_reference_template({}, {}) is None


def test_two_templates_keep_the_same_original_name():
    entry = {'signer_certificate': {'subject': 'CN=Demo@OLD'}}
    first, second = build_reference_template(entry, {}), build_reference_template(entry, {})
    assert first['label'] == second['label'] == 'Demo@OLD'


def test_raw_name_without_dn_syntax_is_still_used_as_template():
    template = build_reference_template({'signer_certificate': {'subject': '演示名称'}}, {})
    assert template['label'] == '演示名称'
