"""Actual low-level Image XObject and vector path editing, with PDFium proof."""
from pathlib import Path
import hashlib

import pikepdf
import pytest
import pypdfium2 as pdfium
from PIL import Image, ImageChops
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from pdf_studio.object_editor import list_editable_objects, edit_existing_object
from pdf_studio.keygen import generate_personal_credentials
from pdf_studio.signing import sign_document


def _image_document(path: Path, *, duplicate: bool = False) -> None:
    canvas=Canvas(str(path),pagesize=(612,792))
    pattern=Image.new("RGB",(30,26),(220,60,40))
    canvas.drawImage(ImageReader(pattern),90,510,width=150,height=130)
    if duplicate:
        canvas.drawImage(ImageReader(pattern),300,510,width=100,height=100)
    canvas.drawString(80,690,"Unchanged ordinary PDF text")
    canvas.save()


def _vector_document(path: Path):
    base=path.parent/"vector-base.pdf"
    c=Canvas(str(base),pagesize=(612,792))
    c.drawString(80,670,"Vector background test")
    c.save()
    with pikepdf.Pdf.open(base) as doc:
        page=doc.pages[0]
        stream=page.obj["/Contents"].read_bytes()
        # Existing native path, not a painted cover for text replacement.
        page.obj["/Contents"]=doc.make_stream(
            stream+b"\nq 0.85 0.12 0.25 rg 80 490 140 95 re f Q\n"
        )
        doc.save(path)


def _render(path: Path):
    pdf=pdfium.PdfDocument(str(path))
    try:
        page=pdf[0]
        try:
            return page.render(scale=1.0).to_pil().convert("RGB")
        finally:page.close()
    finally:pdf.close()


def test_replace_existing_single_use_xobject_rgb_pixels(tmp_path: Path) -> None:
    src,dst=tmp_path/"original.pdf",tmp_path/"edited.pdf"
    _image_document(src)
    before=src.read_bytes()
    inv=list_editable_objects(src,1)
    image=next(entry for entry in inv["objects"] if entry["kind"]=="image")
    assert image["editable"] and image["image_pixels"]==[30,26],inv
    assert image["rect_pdf"]==[90.0,510.0,240.0,640.0]
    asset=tmp_path/"new-color.png"
    Image.new("RGB",(30,26),(20,170,220)).save(asset)
    before_picture=_render(src)
    result=edit_existing_object(
        src,dst,page=1,operator_index=image["operator_index"],
        kind="image",replacement_image=asset,
    )
    assert src.read_bytes()==before
    assert result["changes"]["image_pixels"]==[30,26]
    assert result["saved_sha256"]!=result["original_sha256"]
    assert not (tmp_path/"edited.pdf.changes.json").is_file()
    with pikepdf.Pdf.open(src) as a,pikepdf.Pdf.open(dst) as b:
        old=list(pikepdf.parse_content_stream(a.pages[0]))
        new=list(pikepdf.parse_content_stream(b.pages[0]))
        assert [str(i.operator) for i in new]==[str(i.operator) for i in old]
        assert [list(map(str,inst.operands)) for inst in new]==[
            list(map(str,inst.operands)) for inst in old
        ]
        xa=a.pages[0].resources["/XObject"][image["resource"]]
        xb=b.pages[0].resources["/XObject"][image["resource"]]
        assert hashlib.sha256(xa.read_bytes()).hexdigest()!=hashlib.sha256(xb.read_bytes()).hexdigest()
        assert xb.read_bytes()==Image.open(asset).tobytes()
    after_picture=_render(dst)
    assert ImageChops.difference(before_picture,after_picture).getbbox()
    assert after_picture.getpixel((130,350))==before_picture.getpixel((130,350))
    assert after_picture.getpixel((300,200))==before_picture.getpixel((300,200))
    assert after_picture.getpixel((150,230))==(20,170,220) # image spans Y 152..282 in top-down


def test_simple_vector_rect_changes_existing_color_only(tmp_path: Path) -> None:
    src,dst=tmp_path/"vector.pdf",tmp_path/"vector-edited.pdf"
    _vector_document(src)
    row=next(entry for entry in list_editable_objects(src,1)["objects"] if entry["kind"]=="vector-fill")
    assert row["editable"] and row["rect_pdf"]==[80,490,220,585]
    report=edit_existing_object(
        src,dst,page=1,operator_index=row["operator_index"],
        kind="vector-fill",fill_hex="#0088FF",
    )
    assert report["changes"]["new_color"]=="#0088FF"
    with pikepdf.Pdf.open(src) as a,pikepdf.Pdf.open(dst) as b:
        prior=list(pikepdf.parse_content_stream(a.pages[0]))
        after=list(pikepdf.parse_content_stream(b.pages[0]))
        assert [str(op.operator) for op in prior]==[str(op.operator) for op in after]
        assert all(str(op.operator)!="Do" for op in after)
        assert list(map(str,prior[row["operator_index"]].operands))==list(map(str,after[row["operator_index"]].operands))
    assert ImageChops.difference(_render(src),_render(dst)).getbbox()


def test_shared_image_rejected_no_new_pdf(tmp_path: Path) -> None:
    src=tmp_path/"shared.pdf";_image_document(src,duplicate=True)
    items=list_editable_objects(src,1)["objects"]
    image=next(item for item in items if item["kind"]=="image")
    assert not image["editable"] and image["use_count"]==2
    png=tmp_path/"new.png";Image.new("RGB",(30,26),(5,8,11)).save(png)
    with pytest.raises(ValueError,match="not independently editable"):
        edit_existing_object(src,tmp_path/"bad.pdf",page=1,
                             operator_index=image["operator_index"],kind="image",replacement_image=png)
    assert not (tmp_path/"bad.pdf").exists()


def test_mismatched_dimensions_or_transparency_rejected(tmp_path: Path) -> None:
    src=tmp_path/"original.pdf";_image_document(src)
    image=next(x for x in list_editable_objects(src,1)["objects"] if x["kind"]=="image")
    wrong=tmp_path/"wrong.png";Image.new("RGB",(31,26),"red").save(wrong)
    with pytest.raises(ValueError,match="dimensions"):
        edit_existing_object(src,tmp_path/"not-made.pdf",page=1,
                             operator_index=image["operator_index"],kind="image",replacement_image=wrong)
    alpha=tmp_path/"alpha.png";Image.new("RGBA",(30,26),(80,90,100,120)).save(alpha)
    with pytest.raises(ValueError,match="no transparency"):
        edit_existing_object(src,tmp_path/"not-made.pdf",page=1,
                             operator_index=image["operator_index"],kind="image",replacement_image=alpha)


def test_signed_pdf_allows_image_edit_to_separate_output(tmp_path: Path) -> None:
    src=tmp_path/"original.pdf";_image_document(src)
    password="Synthetic test-only password for signing"
    credentials=tmp_path/"credentials"
    generate_personal_credentials(credentials,label="Private Image Unit Test",password=password,algorithm="ECDSA-P256")
    signed=sign_document(src,tmp_path/"signed.pdf",
                         pfx=credentials/"personal-signing.p12",password=password.encode())
    item=next(x for x in list_editable_objects(signed,1)["objects"] if x["kind"]=="image")
    asset=tmp_path/"valid.png";Image.new("RGB",(30,26),(15,20,30)).save(asset)
    edit_existing_object(signed,tmp_path/"refused.pdf",page=1,
                         operator_index=item["operator_index"],kind="image",replacement_image=asset)
    assert (tmp_path/"refused.pdf").exists()


def test_rotated_or_cropped_pages_not_offered_as_safe_coordinates(tmp_path: Path) -> None:
    source=tmp_path/"original.pdf"
    _image_document(source)
    with pikepdf.Pdf.open(source) as doc:
        doc.pages[0].obj["/Rotate"]=90
        doc.save(tmp_path/"rotated.pdf")
    with pikepdf.Pdf.open(source) as doc:
        doc.pages[0].obj["/CropBox"]=pikepdf.Array([0,0,595,775])
        doc.save(tmp_path/"cropped.pdf")
    for filename in ("rotated.pdf","cropped.pdf"):
        info=list_editable_objects(tmp_path/filename,1)
        assert not any(item["editable"] for item in info["objects"]),filename
        selected=next(entry for entry in info["objects"] if entry["kind"]=="image")
        assert "Unsupported page" in selected["reason"]
    pixel=tmp_path/"new.png";Image.new("RGB",(30,26),(14,20,90)).save(pixel)
    row=next(entry for entry in list_editable_objects(tmp_path/"rotated.pdf",1)["objects"]
             if entry["kind"]=="image")
    with pytest.raises(ValueError,match="not independently editable"):
        edit_existing_object(tmp_path/"rotated.pdf",tmp_path/"bad.pdf",
                             page=1,operator_index=row["operator_index"],
                             kind="image",replacement_image=pixel)
    assert not (tmp_path/"bad.pdf").exists()
