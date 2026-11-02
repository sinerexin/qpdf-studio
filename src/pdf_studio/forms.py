"""Filling ordinary AcroForm text fields into a separate output file."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf
from pypdf import PdfReader, PdfWriter



def text_fields(source: Path) -> dict[str, dict]:
    source = source.resolve(strict=True)
    reader = PdfReader(str(source), strict=False)
    fields = reader.get_fields() or {}
    result: dict[str, dict] = {}
    for name, details in fields.items():
        if str(details.get("/FT", "")) != "/Tx":  # 限制：只列出普通文字域，按钮、签名域和 XFA 不进入表单编辑
            continue  # 限制：非文字域直接跳过，不显示也不填写
        flags = int(details.get("/Ff", 0))
        result[str(name)] = {
            "value": str(details.get("/V", "")),
            "read_only": bool(flags & 1),
            "tooltip": str(details.get("/TU", "")),
        }
    return result


def fill_text_fields(source: Path, target: Path, updates: dict[str, str]) -> dict:
    """Fill an AcroForm's text fields; never modify its source in-place."""
    source = source.resolve(strict=True)
    target = target.resolve()
    if target == source or target.exists():
        raise ValueError("Output must be new and separate from source PDF")
    if not updates:
        raise ValueError("No text fields supplied")
    if len(updates) > 500 or any(not isinstance(v, str) or len(v) > 10000
                                 for v in updates.values()):
        raise ValueError("Text field update count/length limit exceeded")  # 限制：表单字段数量或长度超过上限
    with pikepdf.Pdf.open(source) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
    available = text_fields(source)
    for key in updates:
        field = available.get(key)
        if field is None:
            raise ValueError(f"Unknown or non-text AcroForm field: {key}")
        if field["read_only"]:
            raise ValueError(f"Read-only form field: {key}")
    changed = {key: {"before": available[key]["value"], "after": value}
               for key, value in updates.items() if available[key]["value"] != value}
    if not changed:
        raise ValueError("No field values changed")
    writer = PdfWriter(clone_from=str(source))
    writer.update_page_form_field_values(
        None, {name: value["after"] for name, value in changed.items()},
        auto_regenerate=False, flatten=False,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            writer.write(output)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    report = {
        "operation": "fill-acroform-text",
        "source": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output": target.name,
        "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "fields_changed": changed,
        "warning": "Filled copy is a new PDF; source is unchanged",
    }
    return report
