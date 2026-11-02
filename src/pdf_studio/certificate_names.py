"""A/B 共用的名称分段显示；展示保留原值，主体构建只用于用户确认后的新证书。"""
import re
import warnings

from cryptography import x509
from cryptography.x509.oid import NameOID, ObjectIdentifier

NAME_FIELDS = {'签署者', '持有人', '签发者', 'subject', 'issuer', 'claimed_subject',
               'common_name', 'organization_name', 'organizational_unit_name',
               'locality_name', 'state_or_province_name', 'country_name',
               '签署者名称', '新证书显示名称', '通用名称', 'B 待生成名称'}
ATTRIBUTE_LABELS = {
    '2.5.4.3': '通用名称（CN）', '2.5.4.10': '机构（O）', '2.5.4.11': '部门（OU）',
    '2.5.4.6': '国家（C）', '2.5.4.7': '城市（L）', '2.5.4.8': '地区（ST）',
    '2.5.4.5': '主体序列标识（serialNumber）', '1.2.840.113549.1.9.1': '邮箱',
}
_SHORT_OIDS = {'CN': '2.5.4.3', 'O': '2.5.4.10', 'OU': '2.5.4.11', 'C': '2.5.4.6',
               'L': '2.5.4.7', 'ST': '2.5.4.8', 'SERIALNUMBER': '2.5.4.5',
               'EMAILADDRESS': '1.2.840.113549.1.9.1', 'E': '1.2.840.113549.1.9.1'}


def _split_dn(text, delimiter):
    """只拆分未转义的 DN 分隔符，不能用 str.split(',') 破坏机构名称。"""
    parts, current, escaped = [], [], False
    for char in text:
        if char == delimiter and not escaped:
            parts.append(''.join(current))
            current = []
        else:
            current.append(char)
        escaped = char == '\\' and not escaped
    return parts + [''.join(current)]


def _display_attributes(text):
    """兼容读取已有超长 CN；这里只做显示解析，不替用户截短原名。"""
    result = []
    try:
        for group, rdn in enumerate(reversed(_split_dn(text, ','))):
            for item in _split_dn(rdn, '+'):
                key, separator, value = item.partition('=')
                if not separator:
                    return []
                oid = _SHORT_OIDS.get(key.strip().upper(), key.strip())
                ObjectIdentifier(oid)
                # 十六进制转义可能由多个 UTF-8 字节组成，合并后再解码。
                data = bytearray()
                for match in re.finditer(r'\\([0-9a-fA-F]{2}|.)|([^\\])', value):
                    escaped, plain = match.groups()
                    data.extend(bytes.fromhex(escaped) if escaped and len(escaped) == 2
                                and all(c in '0123456789abcdefABCDEF' for c in escaped)
                                else (escaped or plain).encode('utf-8'))
                result.append({'oid': oid, 'value': data.decode('utf-8'), 'rdn': group})
    except (ValueError, TypeError, UnicodeError):
        return []
    return result


def name_attributes(value):
    """按真正的 DN/RDN 解析，正确保留转义逗号、多值 RDN 和重复属性。"""
    if isinstance(value, x509.Name):
        name = value
    else:
        try:
            name = x509.Name.from_rfc4514_string(str(value))
        except (ValueError, TypeError):
            return _display_attributes(str(value))
    return [{'oid': item.oid.dotted_string, 'value': item.value, 'rdn': group}
            for group, rdn in enumerate(name.rdns) for item in rdn]


def segment_name(value):
    """保留每一段和完整原值；最后一个 @ 后的值称为标识，不冒充证书序列号。"""
    text = str(value)
    parts = text.split('@')
    if len(parts) == 1:
        return [('名称', text)]
    return [(f'名称 {index + 1}', part) for index, part in enumerate(parts[:-1])] + [('标识', parts[-1])]


def format_name(value):
    """卡片统一使用多行纯文本；CN/O/OU 等分别显示，不截断成省略号。"""
    if value is None:
        return '尚未读取 / 未提供'
    attrs = name_attributes(value)
    if not attrs:
        return '\n'.join(f'{label}：{part}' for label, part in segment_name(value))
    lines = []
    for attr in reversed(attrs):
        title = ATTRIBUTE_LABELS.get(attr['oid'], '属性 ' + attr['oid'])
        # 邮箱中的 @ 是地址本身，不能误标成程序生成的主体标识。
        parts = [('值', attr['value'])] if attr['oid'] == '1.2.840.113549.1.9.1' else segment_name(attr['value'])
        lines.append(title)
        lines.extend(f'  {label}：{part}' for label, part in parts)
    return '\n'.join(lines)


def display_name_fields(value, field=''):
    """详情中同时给出分段视图与完整原值；导出的原始报告不经过此函数。"""
    if isinstance(value, str) and field in NAME_FIELDS:
        return {'分段显示': format_name(value), '完整原值': value}
    if isinstance(value, dict):
        return {key: display_name_fields(child, str(key)) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [display_name_fields(child, field) for child in value]
    return value


def build_subject(label, attributes=None):
    """沿用模板中的主体属性，仅把 CN 更新为本次确认的名称；不复制签发者签名。"""
    if not attributes:
        attributes = [{'oid': NameOID.COMMON_NAME.dotted_string, 'value': label.strip(), 'rdn': 0}]
    groups = {}
    has_common_name = False
    for attr in attributes:
        oid = ObjectIdentifier(attr['oid'])
        text = label.strip() if oid == NameOID.COMMON_NAME else attr['value']
        has_common_name = has_common_name or oid == NameOID.COMMON_NAME
        item = _subject_attribute(oid, text)
        groups.setdefault(attr.get('rdn', len(groups)), []).append(item)
    if not has_common_name:
        groups[max(groups, default=-1) + 1] = [_subject_attribute(NameOID.COMMON_NAME, label.strip())]
    return x509.Name([x509.RelativeDistinguishedName(items) for items in groups.values()])


def _subject_attribute(oid, text):
    """有 CN 和无 CN 的模板共用长名称兼容处理，不截短用户的名称。"""
    try:
        return x509.NameAttribute(oid, text)
    except ValueError as exc:
        if "Attribute's length must" not in str(exc):
            raise
        # 这是加密库提供的兼容构造接口；不改写 A，只用于本次新证书主体编码。
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            return x509.NameAttribute(oid, text, _validate=False)
