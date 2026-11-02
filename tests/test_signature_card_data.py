"""Factual card projection only; no PDF writes, GUI windows or key generation."""
from pdf_studio.signature_card_data import SECTIONS, display_value, summary_rows


def test_four_fixed_information_sections():
    assert [index for index, _ in SECTIONS] == [0, 1, 2, 5]
    assert [title for _, title in SECTIONS] == ['签名信息', '证书信息', '公钥信息', '原始信息']


def test_existing_pdf_certificate_is_not_replaced_by_pending_credentials():
    entry = {'signer_certificate': {'subject': 'PDF certificate', 'serial_decimal': str(2 ** 159)}}
    pending = {'public_certificate': {'subject': 'New credential'}}
    rows, note = summary_rows(1, entry, {}, pending)
    assert dict(rows)['持有人'] == 'PDF certificate'
    assert dict(rows)['序列号'] == str(2 ** 159)
    assert '当前显示 PDF' in note


def test_unsigned_pdf_keeps_new_credentials_marked_as_pending():
    pending = {'public_certificate': {'subject': 'New credential', 'public_key': {'bits': 2048}}}
    rows, note = summary_rows(1, {}, {}, pending)
    assert dict(rows)['持有人'] == 'New credential'
    assert '尚未确认签入' in note
    rows, _ = summary_rows(0, {}, {}, pending)
    assert dict(rows)['签署者'] is None


def test_missing_values_are_not_fabricated():
    for section, _ in SECTIONS:
        rows, _ = summary_rows(section, {}, {})
        assert all(value is None for _, value in rows)
    assert display_value(None) == '尚未读取 / 未提供'
    assert display_value(0) == '0'
    assert display_value([0, 30, 60, 10]) == '[0, 30, 60, 10]'


def test_unlinked_certificate_remains_visible_with_clear_origin():
    # 显示发现的真实字段，同时说明没有关联字段树，不把它抹成空白。
    entry = {'discovery': {'field_tree_linked': False},
             'signer_certificate': {'subject': 'Synthetic certificate', 'public_key': {'bits': 2048}}}
    rows, note = summary_rows(1, entry, {})
    assert dict(rows)['持有人'] == 'Synthetic certificate' and '未关联' in note
    rows, _ = summary_rows(0, entry, {})
    assert dict(rows)['签名字段'] == '未关联字段树'


def test_public_key_card_includes_holder_without_dropping_fingerprint():
    # 公钥卡片五行：名称与算法参数同时保留，不因增行丢掉最后的 SPKI 指纹。
    entry = {'signer_certificate': {'subject': 'CN=Demo@N1',
        'public_key': {'type': 'RSAPublicKey', 'bits': 2048, 'spki_sha256': 'synthetic-fingerprint'}}}
    rows, _ = summary_rows(2, entry, {})
    assert len(rows) == 5 and dict(rows)['持有人'] == 'CN=Demo@N1'
    assert dict(rows)['SPKI SHA256'] == 'synthetic-fingerprint'
