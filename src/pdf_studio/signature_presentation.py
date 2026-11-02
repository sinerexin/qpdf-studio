"""A/B 共用的签名状态投影；文件角色不会参与任何通过或失败判断。"""

STATUS_TITLES = ('信息读取', '签名结构', '字节摘要', '数学签名', '身份信任')


def reading_status(report, evidence, entry=None):
    # 读取失败、验证未完成是不同阶段；验证器报错不会抹掉已经读到的信息。
    if report is None:
        return ('信息读取', '尚未读取', 'neutral')
    if not evidence:
        return ('信息读取', '读取失败' if report.get('validation_error') else '尚未读取', 'neutral')
    incomplete = bool(evidence.get('errors') or evidence.get('limits')
                      or (entry or {}).get('errors') or (entry or {}).get('limits')
                      or (evidence.get('dss_validation_store') or {}).get('error')
                      or (evidence.get('dss_validation_store') or {}).get('errors')
                      or (evidence.get('dss_validation_store') or {}).get('limits'))
    if entry is None:
        incomplete = incomplete or any(row.get('errors') or row.get('limits')
                                      for row in evidence.get('signatures', []))
    return ('信息读取', '部分读取' if incomplete else '完整', 'pending' if incomplete else 'success')


def status_cells(report, evidence, audit, entry=None):
    """统一五项标签，未知保持未知，不把信任状态用于推断摘要或数学签名。"""
    reading = reading_status(report, evidence, entry)
    no_signatures = bool(report is not None and report.get('count') == 0
                         and not report.get('validation_error')
                         and reading[1] == '完整' and not evidence.get('signatures'))
    if no_signatures:
        return [reading] + [(name, '已检查 · 未发现签名', 'neutral') for name in STATUS_TITLES[1:]]
    structure = audit.get('signature_structure') or {}
    structure_state = structure.get('state')
    structural = ('签名结构', '正常', 'success') if structure_state == 'normal' else (
        ('签名结构', '异常', 'error') if structure_state == 'abnormal' else
        ('签名结构', '无法确定', 'neutral'))
    # 两条发现路径都优先使用同一算法计算的补充摘要，不改写验证器原始字段。
    supplemental = audit.get('supplemental_digest') or {}
    match = supplemental.get('digest_matches')
    if match is None and not audit.get('unlinked_object'):
        match = audit.get('intact')
    digest = ('字节摘要', '一致', 'success') if match is True else (
        ('字节摘要', '不一致', 'error') if match is False else
        ('字节摘要', '无法计算', 'neutral'))
    valid = audit.get('signature_valid')
    mathematical = ('数学签名', '有效', 'success') if valid is True else (
        ('数学签名', '无效', 'error') if valid is False else
        ('数学签名', '未验证', 'neutral'))
    # 没配置可信根与已经尝试但未建立信任分别保留；都不影响其他四项。
    roots = (report or {}).get('trust_roots_supplied', 0)
    trusted = audit.get('signer_trusted')
    if roots and trusted is True:
        trust = ('身份信任', '已建立', 'success')
    elif (audit.get('unlinked_object') or audit.get('verification_unavailable')
          or audit.get('verification_error') or (roots and trusted is None)):
        trust = ('身份信任', '未检查', 'neutral')
    elif audit:
        trust = ('身份信任', '未建立', 'pending')
    else:
        trust = ('身份信任', '未检查', 'neutral')
    return [reading, structural, digest, mathematical, trust]


def status_explanation(title, audit):
    """每项显示自己的依据，不把通用“未通过”当成所有问题的解释。"""
    if title == '信息读取':
        return '只描述证据读取完整程度；部分读取不等于签名无效，验证未完成不等于读取失败。'
    if title == '签名结构':
        data = audit.get('signature_structure') or {}
        notes = data.get('issues', []) + data.get('notes', [])
        return '\n'.join(notes + [data.get('scope', '尚未取得结构检查依据。')])
    if title == '字节摘要':
        data = audit.get('supplemental_digest') or {}
        basis = ('本卡采用验证器实际 intact 结果；补充比较未完成。\n'
                 if data.get('digest_matches') is None and not audit.get('unlinked_object')
                 and isinstance(audit.get('intact'), bool) else '本卡采用补充摘要结果，或尚无可用结果。\n')
        return ('按签名声明的 ByteRange 对比摘要；不单凭不一致判断原因、操作者或恶意。\n'
                + basis + str(data.get('error') or data.get('note') or '详见验证器 intact 字段。'))
    if title == '数学签名':
        return '仅显示验证器实际给出的 signature_valid；摘要、文件角色和证书名称不会替代此检查。'
    return '身份信任单独判断；未建立或未检查不表示字节发生修改。'


def coverage_description(audit):
    # 覆盖范围、字段关联仍保留在详细依据中，不挤占统一五项检查。
    if audit.get('unlinked_object'):
        return '未关联当前签名字段树；字节摘要对照不等于 CMS 数学签名、页面绑定或身份验证。'
    coverage = str(audit.get('coverage', ''))
    if coverage.endswith('.ENTIRE_REVISION'):
        return '此签名只覆盖历史修订。当前文件有后续内容，需核对改动分类和签名策略。'  # 限制：签名未覆盖当前全部内容时，不能当成整份文件已签署
    if coverage.endswith('.ENTIRE_FILE'):
        return '覆盖当前文件；签名数学有效、身份可信与正文内容真实仍是不同结论。'  # 限制：数学有效、身份可信和正文真实分开判断
    return '尚不能确认覆盖范围，请查看“覆盖与修订”。'  # 限制：覆盖范围不能确认时不把它当成整份文件已签署
