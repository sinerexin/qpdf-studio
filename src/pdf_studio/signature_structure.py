"""A/B 共用的只读签名结构检查；磁盘位置只来自当前有效 xref。"""
from __future__ import annotations

import io

from pyhanko.pdf_utils import generic, misc
from pyhanko.pdf_utils.xref import read_object_header

MAX_DICTIONARY_BYTES = 32 * 1024 * 1024


def _string_bytes(value):
    # 这里只用于核对解析值，不把解码后的字符串称为磁盘原始语法。
    if isinstance(value, generic.ByteStringObject):
        return bytes(value)
    if isinstance(value, generic.TextStringObject):
        return value.original_bytes
    raise ValueError('/Contents 不是直接 PDF 字符串。')


def _contents_span(reader, stream, reference, dictionary):
    """定位当前间接签名字典中 /Contents 的原始字符串边界，包含分隔符。"""
    ref = getattr(reference, 'reference', None)
    if ref is None or ref.pdf is not reader:
        raise ValueError('直接或嵌套签名字典没有独立磁盘定位，本次无法精确映射。')
    if reader.encrypted:
        raise ValueError('加密文件本次不执行字符串原值与磁盘语法的精确映射。')
    offset = reader.xrefs[ref]
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('对象位于对象流、已释放或没有普通文件偏移，无法精确映射。')
    stream.seek(offset)
    # 有界缓冲防止定位时加载整个大文件；超过范围保留“无法确定”，不回退全文件正则猜测。
    budget = min(MAX_DICTIONARY_BYTES, 2 * len(_string_bytes(dictionary.raw_get('/Contents'))) + 1024 * 1024)
    source = io.BytesIO(stream.read(budget))
    if read_object_header(source, strict=True) != (ref.idnum, ref.generation):
        raise ValueError('xref 指向的对象头与对象号/代号不一致。')
    misc.read_non_whitespace(source, seek_back=True)
    if source.read(2) != b'<<':
        raise ValueError('当前对象不是直接字典。')
    values, spans = {}, {}
    for _ in range(4096):
        misc.read_non_whitespace(source, seek_back=True)
        if source.read(2) == b'>>':
            break
        source.seek(-2, 1)
        key = generic.read_object(source, ref)
        if not isinstance(key, generic.NameObject):
            raise ValueError('字典键不是 PDF name。')
        if str(key) in values:
            raise ValueError('字典存在重复键，无法确认采用哪一个原始位置。')
        misc.read_non_whitespace(source, seek_back=True)
        start = source.tell()
        value = generic.read_object(source, ref)
        values[str(key)] = value
        spans[str(key)] = (offset + start, offset + source.tell())
    else:
        raise ValueError('字典键数达到定位预算，未完成精确映射。')
    # 比较同一当前对象的两个关键值；不解析间接 /Contents，不选择历史同号对象。
    if not isinstance(values.get('/ByteRange'), generic.ArrayObject):
        raise ValueError('/ByteRange 不是直接数组，未确认精确映射。')
    if list(values['/ByteRange']) != list(dictionary['/ByteRange']):
        raise ValueError('原始字典的 ByteRange 与当前解析对象不同。')
    if _string_bytes(values.get('/Contents')) != _string_bytes(dictionary.raw_get('/Contents')):
        raise ValueError('原始 /Contents 字符串与当前解析对象不同。')
    return spans['/Contents']


def inspect_signature_structure(reader, stream, dictionary, reference=None):
    """结构、范围、位置分别保留；异常不自动改写摘要或数学验签结果。"""
    result = {'state': 'unknown', 'range_valid': None, 'contents_gap_matches': None,
              'position_precision': 'unavailable', 'issues': [], 'notes': [],
              'scope': 'ByteRange 和当前签名 Contents 位置；不是完整 PDF 合规审计。'}
    position = stream.tell()
    try:
        stream.seek(0, 2)
        size = stream.tell()
        ranges = list(dictionary['/ByteRange'])
        result.update(byte_range=ranges, source_size=size)
        if len(ranges) < 4 or len(ranges) % 2 or len(ranges) > 256:
            raise ValueError('ByteRange 必须是有限的偏移/长度对。')
        previous_end = 0
        for start, length in zip(ranges[::2], ranges[1::2]):
            if (isinstance(start, bool) or isinstance(length, bool)
                    or not isinstance(start, int) or not isinstance(length, int)
                    or start < previous_end or length < 0 or start + length > size):
                raise ValueError('ByteRange 有负数、重叠、非整数或越界。')
            previous_end = start + length
        result.update(range_valid=True, signed_range_end=previous_end,
                      bytes_after_declared_range=size - previous_end)
        if len(ranges) != 4 or ranges[0] != 0:
            result['notes'].append('不是从文件起点开始的标准两段范围，本次无法完整判定结构。')
            return result
        gap = (ranges[1], ranges[2])
        result['declared_gap_half_open'] = list(gap)
        try:
            span = _contents_span(reader, stream, reference, dictionary)
        except Exception as exc:
            result['notes'].append(f'精确定位未完成：{type(exc).__name__}: {exc}')
            return result
        result.update(position_precision='exact_current_xref',
                      contents_span_half_open=list(span), contents_gap_matches=gap == span)
        if gap != span:
            result['issues'].append('ByteRange 留出的空洞与此签名 /Contents 的实际字符串位置不对应。')
        result['state'] = 'normal' if gap == span else 'abnormal'
    except (KeyError, TypeError, ValueError) as exc:
        result['range_valid'] = False
        result['state'] = 'abnormal'
        result['issues'].append(f'{type(exc).__name__}: {exc}')
    except Exception as exc:
        result['notes'].append(f'结构检查未完成：{type(exc).__name__}: {exc}')
    finally:
        stream.seek(position)
    return result
