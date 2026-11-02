"""Page-level operations backed by libqpdf, with explicit change provenance."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import pikepdf


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def transform_pages(source: Path, target: Path, *, pages: list[int] | None = None,
                    rotate: int = 0) -> dict:
    """Extract/reorder selected pages (1-indexed) and optionally rotate a new PDF.

    Signature presence does not block page operations. Inputs are not overwritten.
    """
    source = source.resolve(strict=True)
    target = target.resolve()
    if source == target or target.exists():
        raise ValueError("Choose a new, unused output path")  # 限制：页面操作必须输出到新的未使用路径
    if rotate not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0, 90, 180 or 270")
    with pikepdf.Pdf.open(source) as pdf:
        selected = list(range(1, len(pdf.pages) + 1)) if pages is None else list(pages)
        if not selected:
            raise ValueError("At least one page is required")
        if len(selected) > 10000 or any(not 1 <= n <= len(pdf.pages) for n in selected):
            raise ValueError("Page number outside valid range")
        with pikepdf.Pdf.new() as out:
            for page_num in selected:
                out.pages.append(pdf.pages[page_num - 1])
                if rotate:
                    page = out.pages[-1]
                    current_rotation = int(page.obj.get("/Rotate", 0))
                    page.obj["/Rotate"] = (current_rotation + rotate) % 360
            target.parent.mkdir(parents=True, exist_ok=True)
            out.save(target)
    result = {
        "operation": "page-transform",
        "source": str(source.name),
        "source_sha256": _sha(source),
        "output": str(target.name),
        "output_sha256": _sha(target),
        "selected_original_pages": selected,
        "rotate_clockwise_degrees": rotate,
        "notes": ["Input PDF remains unchanged", "Output is a new document, not an original issuer-signed revision"],  # 限制：页面整理结果是新文件，不是原签署者的签名版本
    }
    return result


def combine_documents(documents: list[Path], target: Path) -> dict:
    """Combine PDFs into a separate output file."""
    if not documents or len(documents) > 100:
        raise ValueError("Provide between 1 and 100 PDFs")
    target = target.resolve()
    if target.exists() or any(target == p.resolve(strict=True) for p in documents):
        raise ValueError("Output must be a new file and must not replace an input")  # 限制：合并结果必须是新文件，不能替换输入文件
    originals = []
    with pikepdf.Pdf.new() as output:
        for input_path in documents:
            source = input_path.resolve(strict=True)
            with pikepdf.Pdf.open(source) as pdf:
                originals.append({"filename": source.name, "sha256": _sha(source), "pages": len(pdf.pages)})
                output.pages.extend(pdf.pages)
        target.parent.mkdir(parents=True, exist_ok=True)
        output.save(target)
    report = {
        "operation": "merge",
        "inputs": originals,
        "output": target.name,
        "output_sha256": _sha(target),
        "notes": ["Input PDFs remain unchanged", "Merged output is a new document"],
    }
    return report
