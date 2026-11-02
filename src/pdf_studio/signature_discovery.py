"""只读扫描当前交叉引用可解析的对象，补充字段树以外的签名候选。"""
from __future__ import annotations

import hashlib
import hmac
import time

from pyhanko.pdf_utils import generic

MAX_OBJECTS = 100000
MAX_NODES = 200000
MAX_SECONDS = 20


def reference_key(value):
    """使用对象号与代号去重；代号不是增量修订序号。"""
    ref = getattr(value, 'reference', None)
    return (ref.idnum, ref.generation) if ref is not None else None


def discover_signature_objects(reader, known_references=(), *, known_dictionary_ids=(), max_candidates=128):
    """不改 Catalog/Fields，不解码页面流，也不把候选冒充已关联签名。"""
    result = {'scope': 'current_resolved_xref_objects', 'objects_scanned': 0,
              'candidates': [], 'errors': [], 'limits': [], 'error_count': 0,
              'note': '扫描当前可解析对象，包括未挂入字段树的对象；不回放已覆盖或已释放的历史对象版本。'}
    seen = set(known_references)
    known_direct = set(known_dictionary_ids)
    started, nodes = time.monotonic(), 0

    def error(location, exc):
        result['error_count'] += 1
        # 错误数量单独保留，避免损坏文件生成无限长的错误文本。
        if len(result['errors']) < 64:
            result['errors'].append(f'{location}: {type(exc).__name__}: {exc}')

    try:
        refs = set()
        # 合并各修订登记过的引用，但后续仅读取它们在当前文件中的有效版本。
        for revision in range(reader.xrefs.total_revisions):
            refs.update(reader.xrefs.explicit_refs_in_revision(revision))
            if len(refs) > MAX_OBJECTS or time.monotonic() - started > MAX_SECONDS:
                result['limits'].append('交叉引用枚举达到资源预算，扫描不完整，不能断言没有签名。')
                return result
        for ref in sorted(refs, key=lambda r: (r.idnum, r.generation)):
            if time.monotonic() - started > MAX_SECONDS:
                result['limits'].append('对象扫描达到时间预算，剩余对象未检查。')
                break
            location = f'{ref.idnum} {ref.generation} R'
            try:
                # 已释放对象不属于当前可解析对象；不能当成读取失败或当前签名。
                if reader.xrefs[ref] is None:
                    continue
                obj = reader.get_object(ref)
                result['objects_scanned'] += 1
                value = generic.IndirectObject(ref.idnum, ref.generation, reader)
                stack = [(obj, value, '', 0)]
                while stack:
                    current, value, suffix, depth = stack.pop()
                    nodes += 1
                    if nodes > MAX_NODES or time.monotonic() - started > MAX_SECONDS:
                        result['limits'].append('直接对象遍历达到节点或时间预算，扫描不完整。')
                        return result
                    if depth > 32:
                        result['limits'].append(f'{location}{suffix} 嵌套超过 32 层，未继续展开。')
                        continue
                    if isinstance(current, generic.DictionaryObject):
                        kind = str(current.get('/Type', ''))
                        candidate = kind in ('/Sig', '/DocTimeStamp') or (
                            '/ByteRange' in current and '/Contents' in current)
                        identity = (ref.idnum, ref.generation) if not suffix else (ref.idnum, ref.generation, suffix)
                        if candidate and identity not in seen and id(current) not in known_direct:
                            if len(result['candidates']) >= max_candidates:
                                result['limits'].append('签名候选数量达到展开预算，其余未展开。')
                                return result
                            seen.add(identity)
                            result['candidates'].append({'value': value, 'dictionary': current,
                                'location': location + suffix, 'container_reference': location,
                                'direct_path': suffix or None, 'identity': identity})
                        # 间接引用会由对象清单单独读取；只在此展开直接字典/数组，避免循环引用。
                        for key in current.keys():
                            child = current.raw_get(key)
                            if isinstance(child, (generic.DictionaryObject, generic.ArrayObject)):
                                stack.append((child, child, suffix + '/' + str(key).lstrip('/'), depth + 1))
                    elif isinstance(current, generic.ArrayObject):
                        for i, child in enumerate(current):
                            if isinstance(child, (generic.DictionaryObject, generic.ArrayObject)):
                                stack.append((child, child, suffix + f'[{i}]', depth + 1))
            except Exception as exc:
                error(location, exc)
    except Exception as exc:
        error('交叉引用扫描', exc)
    return result


def detached_digest_evidence(stream, byte_range, signed_data, subfilter):
    """只比较声明字节段的摘要；这不验证 CMS 数学签名、身份或页面绑定。"""
    result = {'digest_matches': None, 'note': '仅为字节段摘要对照，不是完整 PDF 验签结论。'}
    # 文档时间戳、旧式 SHA1 封装等需要各自规则，不能套用 detached 摘要比较。
    if subfilter not in ('/adbe.pkcs7.detached', '/ETSI.CAdES.detached'):
        result['note'] = '此 SubFilter 未执行补充摘要比较；保留签名容器供查看。'
        return result
    position = stream.tell()
    try:
        stream.seek(0, 2)
        size = stream.tell()
        ranges = list(byte_range)
        if len(ranges) < 4 or len(ranges) % 2 or len(ranges) > 256:
            raise ValueError('ByteRange 必须是有限的偏移/长度对。')
        previous_end = 0
        for start, length in zip(ranges[::2], ranges[1::2]):
            if (isinstance(start, bool) or isinstance(length, bool)
                    or not isinstance(start, int) or not isinstance(length, int)
                    or start < previous_end or length < 0 or start + length > size):
                raise ValueError('ByteRange 有重叠、负数、非整数或越界。')
            previous_end = start + length
        infos = signed_data['signer_infos']
        if len(infos) != 1 or signed_data['encap_content_info']['content_type'].native != 'data':
            raise ValueError('当前补充摘要比较仅处理单个 SignerInfo 的 detached data。')
        info = infos[0]
        algorithm = info['digest_algorithm']['algorithm'].native
        attrs = info['signed_attrs']
        values = [v.native for a in attrs if a['type'].native == 'message_digest' for v in a['values']]
        if len(values) != 1 or not isinstance(values[0], bytes):
            raise ValueError('没有唯一的 messageDigest 签名属性。')
        digest = hashlib.new(algorithm)
        for start, length in zip(ranges[::2], ranges[1::2]):
            stream.seek(start)
            remaining = length
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError('读取签名字节段时提前遇到文件尾。')
                digest.update(block)
                remaining -= len(block)
        result.update(digest_matches=hmac.compare_digest(digest.digest(), values[0]),
                      algorithm=algorithm, computed_digest_hex=digest.hexdigest(),
                      claimed_digest_hex=values[0].hex(), signed_range_end=previous_end,
                      bytes_after_declared_range=size - previous_end,
                      first_range_starts_at_zero=ranges[0] == 0)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        stream.seek(position)
    return result
