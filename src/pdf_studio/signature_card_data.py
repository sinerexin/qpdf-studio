"""Small factual summaries of existing evidence; full values stay in the report."""

SECTIONS = ((0, '签名信息'), (1, '证书信息'), (2, '公钥信息'), (5, '原始信息'))


def summary_rows(index, entry, audit, generated=None):
    certificate = entry.get('signer_certificate') or {}
    pending = (generated or {}).get('public_certificate') or {}
    using_pending = not certificate and bool(pending) and index in (1, 2)
    cert = pending if using_pending else certificate
    fallback = audit.get('certificate') or {}
    key = cert.get('public_key') or {}
    note = ('新生成凭据 · 尚未确认签入当前 PDF' if using_pending else
            '另有新生成凭据；当前显示 PDF 内原有数据' if generated and index in (1, 2) else '')
    # 卡片也要说明数据来自未关联对象，不能只在隐藏详情中标注。
    if entry.get('discovery'):
        note = '对象扫描发现 · 未关联当前签名字段树；识别到证书不代表验证通过。' + (' ' + note if note else '')
    if index == 0:
        rows = [('签名字段', '未关联字段树' if entry.get('discovery') else entry.get('field')),
                ('签署者', certificate.get('subject') or fallback.get('claimed_subject')),
                ('签名算法', audit.get('signature_mechanism')),
                ('摘要算法', audit.get('digest_algorithm'))]
    elif index == 1:
        begin, end = cert.get('not_before'), cert.get('not_after')
        dates = audit.get('certificate_validity') or {}
        begin, end = begin or dates.get('not_before'), end or dates.get('not_after')
        rows = [('持有人', cert.get('subject') or fallback.get('claimed_subject')),
                ('签发者', cert.get('issuer') or fallback.get('issuer')),
                ('有效期', f'{begin} 至 {end}' if begin and end else None),
                ('序列号', cert.get('serial_decimal') or fallback.get('serial_number'))]
    elif index == 2:
        parameters = key.get('parameters') or {}
        # 公钥所属证书的名称也按 A/B 共用的名称分段规则显示。
        rows = [('持有人', cert.get('subject') or fallback.get('claimed_subject')),
                ('公钥类型', key.get('type')),
                ('位数', key.get('bits')),
                ('RSA 指数', parameters.get('e_decimal')),
                ('SPKI SHA256', key.get('spki_sha256'))]
    else:
        rows = [('对象引用', entry.get('signature_object_reference')),
                ('SubFilter', audit.get('signature_subfilter')),
                ('ByteRange', entry.get('byte_range')),
                ('CMS 字节数', entry.get('cms_length'))]
    return rows, note


def display_value(value):
    if value is None:
        return '尚未读取 / 未提供'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(str(v) for v in value) + ']'
    return str(value)


def revocation_description(audit):
    return {
        'offline_only_no_online_revocation': '仅离线 · 未联网检查吊销',
        'strict_policy_requested': '严格吊销策略 · 详见验证依据',
        'online_fetch_opted_in_soft_fail': '联网软失败策略 · 详见验证依据',
    }.get(audit.get('revocation_assessment'), '未判断 / 详见验证依据')


def default_signature_index(entries, previous_field=None):
    """A/B 共用选择规则：保留用户选择，否则优先最新字段签名，再看扫描候选。"""
    if previous_field is not None:
        matching = next((i for i, row in enumerate(entries) if row.get('field') == previous_field), None)
        if matching is not None:
            return matching
    linked = [i for i, row in enumerate(entries) if not row.get('discovery')]
    return linked[-1] if linked else len(entries) - 1


def report_source_details(report):
    """只展示后台读取实际 PDF 时记录的来源，不由界面名称或参考模板生成摘要。"""
    evidence = (report or {}).get('full_evidence') or {}
    return {'实际读取文件': evidence.get('source_path'),
            '文件大小_字节': evidence.get('source_size'),
            '实际文件_SHA256': evidence.get('source_sha256'),
            '读取范围': '当前侧实际 PDF；界面名称分段和参考模板不参与验签'}
