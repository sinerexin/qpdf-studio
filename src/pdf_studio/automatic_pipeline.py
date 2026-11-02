"""第 1 环节：从 A 原稿抽出生成参数。签署和检查在 SigningStages。"""
from pathlib import Path

from .automatic_signing import generation_reference, reference_defaults
from .file_identity import file_identity
from .signature_evidence import read_signature_audit
from .signature_templates import build_reference_template


def prepare_a_parameters(*, original=None, selected_field=None, expected_original_identity=None,
                         cached_original_audit=None, cached_original_error=None,
                         use_cached_original_read=False):
    """只取生成参数。这次读取不是第 5 环节对 A 的检查，也不把 A 的报告复制成 B 的结果。"""
    original = Path(original) if original is not None else None
    report, read_error, original_identity = None, None, None
    if original is not None:
        try:
            original_identity = file_identity(original)
        except OSError as exc:
            read_error = f'{type(exc).__name__}: {exc}'
        if (expected_original_identity is not None
                and original_identity != expected_original_identity):
            raise RuntimeError('读取 A 阶段：原稿文件已变化，请重新点击生成。')
        if original_identity is not None and use_cached_original_read and file_identity(original) == original_identity:
            # 第三步已经读过同一份 A 时，生成参数直接用那次结果，避免再跑一遍验签把界面卡住。
            report, read_error = cached_original_audit, cached_original_error
        elif original_identity is not None:
            try:
                # 参数来自原稿的实际读取；是否信任不作为生成条件。
                report = read_signature_audit(original, [], allow_network=False)
            except Exception as exc:
                # A 无签名或无法完整读取时仍能生成 B，但必须保留读取错误供界面展示。
                read_error = f'{type(exc).__name__}: {exc}'
            if file_identity(original) != original_identity:
                raise RuntimeError('读取 A 阶段：读取期间原稿已变化，未使用旧参数生成。')

    try:
        reference = generation_reference(report, selected_field)
        template = build_reference_template(reference['entry'], reference['audit'])
        defaults = template['parameters'] if template else reference_defaults()
        parameters = dict(label=template['label'] if template else 'B 文档签署者',
                          algorithm=defaults['algorithm'], digest=defaults['digest'], days_valid=365)
        if template:
            parameters['subject_attributes'] = template['subject_attributes']
            # 有序列号或成对有效期才带上。缺证书时仍用随机序列号和 365 天。
            for key in ('serial_number', 'not_before', 'not_after'):
                if key in template:
                    parameters[key] = template[key]
    except Exception as exc:
        raise RuntimeError(f'准备 A 参考参数阶段：{type(exc).__name__}: {exc}') from exc

    return dict(original_audit=report, original_read_error=read_error,
                original_identity=original_identity, parameters=parameters)
