"""qpdf-backed read-only structural inventory and comparative analysis."""
from __future__ import annotations
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any
import pikepdf
from pypdf import PdfReader

def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def objref(value: Any) -> str | None:
    n, g = getattr(value, "objgen", (0, 0))
    if n and getattr(value, "is_indirect", False):
        return f"{n} {g} R"
    return None

def pdf_text(value: Any) -> str:
    """Show a PDF name or string. Bytes that are not UTF-8 stay as #HH, not a guessed character."""
    try:
        return str(value)
    except UnicodeError:
        try:
            raw = bytes(value)
        except (TypeError, ValueError):
            return ""
        return "".join(chr(byte) if 32 <= byte < 127 and byte != 0x23 else f"#{byte:02X}" for byte in raw)

def encode(value: Any, *, root: bool = False, depth: int = 0) -> Any:
    target = objref(value)
    if target and not root:
        return {"$ref": target}
    if depth > 40:
        return {"$depth_limited": True}  # 限制：对象嵌套超过深度后停止展开
    if isinstance(value, pikepdf.Stream):
        return encode(value.stream_dict, root=True, depth=depth + 1)
    if isinstance(value, pikepdf.Dictionary):
        pairs = [(pdf_text(key), encode(item, depth=depth + 1)) for key, item in value.items()]
        return dict(sorted(pairs))
    if isinstance(value, pikepdf.Array):
        return [encode(item, depth=depth + 1) for item in value]
    if isinstance(value, pikepdf.Name):
        return {"$name": pdf_text(value)}
    if isinstance(value, pikepdf.String):
        return pdf_text(value)
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value), "sha256": hash_bytes(bytes(value))}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)

def signature_inventory(pdf: pikepdf.Pdf) -> list[dict[str, Any]]:
    acroform = pdf.Root.get("/AcroForm")
    if acroform is None:
        return []
    pending = list(acroform.get("/Fields", []))
    seen = set()
    found = []
    while pending:
        field = pending.pop(0)
        reference = objref(field)
        if reference in seen and reference is not None:
            continue
        if reference:
            seen.add(reference)
        pending.extend(field.get("/Kids", []))
        if str(field.get("/FT", "")) != "/Sig":
            continue
        signature = field.get("/V")
        row = {"field": pdf_text(field.get("/T", "")), "field_ref": reference, "signed": signature is not None,
               "crypto_verification": "not_performed", "identity_verification": "not_performed"}
        if signature:
            row["byte_range"] = [int(v) for v in signature.get("/ByteRange", [])]
            row["subfilter"] = pdf_text(signature.get("/SubFilter", ""))
            if signature.get("/Contents") is not None:
                contents = bytes(signature["/Contents"])
                row["contents_bytes"] = len(contents)
                row["contents_sha256"] = hash_bytes(contents)
        found.append(row)
    return found

def inspect_pdf(path: Path, destination: Path, streams: bool = False,
                operators: bool = False, text_geometry: bool = False,
                assets: bool = False) -> dict[str, Any]:
    source = path.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Input must be a PDF file")
    if destination.resolve() == source:
        raise ValueError("Cannot overwrite original PDF")  # 限制：不能覆盖原始 PDF
    destination.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    result: dict[str, Any] = {
        "schema": "qpdf-studio/0.1",
        "source": {"filename": source.name, "bytes": len(raw), "sha256": hash_bytes(raw)},
        "raw_eof_offsets": [match.start() for match in re.finditer(rb"%%EOF", raw)],
        "objects": [], "pages": [], "signatures": [], "warnings": [],
    }
    if streams:
        (destination / "streams").mkdir(exist_ok=True)
    if assets:
        (destination / "assets").mkdir(exist_ok=True)
    with pikepdf.Pdf.open(str(source)) as pdf:
        result["pdf_version"] = str(pdf.pdf_version)
        result["trailer"] = encode(pdf.trailer, root=True)
        result["signatures"] = signature_inventory(pdf)
        for obj in pdf.objects:
            item: dict[str, Any] = {"reference": objref(obj), "structure": encode(obj, root=True)}
            if isinstance(obj, pikepdf.Stream):
                item["stream_data"] = {}
                for mode in ("raw", "decoded"):
                    try:
                        binary = obj.read_raw_bytes() if mode == "raw" else obj.read_bytes()
                        info = {"bytes": len(binary), "sha256": hash_bytes(binary)}
                        if streams:
                            label = f"object_{obj.objgen[0]}_{obj.objgen[1]}_{mode}.bin"
                            (destination / "streams" / label).write_bytes(binary)
                            info["exported_path"] = "streams/" + label
                        item["stream_data"][mode] = info
                    except Exception as error:
                        item["stream_data"][mode] = {"error": f"{type(error).__name__}: {error}"}
            result["objects"].append(item)
        for number, page in enumerate(pdf.pages, 1):
            page_info: dict[str, Any] = {
                "number": number, "reference": objref(page.obj),
                "mediabox": encode(page.obj.get("/MediaBox", [])),
                "contents": encode(page.obj.get("/Contents")),
                "resources": encode(page.obj.get("/Resources", pikepdf.Dictionary())),
                "annotations": encode(page.obj.get("/Annots", [])),
            }
            resources = page.resources
            page_info["fonts"] = []
            for name, font in resources.get("/Font", {}).items():
                try:
                    descriptor = font.get("/FontDescriptor", {})
                    page_info["fonts"].append({
                        "resource": str(name),
                        "object": objref(font),
                        "subtype": str(font.get("/Subtype", "")),
                        "base_font": str(font.get("/BaseFont", "")),
                        "encoding": encode(font.get("/Encoding")),
                        "font_descriptor": encode(descriptor),
                    })
                except Exception as exc:
                    page_info["fonts"].append({"resource": str(name), "error": str(exc)})
            page_info["images"] = []
            for name, image_obj in page.get_images().items():
                entry: dict[str, Any] = {
                    "resource": str(name), "object": objref(image_obj),
                    "pixel_width": int(image_obj.get("/Width", 0)),
                    "pixel_height": int(image_obj.get("/Height", 0)),
                    "colorspace": encode(image_obj.get("/ColorSpace")),
                    "filters": encode(image_obj.get("/Filter")),
                }
                if assets:
                    try:
                        extracted = pikepdf.PdfImage(image_obj).extract_to(
                            fileprefix=str(destination / "assets" / f"p{number:04d}_image{len(page_info['images']):03d}")
                        )
                        entry["exported_file"] = Path(extracted).name
                    except Exception as exc:
                        entry["extract_error"] = f"{type(exc).__name__}: {exc}"
                page_info["images"].append(entry)
            if operators:
                try:
                    page_info["content_operators"] = [
                        {"operator": str(op.operator), "operands": [encode(v) for v in op.operands]}
                        for op in pikepdf.parse_content_stream(page)
                    ]
                except Exception as exc:
                    page_info["content_operators_error"] = f"{type(exc).__name__}: {exc}"
            result["pages"].append(page_info)
    try:
        reader = PdfReader(str(source), strict=False)
        for index, page in enumerate(reader.pages):
            try:
                if text_geometry:
                    text_fragments: list[dict[str, Any]] = []
                    def visitor(text, user_matrix, text_matrix, font, font_size):
                        if text:
                            text_fragments.append({
                                "text": text,
                                "user_matrix": [float(x) for x in user_matrix],
                                "text_matrix": [float(x) for x in text_matrix],
                                "font_size": float(font_size),
                                "base_font": str(font.get("/BaseFont", "")) if font else "",
                                "coordinates_note": "Matrices in PDF user space, not guaranteed glyph bounding boxes",
                            })
                    result["pages"][index]["text"] = page.extract_text(visitor_text=visitor) or ""
                    result["pages"][index]["text_fragments"] = text_fragments
                else:
                    result["pages"][index]["text"] = page.extract_text() or ""
            except Exception as error:
                result["pages"][index]["text_error"] = str(error)
    except Exception as error:
        result["warnings"].append("Text extraction: " + str(error))
    result["page_count"] = len(result["pages"])
    result["object_count"] = len(result["objects"])
    (destination / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result

def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if a.get("schema") != "qpdf-studio/0.1" or b.get("schema") != "qpdf-studio/0.1":
        raise ValueError("Unsupported manifest format")
    left = {x["reference"]: x for x in a["objects"]}
    right = {x["reference"]: x for x in b["objects"]}
    text_diff = []
    for index in range(max(len(a["pages"]), len(b["pages"]))):
        t1 = a["pages"][index].get("text", "") if index < len(a["pages"]) else ""
        t2 = b["pages"][index].get("text", "") if index < len(b["pages"]) else ""
        if t1 != t2:
            text_diff.append({"page": index + 1, "diff": list(difflib.unified_diff(
                t1.splitlines(), t2.splitlines(), fromfile="A", tofile="B", lineterm=""))})
    return {
        "same_file_hash": a["source"]["sha256"] == b["source"]["sha256"],
        "only_in_a": sorted(set(left) - set(right)), "only_in_b": sorted(set(right) - set(left)),
        "changed_same_object_number": sorted(k for k in left.keys() & right.keys() if left[k] != right[k]),
        "page_text_differences": text_diff, "signature_inventory_a": a["signatures"],
        "signature_inventory_b": b["signatures"],
        "note": "Object numbers are not stable across PDF rewrite. This is not an authenticity verdict.",
    }
