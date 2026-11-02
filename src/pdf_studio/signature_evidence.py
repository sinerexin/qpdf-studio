"""Read-only signature evidence. Validation remains owned by signature_audit.

Embedded certificate bags are NOT verified chains. Parsed PDF dictionaries
and ASN.1 values are NOT contiguous original file bytes. No private key access.
"""
from __future__ import annotations

import base64
import hashlib
import io
from datetime import date, datetime
from pathlib import Path

from asn1crypto import cms, x509 as asn_x509, ocsp, crl
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, dsa, ec
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import enumerate_sig_fields
from pyhanko.sign.general import extract_certificate_info

from .certificate_names import name_attributes
from .signature_values import detached_values
from .signature_discovery import discover_signature_objects, detached_digest_evidence, reference_key
from .signature_structure import inspect_signature_structure
from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature, extract_contents

MAX_SIGNATURES = 128
MAX_CMS_BYTES = 16 * 1024 * 1024
MAX_CERTIFICATES = 256
MAX_FILE_BYTES = 512 * 1024 * 1024
PRIVATE_KEY_NOTE = "标准 PDF 数字签名结构不提供私钥；不能由公钥反推。未读取本机或独立私钥文件。"  # 限制：不能从公钥反推或读取私钥


def _error(exc):
    return f"{type(exc).__name__}: {exc}"


class NativeView:
    """Bound recursive ASN.1 previews explicitly; never silently claim completeness."""

    def __init__(self):
        self.nodes = 0
        self.bytes = 0
        self.limits = []

    def convert(self, value, depth=0):
        self.nodes += 1
        if self.nodes > 50000 or depth > 32:
            self.limits.append("ASN.1 结构达到 50000 节点或 32 层预览上限；请检查 CMS DER 原始数据")
            return {"not_expanded": "structure_limit"}  # 限制：结构超过展开上限后不再展开
        if isinstance(value, bytes):
            self.bytes += len(value)
            if self.bytes > MAX_CMS_BYTES:
                self.limits.append("ASN.1 二进制展开达到 16 MiB 上限；CMS DER 另行保留")
                return {"length": len(value), "sha256": hashlib.sha256(value).hexdigest(), "not_expanded": "bytes_limit"}  # 限制：字节内容超过上限后不再展开
            return {"length": len(value), "hex": value.hex()}
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): self.convert(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.convert(v, depth + 1) for v in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)


def public_key_details(key):
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    result = {
        "type": type(key).__name__, "bits": getattr(key, "key_size", None),
        "spki_sha256": hashlib.sha256(der).hexdigest(), "spki_der_hex": der.hex(),
        "spki_pem": key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii"),
        "private_key": PRIVATE_KEY_NOTE,
    }
    if isinstance(key, rsa.RSAPublicKey):
        n = key.public_numbers()
        result["parameters"] = {"n_hex": format(n.n, "x"), "e_decimal": n.e, "e_hex": format(n.e, "x")}
    elif isinstance(key, ec.EllipticCurvePublicKey):
        n = key.public_numbers()
        result["parameters"] = {"curve": n.curve.name, "x_hex": format(n.x, "x"), "y_hex": format(n.y, "x")}
    elif isinstance(key, dsa.DSAPublicKey):
        n = key.public_numbers()
        p = n.parameter_numbers
        result["parameters"] = {"y_hex": format(n.y, "x"), "p_hex": format(p.p, "x"), "q_hex": format(p.q, "x"), "g_hex": format(p.g, "x")}
    else:
        result["parameters_note"] = "此算法完整公钥见 SPKI DER / PEM；未转成 RSA 或 EC 参数"
    return result


def certificate_details(asn_cert):
    der = asn_cert.dump()
    cert = x509.load_der_x509_certificate(der)
    view = NativeView()
    result = {
        "subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
        # 保留结构化 RDN 属性，避免 UI 用普通逗号切分破坏转义名称或重复机构字段。
        "subject_attributes": name_attributes(cert.subject), "issuer_attributes": name_attributes(cert.issuer),
        "serial_decimal": str(cert.serial_number), "serial_hex": format(cert.serial_number, "x"),
        "version": cert.version.name,
        "not_before": cert.not_valid_before_utc.isoformat(), "not_after": cert.not_valid_after_utc.isoformat(),
        "signature_algorithm_oid": cert.signature_algorithm_oid.dotted_string,
        "sha256": hashlib.sha256(der).hexdigest(), "sha1_identifier_only": hashlib.sha1(der).hexdigest(),
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        "certificate_der_hex": der.hex(), "certificate_signature_hex": cert.signature.hex(),
        "identity_note": "Subject / Issuer 是证书声明；内嵌、自签或日期有效均不自动建立独立身份信任。",  # 限制：证书名称、自签和日期有效都不等于身份已验证
    }
    try:
        result["public_key"] = public_key_details(cert.public_key())
    except Exception as exc:
        result["public_key_error"] = _error(exc)
    try:
        result["tbs_and_extensions"] = view.convert(asn_cert["tbs_certificate"].native)
    except Exception as exc:
        result["extensions_parse_error"] = _error(exc)
    result["limits"] = sorted(set(view.limits))
    return result


def _pdf_dictionary(sig_object):
    # Reconstructed representation only; /Contents is held separately as CMS.
    values = {}
    for key in sig_object.keys():
        if str(key) == "/Contents":
            values[str(key)] = "见 pdf_contents_base64：PDF 字符串解码后原值（含填充）；CMS DER 另列，不是磁盘原始语法字节"
            continue
        value = sig_object.raw_get(key)
        stream = io.BytesIO()
        value.write_to_stream(stream)
        data = stream.getvalue()
        if len(data) > 1024 * 1024:
            values[str(key)] = {"not_expanded": "dictionary_value_exceeds_1MiB", "length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        else:
            values[str(key)] = data.decode("latin-1")
    return values


def _timestamp_details(signer_info):
    tokens = []
    # Both document timestamp signatures and signature-time tokens are shown.
    attrs = signer_info["unsigned_attrs"]
    if attrs.native is None:
        return tokens
    for attr in attrs:
        if attr["type"].native not in ("signature_time_stamp_token", "content_time_stamp"):
            continue
        for token in attr["values"]:
            item = {"attribute_oid": attr["type"].dotted, "validation": "只解析；本栏不独立验证 TSA 签名、信任、吊销或摘要绑定"}  # 限制：时间戳栏只解析，不单独建立 TSA 信任
            try:
                sd = token["content"]
                content = sd["encap_content_info"]["content"]
                view = NativeView()
                item["tst_info"] = view.convert(content.parsed.native)
                item["cms_structure"] = view.convert(sd.native)
                item["limits"] = sorted(set(view.limits))
            except Exception as exc:
                item["parse_error"] = _error(exc)
            tokens.append(item)
    return tokens


def _extract_signature(signed, index):
    entry = {"index": index, "field": signed.field_name, "errors": [], "limits": []}
    operations = {
        "pdf_dictionary_reconstructed_not_source_bytes": lambda: _pdf_dictionary(signed.sig_object),
        "signature_object_reference": lambda: _reference(signed.sig_field.raw_get('/V')),
        "signed_revision_index_zero_based": lambda: signed.signed_revision,
        "byte_range": lambda: list(signed.byte_range),
        "docmdp_policy_claimed": lambda: str(signed.docmdp_level),
        "fieldmdp_policy_claimed": lambda: ({"action": str(signed.fieldmdp.action), "fields": signed.fieldmdp.fields} if signed.fieldmdp is not None else None),
    }
    for name, operation in operations.items():
        try:
            entry[name] = operation()
        except Exception as exc:
            entry["errors"].append({"section": name, "error": _error(exc)})
    # 字段识别与对象扫描共用同一套 CMS/证书解析，避免两条路径显示不同字段。
    return _extract_cms(signed.pkcs7_content, entry, lambda: signed.signer_cert)


def _extract_cms(raw, entry, signer_certificate=None):
    """读取容器、内嵌证书和公钥；不把“成功解析”当成“验证通过”。"""
    data = None
    entry["pdf_contents_length"] = len(raw)
    entry["pdf_contents_sha256"] = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_CMS_BYTES:
        entry["limits"].append("CMS 大于 16 MiB，未展开或导出；当前数据不完整")
        return entry
    entry["pdf_contents_base64"] = base64.b64encode(raw).decode("ascii")
    try:
        content_info = cms.ContentInfo.load(raw)
        der = content_info.dump()
        entry["cms_der_base64"] = base64.b64encode(der).decode("ascii")
        entry["cms_length"] = len(der)
        entry["cms_sha256"] = hashlib.sha256(der).hexdigest()
        data = content_info["content"]
        view = NativeView()
        entry["cms_structure"] = view.convert(content_info.native)
        entry["limits"].extend(sorted(set(view.limits)))
        entry["signer_infos"] = []
        for info in data["signer_infos"]:
            iview = NativeView()
            entry["signer_infos"].append({
                "signer_identifier": iview.convert(info["sid"].native),
                "digest_algorithm": iview.convert(info["digest_algorithm"].native),
                "signature_algorithm": iview.convert(info["signature_algorithm"].native),
                "signature_value_hex": info["signature"].native.hex(),
                "signed_attributes": iview.convert(info["signed_attrs"].native),
                "unsigned_attributes": iview.convert(info["unsigned_attrs"].native),
                "timestamp_tokens": _timestamp_details(info),
            })
            entry["limits"].extend(sorted(set(iview.limits)))
        eci = data["encap_content_info"]
        if eci["content_type"].native == "tst_info":
            tview = NativeView()
            entry["document_timestamp_tst_info"] = tview.convert(eci["content"].parsed.native)
        entry["embedded_revocation_info"] = view.convert(data["crls"].native)
    except Exception as exc:
        entry["errors"].append({"section": "cms", "error": _error(exc)})
    entry["embedded_certificates"] = []
    try:
        bag = data["certificates"]
        for n, choice in enumerate(bag if bag.native is not None else []):
            if n >= MAX_CERTIFICATES:
                entry["limits"].append("内嵌证书数量超过 256；超出部分未展开，CMS DER 保留")  # 限制：内嵌证书超过 256 张就停止展开
                break
            try:
                if choice.name == "certificate":
                    entry["embedded_certificates"].append(certificate_details(choice.chosen))
                else:
                    entry["embedded_certificates"].append({"choice": choice.name, "der_hex": choice.dump().hex(), "note": "非 X.509 公钥证书，未作 X.509 解释"})  # 限制：不是 X.509 证书时不解释成身份
            except Exception as exc:
                entry["errors"].append({"section": f"certificate_{n}", "error": _error(exc)})
    except Exception as exc:
        entry["errors"].append({"section": "certificate_bag", "error": _error(exc)})
    try:
        cert = signer_certificate() if signer_certificate is not None else extract_certificate_info(data).signer_cert
        entry["signer_certificate"] = certificate_details(cert) if cert is not None else None
    except Exception as exc:
        entry["errors"].append({"section": "signer_certificate", "error": _error(exc)})
    return entry



def _discovery_info(candidate):
    """只公开定位信息，不把解析器对象写入 JSON 报告。"""
    return {'method': 'xref_object_scan', 'field_tree_linked': False,
            'object_location': candidate['location'],
            'container_reference': candidate['container_reference'],
            'direct_path': candidate['direct_path'],
            'note': '未在已枚举的签名字段树中找到关联；可能是孤立、遗留或非标准签名对象，不代表当前页面已获签名认证。'}


def _extract_unlinked(candidate, index, raw, stream):
    """提取未关联对象中的实际数据；不补写字段、不修复或重存原 PDF。"""
    dictionary = candidate['dictionary']
    label = '对象 ' + candidate['location'] + '（未关联字段树）'
    entry = {'index': index, 'field': label, 'field_name_in_pdf': None,
             'signature_object_reference': candidate['location'],
             'discovery': _discovery_info(candidate), 'errors': [], 'limits': []}
    for key, getter in {
        'pdf_dictionary_reconstructed_not_source_bytes': lambda: _pdf_dictionary(dictionary),
        'byte_range': lambda: list(dictionary['/ByteRange']),
    }.items():
        try:
            entry[key] = getter()
        except Exception as exc:
            entry['errors'].append({'section': key, 'error': _error(exc)})
    _extract_cms(raw, entry)
    metadata = {'field': label, 'signature_subfilter': str(dictionary.get('/SubFilter', '')),
                'signing_date_claimed': str(dictionary.get('/M', '')) or None,
                'reason_claimed': str(dictionary.get('/Reason', '')) or None,
                'location_claimed': str(dictionary.get('/Location', '')) or None,
                'intact': None, 'signature_valid': None, 'signer_trusted': None,
                'unlinked_object': True, 'revocation_assessment': 'offline_only_no_online_revocation',
                'overall_technical_result': '未关联字段树的签名数据；未完成完整 PDF 验签'}
    try:
        # 算法和摘要来自实际 CMS SignerInfo，不从文件名、图章或证书名称推测。
        data = cms.ContentInfo.load(raw)['content'] if len(raw) <= MAX_CMS_BYTES else None
        if data is not None and len(data['signer_infos']) == 1:
            info = data['signer_infos'][0]
            metadata['digest_algorithm'] = info['digest_algorithm']['algorithm'].native
            metadata['signature_mechanism'] = info['signature_algorithm']['algorithm'].native
            metadata['supplemental_digest'] = detached_digest_evidence(
                stream, entry.get('byte_range', []), data, metadata['signature_subfilter'])
    except Exception as exc:
        entry['errors'].append({'section': 'supplemental_digest', 'error': _error(exc)})
    # 使用独立的补充结果字段，绝不把摘要一致填成数学签名或身份信任“通过”。
    entry['audit'] = metadata
    return entry


def _reference(value):
    ref = getattr(value, 'reference', None)
    return f"{ref.idnum} {ref.generation} R" if ref is not None else "直接对象（无独立 xref）"


def _attach_common_checks(entry, reader, stream, dictionary, reference):
    """字段树与对象扫描走同一个只读检查入口；不接收 A/B 角色参数。"""
    metadata = dict(entry.get('audit') or {})
    if entry.get('audit') is None:
        metadata['verification_unavailable'] = True
    entry['audit'] = metadata
    metadata['signature_structure'] = inspect_signature_structure(reader, stream, dictionary, reference)
    # 已计算的对象摘要复用；关联字段也采用同样的 detached 算法，避免两套口径。
    if 'supplemental_digest' not in metadata:
        try:
            raw = extract_contents(dictionary)
            if len(raw) > MAX_CMS_BYTES:
                metadata['supplemental_digest'] = {'digest_matches': None, 'note': 'CMS 超出展开范围，未计算摘要。'}
                return
            data = cms.ContentInfo.load(raw)['content']
            metadata['supplemental_digest'] = detached_digest_evidence(
                stream, list(dictionary['/ByteRange']), data, str(dictionary.get('/SubFilter', '')))
        except Exception as exc:
            # 不能计算与比较后确实不一致是两种状态，不能用 False 代替错误。
            metadata['supplemental_digest'] = {'digest_matches': None, 'error': _error(exc)}


def _dss_store(reader):
    """Surface offline DSS assets, explicitly not an LTV or revocation verdict."""
    result = {"note": "DSS 内嵌证据只作读取，不自动构成 LTV / PAdES 或未吊销结论。", "errors": [], "limits": []}
    store = reader.root.get('/DSS')
    if store is None:
        result['state'] = 'not_present'
        return result
    store = store.get_object()
    result['dictionary_reconstructed'] = _pdf_dictionary(store)
    total = 0
    for key, parser in (('/Certs', asn_x509.Certificate), ('/OCSPs', ocsp.OCSPResponse), ('/CRLs', crl.CertificateList)):
        values = store.get(key, [])
        values = values.get_object() if hasattr(values, 'get_object') else values
        result[key] = []
        for index, reference in enumerate(values):
            if index >= 256:
                result['limits'].append(key + ' 超过 256 项，后续未展开')
                break
            item = {'reference': _reference(reference)}
            result[key].append(item)
            try:
                data = reference.get_object().data
                total += len(data)
                item.update(length=len(data), sha256=hashlib.sha256(data).hexdigest())
                if len(data) > 4 * 1024 * 1024 or total > 32 * 1024 * 1024:
                    result['limits'].append(key + ' 解码证据超过单项 4 MiB 或累计 32 MiB；未展开')
                    continue
                item['decoded_der_base64'] = base64.b64encode(data).decode('ascii')
                parsed = parser.load(data)
                if key == '/Certs':
                    item['certificate'] = certificate_details(parsed)
                else:
                    view = NativeView()
                    item['parsed'] = view.convert(parsed.native)
                    result['limits'].extend(sorted(set(view.limits)))
            except Exception as exc:
                item['error'] = _error(exc)
    return result


def validation_details(status, explicit_roots):
    """Project the *already computed* validator status; never revalidate here."""
    def one(current):
        if current is None:
            return {"state": "not_available_or_not_evaluated"}
        row = {}
        for name in ("intact", "valid", "md_algorithm", "pkcs7_signature_mechanism",
                     "trust_problem_indic", "validation_time", "error_time_horizon",
                     "timestamp", "signer_reported_dt", "coverage", "modification_level", "docmdp_ok"):
            value = getattr(current, name, None)
            row[name] = value if value is None or isinstance(value, (bool, int, float)) else str(value)
        row["trusted_under_explicit_roots"] = bool(current.trusted) if explicit_roots else None  # 限制：没有明确可信根时，不记录为已信任
        row["revocation_finding"] = str(current.revocation_details) if current.revocation_details is not None else "未取得吊销结论；不等于未吊销"  # 限制：没有吊销记录不等于未吊销
        row["validated_certificate_path"] = [certificate_details(c) for c in current.validation_path] if current.validation_path is not None else []
        return row
    return {"signature": one(status),
            "signature_timestamp": one(getattr(status, "timestamp_validity", None)),
            "content_timestamp": one(getattr(status, "content_timestamp_validity", None)),
            "note": "复用本次验证器结果；未提供可信根时不建立身份信任；无吊销记录不等于未吊销。"}  # 限制：未提供可信根时不建立身份信任


def collect_signature_evidence(source, audit):
    """Enrich an existing audit without running cryptographic verification twice.

    Signature evidence collection still runs if the validator rejected the PDF.
    Source is rb only. All partial results and failures are explicit.
    """
    path = Path(source).resolve(strict=True)
    initial = path.stat()
    result = {"schema": "qpdf-studio/signature-evidence-v1", "source_path": str(path),
              "signatures": [], "errors": [], "limits": [], "private_key": PRIVATE_KEY_NOTE,
              "certificate_bag_note": "内嵌证书集合不是已验证信任链；验证路径仅以 audit.certificate_path 结果为准。",  # 限制：PDF 里自带的证书集合不是已验证信任链
              "raw_note": "PDF 字典为解析器重构表示；pdf_contents_base64 为 PDF 字符串解码后原值（含填充）；CMS DER 是 ASN.1 解析器导出的容器。不等于磁盘上 PDF 字符串的十六进制或转义语法字节。",
              "timestamp_note": "时间戳存在、声称签署时间、TSA 数学验证和 TSA 身份信任分别判断；未提供的结论标为未判断。"}  # 限制：时间戳存在不等于 TSA 身份已信任
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        result["source_sha256"] = digest.hexdigest()
        result["source_size"] = stream.tell()
        stream.seek(0)
        try:
            reader = PdfFileReader(stream)
            try:
                result['dss_validation_store'] = _dss_store(reader)
            except Exception as exc:
                result['dss_validation_store'] = {'error': _error(exc)}
            result['empty_signature_fields'] = []
            total_cms = 0
            known_references = set()
            known_dictionary_ids = set()
            # embedded_signatures omits unsupported SubFilters, so enumerate
            # raw fields instead and isolate malformed signatures individually.
            index = 0
            try:
                for name, sig_value, sig_field in enumerate_sig_fields(reader):
                    if sig_value is None:
                        result['empty_signature_fields'].append(name)
                        if len(result['empty_signature_fields']) >= MAX_SIGNATURES:
                            result['limits'].append('空签名字段达到 128 项，后续字段未枚举')  # 限制：空签名字段超过 128 项就停止枚举
                            break
                        continue
                    if index >= MAX_SIGNATURES:
                        result["limits"].append("超过 128 个签名，后续签名未展开；不能视为完整检查")  # 限制：签名超过 128 个就不能当成完整检查
                        break
                    # 对同一间接签名对象按 xref 去重，不按名称或数组位置猜测身份。
                    known_dictionary_ids.add(id(sig_value))
                    known = reference_key(sig_field.get_object().raw_get('/V'))
                    if known is not None:
                        known_references.add(known)
                    try:
                        signed = EmbeddedPdfSignature(reader, sig_field, name)
                        total_cms += len(signed.pkcs7_content)
                        if total_cms > 32 * 1024 * 1024:
                            result["limits"].append("全部 CMS 累计超过 32 MiB，后续签名未展开，不能视为完整检查")  # 限制：签名数据超过 32 MiB 就不能当成完整检查
                            break
                        entry = _extract_signature(signed, index + 1)
                    except Exception as exc:
                        entry = {"index": index + 1, "field": name, "errors": [_error(exc)]}
                        # A legacy / non-CMS signature is evidence, not absent.
                        try:
                            dictionary = sig_value.get_object()
                            entry['pdf_dictionary_reconstructed_not_source_bytes'] = _pdf_dictionary(dictionary)
                            entry['signature_object_reference'] = _reference(sig_field.get_object().raw_get('/V'))
                            raw = extract_contents(dictionary)
                            total_cms += len(raw)
                            entry.update(cms_length=len(raw), cms_sha256=hashlib.sha256(raw).hexdigest())
                            if len(raw) <= MAX_CMS_BYTES and total_cms <= 32 * 1024 * 1024:
                                entry['unparsed_signature_contents_base64'] = base64.b64encode(raw).decode('ascii')
                            else:
                                entry['limits'] = ['未解析签名容器超出大小上限，不展开']  # 限制：单个未解析签名容器超限时不展开
                        except Exception as inner:
                            entry['errors'].append(_error(inner))
                    # Match by field, never position if an audit skipped a signature.
                    entry["audit"] = next((row for row in audit.get("signatures", []) if row.get("field") == entry.get("field")), None)
                    try:
                        _attach_common_checks(entry, reader, stream, sig_value.get_object(),
                                              sig_field.get_object().raw_get('/V'))
                    except Exception as exc:
                        entry['errors'].append({'section': 'common_checks', 'error': _error(exc)})
                    result["signatures"].append(entry)
                    index += 1
            except Exception as exc:
                result['errors'].append('签名字段树读取失败：' + _error(exc))
            # 字段树为空、断链或部分损坏时，仍扫描当前交叉引用内可解析的对象。
            discovery = discover_signature_objects(reader, known_references, known_dictionary_ids=known_dictionary_ids,
                max_candidates=max(0, MAX_SIGNATURES - len(result['signatures'])))
            candidates = discovery.pop('candidates')
            result['object_discovery'] = discovery
            result['errors'].extend(discovery['errors'])
            result['limits'].extend(discovery['limits'])
            for candidate in candidates:
                try:
                    dictionary = candidate['dictionary']
                    raw = extract_contents(dictionary)
                    total_cms += len(raw)
                    if total_cms > 32 * 1024 * 1024:
                        result['limits'].append('全部 CMS 累计超过 32 MiB，剩余对象未展开。')
                        break
                    entry = _extract_unlinked(candidate, len(result['signatures']) + 1, raw, stream)
                    _attach_common_checks(entry, reader, stream, dictionary, candidate['value'])
                except Exception as exc:
                    # 一个候选无法解析，不影响其他候选，也不能把它默默当作“不存在”。
                    entry = {'index': len(result['signatures']) + 1,
                             'field': '对象 ' + candidate['location'] + '（未关联字段树）',
                             'signature_object_reference': candidate['location'],
                             'discovery': _discovery_info(candidate), 'errors': [_error(exc)], 'limits': []}
                result['signatures'].append(entry)
        except Exception as exc:
            result["errors"].append(_error(exc))
    final = path.stat()
    if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
        raise RuntimeError("证据读取期间源文件已变化；结果已丢弃，请重新读取")
    # PDF 数字/字符串对象虽像普通值，仍可能经 container_ref 关联 reader 和文件句柄。
    # 离开读取层前只保留解析值，不能把解析器实例带进 UI 缓存或后续生成参数。
    return detached_values(result)


def read_signature_audit(source, roots=None, *, allow_network=False, require_revocation=False):
    from .signature_audit import audit_signatures
    initial = Path(source).stat()
    if initial.st_size > MAX_FILE_BYTES:
        raise ValueError("签名详情当前支持不超过 512 MiB 的 PDF；此文件未作验证结论")  # 限制：超过 512 MiB 的 PDF 不作验签结论
    try:
        audit = audit_signatures(source, roots or [], allow_network=allow_network, require_revocation=require_revocation)
    except Exception as exc:
        audit = {"count": None, "signatures": [], "validation_error": _error(exc),
                 "trust_roots_supplied": len(roots or []), "network_fetching": allow_network}
    audit["full_evidence"] = collect_signature_evidence(source, audit)
    evidence = audit['full_evidence']
    # 保留 count 的原有含义：它是字段验签器返回数量，不是假设所有候选已经验签。
    audit['detected_signature_count'] = len(evidence['signatures'])
    audit['unlinked_signature_count'] = sum(bool(row.get('discovery')) for row in evidence['signatures'])
    if audit['unlinked_signature_count']:
        audit['discovery_notice'] = '已提取未关联字段树的签名对象；补充摘要对照与完整字段验签分开显示。'
    if (any(row.get('audit') is None or (row.get('audit') or {}).get('verification_unavailable') or row.get('errors') or row.get('limits') for row in evidence['signatures']) or evidence['errors'] or evidence['limits']) and not audit.get('validation_error'):
        audit['validation_error'] = '发现未获完整验证结论的签名数据、读取错误或范围上限；部分证据未完成，不代表无签名'  # 限制：证据不完整时不能断言这份 PDF 没有签名
    final = Path(source).stat()
    if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
        raise RuntimeError("验证期间源文件已变化；结果已丢弃，请重新读取")
    # 字段验签与对象扫描的结果一起脱离解析器；不改变 True/False/None 验证结论。
    return detached_values(audit)
