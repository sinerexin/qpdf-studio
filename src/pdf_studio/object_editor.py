"""Conservative direct editing of existing PDF image/vector drawing objects.

Image edit: replace RGB pixels within the selected *existing* Image XObject
stream, only when the resource is used by one Do operation in the document.
Vector edit: change the existing rg operands of an isolated q/rg/re/f/Q path.
Neither operation adds a white cover, overlay, new paint instruction or raster
representation of an entire page.
"""
from __future__ import annotations

import hashlib
import math
import zlib
from pathlib import Path

import pikepdf
from PIL import Image

from .editing import _signed_or_certified, page_contents_ready

MAX_IMAGE_PIXELS = 16_000_000
_DRAW_END = {"f", "F", "f*"}


def _finite(values) -> list[float]:
    numbers = [float(x) for x in values]
    if not all(math.isfinite(x) for x in numbers):
        raise ValueError("Non-finite PDF drawing operands")
    return numbers


def _image_resource(page: pikepdf.Page, name: str):
    resources = page.resources.get("/XObject", {})
    image = resources.get(name)
    if image is None or not isinstance(image, pikepdf.Stream):
        raise ValueError("Drawing references an unavailable XObject")
    if str(image.get("/Subtype", "")) != "/Image":
        raise ValueError("Nested Form XObjects are not eligible image targets")  # 限制：嵌套 Form XObject 不能当图片替换目标
    if image.get("/ImageMask") or image.get("/SMask") or image.get("/Mask") is not None:
        raise ValueError("Image masks and transparency are not supported for direct pixel replacement")  # 限制：带遮罩或透明的图片不能直接替换像素
    if image.get("/Decode") is not None or image.get("/DecodeParms") is not None:
        raise ValueError("Custom image decode maps/predictors require specialized editing")
    if str(image.get("/ColorSpace", "")) != "/DeviceRGB":
        raise ValueError("Only simple DeviceRGB Image XObjects are supported")  # 限制：直接替换图片只支持简单的 DeviceRGB 图像
    if int(image.get("/BitsPerComponent", 0)) != 8:
        raise ValueError("Only 8-bit RGB Image XObjects are supported")  # 限制：直接替换图片只支持 8 位 RGB
    width, height = int(image.get("/Width", 0)), int(image.get("/Height", 0))
    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image pixel dimensions outside supported limits")  # 限制：图片像素尺寸超出允许范围
    return image, width, height


def _image_usages(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    """Count all page Do usages by indirect XObject identity, not resource name."""
    usage: dict[tuple[int, int], int] = {}
    for sheet in pdf.pages:
        xobjects = sheet.resources.get("/XObject", {})
        for entry in xobjects.values():
            if isinstance(entry, pikepdf.Stream) and str(entry.get("/Subtype", "")) == "/Form":
                raise ValueError("Documents with Form XObjects require nested resource analysis")
        for command in pikepdf.parse_content_stream(sheet):
            if str(command.operator) != "Do" or len(command.operands) != 1:
                continue
            selected = xobjects.get(str(command.operands[0]))
            if not isinstance(selected, pikepdf.Stream):
                raise ValueError("Unexpected XObject resource in PDF drawing stream")
            ref = tuple(selected.objgen)
            if ref == (0, 0):
                raise ValueError("Direct anonymous XObjects cannot be counted safely")  # 限制：匿名 XObject 不能安全计数
            usage[ref] = usage.get(ref, 0) + 1
    return usage


def _page_coordinates_supported(sheet: pikepdf.Page, commands: list) -> bool:
    """Page glyph/image hit testing assumes an unrotated, uncropped canvas."""
    page = sheet.obj
    for attribute in ("/Rotate", "/CropBox"):
        cursor = page
        for _ in range(40):
            entry = cursor.get(attribute)
            if entry is not None:
                if attribute == "/Rotate" and int(entry) % 360 != 0:
                    return False
                if attribute == "/CropBox":
                    if [float(x) for x in entry] != [float(x) for x in sheet.MediaBox]:
                        return False
                break
            cursor = cursor.get("/Parent")
            if cursor is None:
                break
    for index, command in enumerate(commands):
        if str(command.operator) != "cm":
            continue
        matrix = _finite(command.operands)
        if matrix == [1.,0.,0.,1.,0.,0.]:
            continue
        # Only an isolated q/cm/Do/Q may provide a placement transform.
        if (index < 1 or index+2 >= len(commands)
                or [str(c.operator) for c in commands[index-1:index+3]]
                    != ["q","cm","Do","Q"]):
            return False
    return True


def list_editable_objects(source: Path, page: int) -> dict:
    """List exact candidates with native page coordinates and operator index."""
    src = Path(source).resolve(strict=True)
    with pikepdf.Pdf.open(src) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ValueError("Page outside document")
        sheet = pdf.pages[page - 1]
        commands = list(pikepdf.parse_content_stream(sheet))
        items: list[dict] = []
        supported_coords = _page_coordinates_supported(sheet, commands)
        try:
            usages = _image_usages(pdf)
            image_error = None
        except (ValueError, TypeError, KeyError, pikepdf.PdfError) as error:
            usages = {}
            image_error = str(error)
        for index in range(len(commands)):
            ins = commands[index]
            if str(ins.operator) == "Do" and len(ins.operands) == 1:
                name = str(ins.operands[0])
                if index < 2 or index + 1 >= len(commands):
                    continue
                block = commands[index-2:index+2]
                if [str(x.operator) for x in block] != ["q", "cm", "Do", "Q"]:
                    continue
                try:
                    a,b,c,d,e,f = _finite(block[1].operands)
                    if b or c or a <= 0 or d <= 0:
                        continue
                    image,w,h = _image_resource(sheet,name)
                    used = usages.get(tuple(image.objgen), 0)
                    items.append({
                        "kind":"image", "operator_index":index,
                        "xobject_ref":f"{image.objgen[0]} {image.objgen[1]} R",
                        "resource":name, "image_pixels":[w,h],
                        "rect_pdf":[e,f,e+a,f+d],
                        "editable":supported_coords and image_error is None and used == 1,
                        "use_count":used,
                        "reason":"Unsupported page rotation/crop/transformation" if not supported_coords else
                                  image_error if image_error else
                                  "Resource is reused" if used != 1 else "Single original RGB XObject",
                    })
                except (ValueError,TypeError,KeyError,pikepdf.PdfError):
                    continue
            if str(ins.operator) == "re" and index >= 2 and index+2<len(commands):
                block = commands[index-2:index+3]
                if [str(x.operator) for x in block] != ["q","rg","re","f","Q"]:
                    continue
                try:
                    r,g,b = _finite(block[1].operands)
                    x,y,w,h = _finite(ins.operands)
                    if any(v < 0 or v > 1 for v in (r,g,b)) or w<=0 or h<=0:
                        continue
                    items.append({
                        "kind":"vector-fill", "operator_index":index,
                        "color_operator_index":index-1,
                        "color_rgb":[round(r,6),round(g,6),round(b,6)],
                        "rect_pdf":[x,y,x+w,y+h], "editable":supported_coords,
                        "reason":"Original simple rectangle path q/rg/re/f/Q"
                                  if supported_coords else "Unsupported page rotation/crop/transformation",
                    })
                except (ValueError,TypeError):
                    continue
        return {
            "page":page, "source":src.name,
            "signed_or_certified":_signed_or_certified(pdf),  # 限制标记：为真时表示原文件已签名或已认证
            "objects":items,
            "note":"Only existing single-use RGB image resources and isolated fill rectangles; no cover or overlay",
        }


def _filehash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _validate_output(source: Path, target: Path, page: int,
                     index: int, kind: str):
    with pikepdf.Pdf.open(target) as pdf:
        assert 1 <= page <= len(pdf.pages)
        commands = list(pikepdf.parse_content_stream(pdf.pages[page-1]))
        if index >= len(commands):
            raise ValueError("Result drawing instruction was lost")
        actual = str(commands[index].operator)
        expected = "Do" if kind == "image" else "re"
        if actual != expected:
            raise ValueError("Result drawing instruction was unexpectedly rewritten")


def edit_existing_object(source: Path, destination: Path, *, page: int,
                         operator_index: int, kind: str,
                         replacement_image: Path | None = None,
                         fill_hex: str | None = None) -> dict:
    """Modify original drawing data or color operands into a new PDF copy."""
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if src == dst or dst.exists():
        raise ValueError("Output must be a new unused PDF name")  # 限制：对象修改必须另存为新的未使用 PDF
    with pikepdf.Pdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF editing requires password support, not provided by this operation")  # 限制：加密 PDF 不能用这个操作修改，本操作不提供密码解密
        inventory = list_editable_objects(src, page)
        candidate = next((item for item in inventory["objects"]
                          if item["kind"] == kind and item["operator_index"] == operator_index),None)
        if not candidate or not candidate["editable"]:
            raise ValueError("The existing image/vector object is not independently editable")
        sheet = pdf.pages[page-1]
        commands = list(pikepdf.parse_content_stream(sheet))
        if kind == "image":
            if replacement_image is None or fill_hex is not None:
                raise ValueError("Image object editing requires a replacement image file")
            asset = Path(replacement_image).resolve(strict=True)
            if asset.stat().st_size > 35_000_000:
                raise ValueError("Replacement image exceeds safe file-size limit")  # 限制：替换图片超过安全文件大小
            resource, width, height = _image_resource(sheet, candidate["resource"])
            with Image.open(asset) as picture:
                picture.load()
                if picture.mode != "RGB":
                    raise ValueError("Replacement must have RGB pixels and no transparency/alpha")
                if picture.size != (width,height):
                    raise ValueError("Replacement image pixel dimensions must exactly match existing PDF image")
                binary = picture.tobytes()
            if len(binary) != width*height*3:
                raise ValueError("RGB pixel bytes are malformed")
            prior = resource.read_bytes()
            if len(prior) != len(binary):
                raise ValueError("Original image decoded bytes do not match expected 8-bit RGB size")
            if prior == binary:
                raise ValueError("Replacement is identical to the original image")
            # The selected native XObject stream is changed in place, in this
            # newly saved PDF. No extra Do operator or covering rectangle.
            resource.write(zlib.compress(binary), filter=pikepdf.Name("/FlateDecode"))
            after = {"old_image_sha256":hashlib.sha256(prior).hexdigest(),
                     "new_image_sha256":hashlib.sha256(binary).hexdigest(),
                     "image_pixels":[width,height], "resource":candidate["resource"],
                     "xobject_ref":candidate["xobject_ref"]}
        elif kind == "vector-fill":
            if replacement_image is not None or not isinstance(fill_hex,str):
                raise ValueError("Vector fill requires a hex RGB color")
            import re
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}",fill_hex):
                raise ValueError("Fill color must use #RRGGBB")
            replacement = [int(fill_hex[i:i+2],16)/255 for i in (1,3,5)]
            previous = candidate["color_rgb"]
            if all(abs(a-b)<1/510 for a,b in zip(previous,replacement)):
                raise ValueError("New vector fill color is identical to the original")
            if not page_contents_ready(sheet):
                raise ValueError("This page has no usable content stream")
            color_index = candidate["color_operator_index"]
            original = commands[color_index]
            commands[color_index] = pikepdf.ContentStreamInstruction(
                [pikepdf.Real(value) for value in replacement], original.operator,
            )
            sheet.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(commands))
            after = {"original_rgb":previous,"new_rgb":replacement,"new_color":fill_hex.upper()}
        else:
            raise ValueError("Unsupported object edit kind")
        dst.parent.mkdir(parents=True,exist_ok=True)
        try:
            with dst.open("xb") as out:
                pdf.save(out)
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    try:
        _validate_output(src,dst,page,operator_index,kind)
        if kind == "image":
            with pikepdf.Pdf.open(dst) as verified:
                resource = verified.pages[page-1].resources["/XObject"][candidate["resource"]]
                if hashlib.sha256(resource.read_bytes()).hexdigest() != after["new_image_sha256"]:
                    raise ValueError("Saved Image XObject data does not match replacement")
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    report={
        "operation":"edit-native-PDF-image-or-vector-object",
        "kind":kind,"page":page,"operator_index":operator_index,
        "original":src.name,"original_sha256":_filehash(src),
        "saved":dst.name,"saved_sha256":_filehash(dst),
        "rect_pdf":candidate["rect_pdf"],
        "changes":after,
        "method":"Existing PDF object/operand rewritten; no added overlay, mask or paint instruction",
        "limitations":"Only single-use flat RGB images or isolated q/rg/re/f/Q vector rectangles",
    }
    return report
