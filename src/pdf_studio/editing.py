"""PDF document property editing into separate output files.

Uses the existing pikepdf/libqpdf engine. Outputs are always separate files.
This module intentionally does not erase or forge signed revisions.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pikepdf

from .core import pdf_text as _pdf_info_text, signature_inventory

EDITABLE_PROPERTIES = ("Title", "Author", "Subject", "Keywords", "Creator")


def page_contents_ready(page: pikepdf.Page) -> bool:
    """Accept one content stream or several streams on the same page.

    Later writers replace only this page's /Contents with one new stream.
    Shared streams used by other pages stay unchanged.
    """
    contents = page.obj.get("/Contents")
    if isinstance(contents, pikepdf.Stream):
        return True
    if isinstance(contents, pikepdf.Array) and len(contents) >= 1:
        return all(isinstance(part, pikepdf.Stream) for part in contents)
    return False


def document_properties(source: Path) -> dict[str, str]:
    """Read commonly used Info dictionary keys (never signatory identity)."""
    with pikepdf.Pdf.open(source.resolve(strict=True)) as pdf:
        info = pdf.docinfo
        return {key: _pdf_info_text(info.get("/" + key, "")) for key in EDITABLE_PROPERTIES}


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _signed_or_certified(pdf: pikepdf.Pdf) -> bool:  # 限制：判断这份 PDF 是否已签名或已认证
    if signature_inventory(pdf):  # 限制：签名清单里已有签名，就视为已签名
        return True  # 限制：命中后返回真
    if pdf.Root.get("/Perms") is not None:  # 限制：存在文档权限字典时，视为已认证
        return True  # 限制：命中后返回真
    for obj in pdf.objects:  # 限制：继续逐个对象查找签名结构
        if not isinstance(obj, pikepdf.Dictionary):  # 限制：不是字典的对象不参与签名判断
            continue  # 限制：跳过非字典对象
        if str(obj.get("/Type", "")) == "/Sig" or obj.get("/ByteRange") is not None:  # 限制：对象类型是签名，或带有签名字节范围
            return True  # 限制：命中后返回真
        if str(obj.get("/FT", "")) == "/Sig":  # 限制：表单域类型是签名域
            return True  # 限制：命中后返回真
    return False  # 限制：以上都没有命中，才不算已签名或已认证


def has_byte_range_signature(pdf: pikepdf.Pdf) -> bool:
    """True when a completed signature still records a ByteRange."""
    if any(row.get("byte_range") for row in signature_inventory(pdf)):
        return True
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) and "/ByteRange" in obj and "/Contents" in obj:
            return True
    return False


def _prefix_matches(source: Path, destination: Path) -> bool:
    size = source.stat().st_size
    with source.open("rb") as original, destination.open("rb") as revised:
        remaining = size
        while remaining:
            block = min(1024 * 1024, remaining)
            if original.read(block) != revised.read(block):
                return False
            remaining -= block
    return True


def _append_revised_page(source: Path, edited: Path, destination: Path, page: int) -> None:
    """Copy the original bytes, then append one page's new contents and resources."""
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader

    with source.open("rb") as src_f, edited.open("rb") as edit_f, destination.open("xb") as out:
        writer = IncrementalPdfFileWriter(src_f)
        edited_reader = PdfFileReader(edit_f)
        page_ref, _inherited = writer.find_page_for_modification(page - 1)
        current = page_ref.get_object()
        updated_ref, _edited_resources = edited_reader.find_page_for_modification(page - 1)
        updated = updated_ref.get_object()
        if "/Contents" not in updated:
            raise ValueError("编辑结果没有页面内容流。")
        current[generic.pdf_name("/Contents")] = writer.import_object(updated.raw_get("/Contents"))
        if "/Resources" in updated:
            current[generic.pdf_name("/Resources")] = writer.import_object(updated.raw_get("/Resources"))
        writer.mark_update(page_ref)
        writer.write(out)
    if not _prefix_matches(source, destination):
        raise ValueError("原稿字节保留核对失败。")


def save_edited_pdf(source: Path, pdf: pikepdf.Pdf, destination: Path, *, page: int) -> bool:
    """Save an edited document without moving an existing signature's bytes.

    A completed signature records a ByteRange. pikepdf's full save rewrites the
    file and moves /Contents, so that signature then fails structure and digest
    checks. This keeps the original bytes as a prefix and appends the edited
    page. The old signature still covers only the revision it signed. Unsigned
    files keep the normal full save. Returns whether the original bytes were kept.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination == source or destination.exists():
        raise ValueError("必须另存为不存在的新 PDF，不能覆盖原稿。")
    if not 1 <= page <= len(pdf.pages):
        raise ValueError("页码超出文档范围。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not has_byte_range_signature(pdf):
        try:
            with destination.open("xb") as outgoing:
                pdf.save(outgoing)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return False
    fd, name = tempfile.mkstemp(prefix="qpdf-page-", suffix=".pdf")
    os.close(fd)
    temporary = Path(name)
    try:
        pdf.save(temporary)
        _append_revised_page(source, temporary, destination, page)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise ValueError("已有签名的文件没能按原字节增量保存，未交付整文件重写。" + str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return True


def edit_properties(source: Path, destination: Path, values: dict[str, str]) -> dict:
    """Update selected standard Info fields and save a separate PDF.

    Signature presence does not block this operation. Inputs are not overwritten.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination == source or destination.exists():
        raise ValueError("Output must be a new filename; the source will not be overwritten")  # 限制：文档属性必须另存为新文件，不能覆盖原稿
    if not values or any(key not in EDITABLE_PROPERTIES for key in values):
        raise ValueError("Only standard PDF Info properties may be changed")  # 限制：只能修改标准 PDF 信息属性
    if any(not isinstance(value, str) or len(value) > 4096 for value in values.values()):
        raise ValueError("PDF Info property must be a string of no more than 4096 characters")
    changed: dict[str, dict[str, str]] = {}
    with pikepdf.Pdf.open(source) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDFs are not modified by this editor")  # 限制：加密 PDF 不能用这个编辑器修改
        info = pdf.docinfo
        for key, new_value in values.items():
            pdf_key = "/" + key
            old_value = _pdf_info_text(info.get(pdf_key, ""))
            if old_value != new_value:
                changed[key] = {"before": old_value, "after": new_value}
                if new_value:
                    info[pdf_key] = pikepdf.String(new_value)
                elif pdf_key in info:
                    del info[pdf_key]
        if not changed:
            raise ValueError("No properties changed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf.save(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    report = {
        "operation": "edit-pdf-properties",
        "source_filename": source.name,
        "source_sha256": _file_hash(source),
        "output_filename": destination.name,
        "output_sha256": _file_hash(destination),
        "properties_changed": changed,
        "signature_handling": "Signature presence does not block editing; output is saved separately",
        "warning": "Edited output is a new PDF, not a newly authenticated original",
    }
    return report
