"""PDF note annotations saved into a separate output file.

Annotations are distinct from page contents. This tool never obscures, redacts,
overwrites the source PDF. Signature presence does not block annotation output.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf



def _hash_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def add_note(source: Path, target: Path, *, page_number: int,
             x: float, y: float, contents: str, author: str = "PDF Studio",
             width: float = 24, height: float = 24) -> dict:
    """Append a visible PDF /Text note to a NEW PDF and produce an audit record.

    Coordinates are in PDF default user-space units (bottom-left origin).
    The annotations' location can differ with page rotation/crop boxes.
    """
    source = source.resolve(strict=True)
    target = target.resolve()
    if source == target or target.exists():
        raise ValueError("The output must be a new file; original PDF is not overwritten")  # 限制：批注必须写入新文件，不能覆盖原 PDF
    if not contents.strip() or len(contents) > 5000 or len(author) > 250:
        raise ValueError("Note text must be 1–5000 characters; author limited to 250")  # 限制：批注正文最多 5000 字，作者名最多 250 字
    coords = (x, y, width, height)
    if any(not isinstance(v, (int, float)) or abs(v) > 1_000_000 for v in coords):
        raise ValueError("Invalid annotation coordinates")
    if width <= 0 or height <= 0:
        raise ValueError("Annotation dimensions must be positive")
    with pikepdf.Pdf.open(source) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing is not supported")  # 限制：加密 PDF 不支持批注编辑
        if page_number < 1 or page_number > len(pdf.pages):
            raise ValueError("Page number is outside the document")
        page = pdf.pages[page_number - 1]
        bounds = [float(v) for v in page.MediaBox]
        if x < bounds[0] or y < bounds[1] or x + width > bounds[2] or y + height > bounds[3]:
            raise ValueError("Note rectangle is outside the page MediaBox")
        note = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Text"),
            "/Rect": pikepdf.Array([x, y, x + width, y + height]),
            "/Contents": pikepdf.String(contents),
            "/T": pikepdf.String(author),
            "/Name": pikepdf.Name("/Note"),
            "/F": 4,  # printable
        })
        annotation = pdf.make_indirect(note)
        existing = page.obj.get("/Annots")
        if existing is None:
            page.obj["/Annots"] = pikepdf.Array([annotation])
        else:
            existing.append(annotation)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf.save(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    report = {
        "operation": "add-note-annotation",
        "input_filename": source.name,
        "input_sha256": _hash_file(source),
        "output_filename": target.name,
        "output_sha256": _hash_file(target),
        "page": page_number,
        "rectangle": [x, y, x + width, y + height],
        "note": contents,
        "author": author,
        "integrity_warning": "This is a new annotated PDF, not an original signed issuer document",  # 限制：批注结果是新文件，不是原签署者的签名原件
    }
    return report
