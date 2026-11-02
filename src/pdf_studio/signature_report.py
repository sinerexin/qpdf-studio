"""Static, escaped local HTML report for PDF signature assessments."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


def signature_audit_html(data: dict[str, Any]) -> str:
    """Format an already-computed audit; does not re-evaluate signature trust."""
    results = {
        "signature_valid_and_certificate_path_trusted": "数学签名有效，所选证书信任路径成立",
        "cryptographically_valid_identity_unverified": "数学签名有效，签署者身份尚未验证",  # 限制：数学有效不等于身份已验证
        "cryptographically_valid_but_certificate_not_trusted": "数学签名有效，但证书路径未通过信任验证",  # 限制：证书未通过可信根时，不显示为身份已验证
        "cryptographic_integrity_or_signature_failed": "签名数学验证或字节完整性检查未通过",
        "inconclusive_validation_error": "签名检查存在错误，结论不确定",
    }
    def e(value: Any) -> str:
        return escape(str(value) if value is not None else "未提供", quote=True)

    def cell(label: str, value: Any) -> str:
        return f"<tr><th>{e(label)}</th><td>{e(value)}</td></tr>"

    sections = []
    for index, item in enumerate(data.get("signatures", []), 1):
        cert = item.get("certificate") or {}
        validity = item.get("certificate_validity") or {}
        byte_range = item.get("byte_range_evidence") or {}
        status = item.get("overall_technical_result", "inconclusive_validation_error")
        rows = [
            cell("PDF 签名字段", item.get("field")),
            cell("技术核验结论", results.get(status, status)),
            cell("签名字节完整性", item.get("intact")),
            cell("CMS 数学签名有效", item.get("signature_valid")),
            cell("证书信任路径已通过", item.get("signer_trusted")),  # 限制：报告单独列出信任路径是否通过，不把数学有效写成身份已验证
            cell("签名覆盖范围", item.get("coverage")),
            cell("签名后修订等级", item.get("modification_level")),
            cell("签名算法", item.get("signature_mechanism")),
            cell("摘要算法", item.get("digest_algorithm")),
            cell("证书主体（身份声明）", cert.get("claimed_subject")),
            cell("证书签发者（证书字段）", cert.get("issuer")),
            cell("证书 SHA-256 指纹", cert.get("sha256_fingerprint")),
            cell("证书有效期开始", validity.get("not_before")),
            cell("证书有效期结束", validity.get("not_after")),
            cell("当前是否在证书有效期内", validity.get("currently_within_dates")),
            cell("签署时间（PDF 自报）", item.get("signing_date_claimed")),
            cell("签名签署的文件终止位置", byte_range.get("signed_revision_ends_at")),
            cell("签名范围外的后续字节数", item.get("additional_bytes_after_signed_revision")),
            cell("吊销检查策略", item.get("revocation_assessment")),
        ]
        path = item.get("certificate_path") or []
        if path:
            rows.append(cell("证书链路径", " → ".join(
                part.get("subject", "") for part in path
            )))
        sections.append(
            f"<section><h2>签名 #{index}</h2><table>{''.join(rows)}</table></section>"
        )
    if not sections:
        sections = ["<section><p>此 PDF 未发现嵌入数字签名。可见图章或图片本身不属于密码学签名。</p></section>"]
    limitations = "".join(f"<li>{e(text)}</li>" for text in data.get("limitations", []))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>PDF 数字签名核验报告</title>
<style>
body{{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f7fb;color:#172235;margin:0}}
main{{max-width:980px;margin:40px auto;padding:30px;background:#fff;border:1px solid #dbe2ed;border-radius:12px}}
h1{{font-size:26px;margin:0 0 6px}}h2{{font-size:19px;margin-top:30px}}
.sub{{color:#57667d}}.summary{{padding:18px 22px;background:#eef5fa;border-left:4px solid #247d97;margin-top:24px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}}th,td{{padding:10px 12px;border-bottom:1px solid #dfe6f0;text-align:left;vertical-align:top;overflow-wrap:anywhere}}
th{{width:31%;color:#52647b;font-weight:600;background:#f8fafc}}li{{margin-bottom:6px}}
footer{{margin-top:24px;color:#67778c;font-size:12px}}@media print{{body{{background:#fff}}main{{margin:0;border:0;padding:0}}}}
</style></head><body><main>
<h1>PDF 数字签名技术核验报告</h1>
<div class="sub">qpdf Studio · 独立技术检查，不是发行机构证明书</div>
<div class="summary">
文件：<strong>{e(data.get('source'))}</strong><br>
发现数字签名：<strong>{e(data.get('count', 0))}</strong><br>
用户指定可信根：{e(data.get('trust_roots_supplied', 0))} 个<br>
证书网络检查：{e('已明确授权' if data.get('network_fetching') else '关闭（默认离线）')}
</div>
{''.join(sections)}
<h2>结论适用范围与限制</h2>
<ul>{limitations}</ul>
<footer>本报告从本地 PDF 结构及证书核验结果生成。证书主体名称并不单独证明文件业务内容的真实性。</footer>
</main></body></html>"""


def save_signature_html(report: dict[str, Any], destination: Path) -> Path:
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Verification report already exists; choose a new output name")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as file:
        file.write(signature_audit_html(report))
    return destination
