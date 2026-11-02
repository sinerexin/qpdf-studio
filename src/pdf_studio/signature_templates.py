"""从 A 建立 B 的参考模板；参考数据与 B 实际生成值分开保存。"""
from .automatic_signing import reference_defaults
from .certificate_names import build_subject, name_attributes


def _present(value):
    return value is not None and not (isinstance(value, str) and not str(value).strip())


def _original_certificate_claims(certificate):
    """只转发 A 证书里能原样使用的序列号和有效期。指纹、公钥和证书签名不进入生成参数。"""
    claims = {}
    serial = certificate.get('serial_decimal')
    if not _present(serial):
        serial = certificate.get('serial_number')
    if _present(serial):
        claims['serial_number'] = serial
    before, after = certificate.get('not_before'), certificate.get('not_after')
    if _present(before) and _present(after):
        claims['not_before'] = before
        claims['not_after'] = after
    return claims


def build_reference_template(entry, audit):
    """不改动 A，不生成密钥或文件。新证书沿用 A 的原名，不再追加程序标识。"""
    if not entry:
        return None
    certificate = entry.get('signer_certificate') or {}
    attrs = certificate.get('subject_attributes') or name_attributes(certificate.get('subject', ''))
    common_names = [item['value'] for item in attrs if item['oid'] == '2.5.4.3']
    label = common_names[-1] if common_names else certificate.get('subject') or 'B 文档签署者'
    subject = build_subject(label, attrs).rfc4514_string()
    preview_certificate = dict(certificate, subject=subject, subject_attributes=name_attributes(subject))
    preview_entry = dict(entry, signer_certificate=preview_certificate, field='尚未生成 B 签名')
    preview_entry.pop('discovery', None)
    return {'label': label, 'subject_attributes': attrs,
            **_original_certificate_claims(certificate),
            'parameters': reference_defaults(entry, audit),
            'reference_entry': entry, 'reference_audit': audit,
            'preview_entry': preview_entry, 'preview_audit': dict(audit or {}),
            'note': 'A 参考模板 · B 尚未生成。名称沿用 A 的原名；证书、公钥、签名等实际值生成后更新。'}


def template_section(template, index):
    """完整 A 信息可在模板详情查看；不把 A 的公钥或签名当成 B 已生成的结果。"""
    entry, audit = template['reference_entry'], template['reference_audit']
    certificate = entry.get('signer_certificate') or {}
    data = {
        0: {'签名信息模板': audit, '原签名字段': entry.get('field')},
        1: {'证书信息模板': certificate, '内嵌证书模板': entry.get('embedded_certificates', [])},
        2: {'公钥信息模板': certificate.get('public_key'), '持有人': certificate.get('subject')},
        5: {'完整 A 参考信息': entry},
    }.get(index, {'完整 A 参考信息': entry})
    return {'显示来源': template['note'], 'B 待生成名称': template['label'], '参考信息': data}
