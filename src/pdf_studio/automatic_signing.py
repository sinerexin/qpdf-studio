"""从 A 的签名报告抽出 B 的生成参数；不依赖 A 已经有签名。"""
from pathlib import Path
from .signature_values import detached_values

from .signature_card_data import default_signature_index

from .keygen import ALGORITHMS, _check_config
from .workflow_policy import signing_output_block_reason


def reference_defaults(entry=None, audit=None):
    entry, audit = entry or {}, audit or {}
    key = (entry.get('signer_certificate') or {}).get('public_key') or {}
    kind, bits = str(key.get('type') or '').upper(), key.get('bits')
    curve = (key.get('parameters') or {}).get('curve')
    candidate = f'RSA-{bits}' if 'RSA' in kind and bits else {
        'secp256r1': 'ECDSA-P256', 'secp384r1': 'ECDSA-P384',
    }.get(curve)
    algorithm = candidate if candidate in ALGORITHMS else 'RSA-3072'
    digest = str(audit.get('digest_algorithm') or 'sha256').lower()
    if entry:
        note = f"参考 A 的签名字段：{entry.get('field') or '未命名'}。算法和摘要均可调整。"
        if candidate not in ALGORITHMS:
            note += ' A 的密钥参数未识别或当前生成器不支持，默认使用 RSA-3072。'
    else:
        note = 'A 暂无可用签名参数；使用可调整的默认参数，不影响为 B 生成新签名。'
    return {'algorithm': algorithm, 'digest': digest, 'note': note}


def read_completion_text(slot, result=None, error=None):
    if error:
        return f'{slot} 签名读取失败：{error}'
    report = result or {}
    evidence = report.get('full_evidence') or {}
    # 状态栏也使用识别数量，不用字段验签器的 0 覆盖对象扫描得到的数据。
    if report.get('unlinked_signature_count'):
        count = report.get('detected_signature_count', len(evidence.get('signatures') or []))
        partial = ' 部分结果不完整，详见依据。' if report.get('validation_error') else ''
        return f'{slot} 已识别 {count} 项签名数据 · 含未关联字段树对象；解析与验证结果分别查看。' + partial
    if report.get('validation_error') or evidence.get('errors') or evidence.get('limits'):
        return f'{slot} 签名读取已结束 · 部分结果不完整，详见验证依据。'
    if report.get('count') == 0 and not evidence.get('signatures'):
        return f'{slot} 签名读取完成 · 未发现已有数字签名；仍可为 B 生成新签名。'
    count = report.get('count')
    return (f'{slot} 签名读取完成 · 共 {count} 个签名，详见验证结果。'
            if isinstance(count, int) else f'{slot} 签名读取已结束 · 数量未确定，详见验证依据。')


def validate_generation_request(source, output, credential_folder, *, label, password=None,
                                algorithm, days_valid, protected_paths=()):
    # Preflight automatic mode without generating or persisting a password.
    _check_config(label, password if password is not None else 'automatic-password-preflight', days_valid)
    if algorithm not in ALGORITHMS:
        raise ValueError('当前密钥生成器不支持所选算法。')  # 限制：当前密钥生成器不支持所选算法
    source, output, folder = Path(source), Path(output), Path(credential_folder)
    if not source.is_file():
        raise ValueError('B 文件不可访问，请重新导入。')
    reason = signing_output_block_reason(output, (source, *protected_paths))
    if reason:
        raise ValueError(reason)
    if folder.exists():
        raise ValueError('凭据目录已存在，请选择一个新目录，避免覆盖已有密钥。')
    if not folder.parent.is_dir():
        raise ValueError('凭据输出目录的上级文件夹不存在。')


def generation_reference(report, selected_field=None):
    """从 A 的实际解析报告取参数快照，绝不从标签排版、B 模板或验证卡反向取值。"""
    entries = ((report or {}).get('full_evidence') or {}).get('signatures') or []
    index = default_signature_index(entries, selected_field)
    if index < 0:
        return {'entry': {}, 'audit': {}}
    entry = detached_values(entries[index])
    audit = detached_values(entry.get('audit') or next(
        (row for row in (report or {}).get('signatures', []) if row.get('field') == entry.get('field')), {}))
    return {'entry': entry, 'audit': audit}


def generated_signature_entries(report, generated):
    """用最终 PDF 内证书指纹关联本次签名，不按字段顺序、名称或预计参数猜测。"""
    expected = str(generated.get('certificate_sha256_fingerprint') or '').replace(':', '').lower()
    if not expected:
        return []
    entries = ((report or {}).get('full_evidence') or {}).get('signatures') or []
    return [entry for entry in entries
            if str((entry.get('signer_certificate') or {}).get('sha256') or '').replace(':', '').lower() == expected]
