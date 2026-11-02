"""仅使用虚构名称，测试 A/B 共用的分段表示；不生成密钥或修改 PDF。"""
from cryptography import x509
from cryptography.x509.oid import NameOID

from pdf_studio.certificate_names import (
    build_subject, display_name_fields, format_name, name_attributes, segment_name,
)


def test_common_name_segments_keep_original_values():
    raw = 'CN=Demo@虚构测试机构@N123,O=演示机构,C=CN'
    text = format_name(raw)
    assert '名称 1：Demo' in text and '名称 2：虚构测试机构' in text
    assert '标识：N123' in text and '机构（O）' in text and '\n' in text
    assert segment_name('甲@@末段') == [('名称 1', '甲'), ('名称 2', ''), ('标识', '末段')]


def test_escaped_comma_and_multivalued_rdn_are_not_split_incorrectly():
    raw = r'CN=Alpha\, Beta@N1+OU=Lab,O=Example,C=CN'
    attrs = name_attributes(raw)
    cn = next(item for item in attrs if item['oid'] == '2.5.4.3')
    ou = next(item for item in attrs if item['oid'] == '2.5.4.11')
    assert cn['value'] == 'Alpha, Beta@N1' and cn['rdn'] == ou['rdn']
    assert 'Alpha, Beta' in format_name(raw)


def test_detail_view_preserves_raw_string_and_non_name_values():
    raw = {'subject': 'CN=Demo@N1', 'public_key': {'type': 'RSAPublicKey'}, 'signature_value_hex': 'abcdef'}
    view = display_name_fields(raw)
    assert view['subject']['完整原值'] == raw['subject']
    assert '标识：N1' in view['subject']['分段显示']
    assert view['public_key'] == raw['public_key']
    assert raw['subject'] == 'CN=Demo@N1'


def test_new_subject_preserves_other_attributes_and_rdn_grouping():
    attrs = name_attributes(r'CN=Demo@OLD+OU=Lab,O=Example,C=CN')
    subject = build_subject('Demo@QPDF-NEW', attrs)
    assert subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == 'Demo@QPDF-NEW'
    assert subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == 'Example'
    assert len(subject.rdns) == 3
    assert isinstance(subject, x509.Name)


def test_plain_name_fallback_has_no_new_rejection_rule():
    assert '测试名字' in format_name('测试名字')
    assert build_subject('测试名字').get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == '测试名字'
