"""qpdf Studio command line interface."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .core import inspect_pdf, compare
from .signing import sign_document, verify_document
from .visual import render_document, visual_diff
from .deep import semantic_inventory
from .pages import transform_pages, combine_documents
from .bundle import analyze_complete
from .editing import edit_properties, document_properties
from .annotations import add_note
from .revisions import inspect_revisions
from .paint_diagnostics import inspect_paint_operations
from .object_editor import list_editable_objects, edit_existing_object
from .attachments import extract_attachments
from .forms import text_fields, fill_text_fields
from .signature_audit import audit_signatures
from .signature_profile import inspect_signature_profile, compare_signature_parameters
from .text_search import find_text
from .body_text import add_body_text
from .signature_report import save_signature_html
from .replace_text import (
    list_text_runs, replace_text_run, list_tj_spans, replace_tj_span,
)
from .adjacent_text import list_adjacent_text_spans, replace_adjacent_text_span
from .paragraph_reflow import list_reflow_blocks, reflow_text_object
from .flow_layout import reflow_with_following_text, plan_text_flow
from .original_layout import read_original_layout, reflow_like_original
from .keygen import ALGORITHMS, generate_personal_credentials, reissue_personal_certificate

def main() -> None:
    p = argparse.ArgumentParser(prog="qpdf-studio", description="qpdf-based structure inspection and PDF signing")
    sp = p.add_subparsers(dest="action", required=True)
    ins = sp.add_parser("inspect")
    ins.add_argument("pdf", type=Path)
    ins.add_argument("--out", type=Path, required=True)
    ins.add_argument("--streams", action="store_true")
    ins.add_argument("--operators", action="store_true", help="Parse page drawing/text operators")
    ins.add_argument("--text-geometry", action="store_true", help="Export text fragments and matrices")
    ins.add_argument("--assets", action="store_true", help="Extract image XObjects where supported")
    diff = sp.add_parser("compare")
    diff.add_argument("a", type=Path)
    diff.add_argument("b", type=Path)
    diff.add_argument("--out", type=Path, required=True)
    sign = sp.add_parser("sign")
    sign.add_argument("pdf", type=Path)
    sign.add_argument("--out", type=Path, required=True)
    sign.add_argument("--pfx", type=Path)
    sign.add_argument("--cert", type=Path)
    sign.add_argument("--key", type=Path)
    sign.add_argument("--password-env")
    new_keys = sp.add_parser("generate-keys", help="Create encrypted personal keys and an untrusted self-signed certificate")
    new_keys.add_argument("--out", type=Path, required=True)
    new_keys.add_argument("--name", required=True, help="Personal display name, not an authenticated identity")
    new_keys.add_argument("--algorithm", choices=ALGORITHMS, default="RSA-3072")
    new_keys.add_argument("--days", type=int, default=365)
    new_keys.add_argument("--password-env", help="Password environment variable; omit for private no-echo prompt")
    renew = sp.add_parser("reissue-selfsigned", help="Modify owned self-signed certificate by reissuing with same private key")
    renew.add_argument("--existing", type=Path, required=True)
    renew.add_argument("--out", type=Path, required=True)
    renew.add_argument("--name", required=True)
    renew.add_argument("--days", type=int, default=365)
    renew.add_argument("--password-env", help="Password environment variable; omit for private no-echo prompt")
    verify = sp.add_parser("verify")
    verify.add_argument("pdf", type=Path)
    verify.add_argument("--trust-root", type=Path, action="append", default=[])
    verify.add_argument("--allow-network", action="store_true",
                        help="Explicit opt-in to certificate OCSP/CRL network requests")
    verify.add_argument("--strict-revocation", action="store_true",
                        help="Require revocation evidence (requires trusted roots)")
    verify.add_argument("--html-out", type=Path, help="Save human-readable escaped HTML report")
    sig_profile = sp.add_parser(
        "signature-profile", help="Read original signed PDF public algorithm, fingerprints and independent trust status"
    )
    sig_profile.add_argument("pdf", type=Path)
    sig_profile.add_argument("--trust-root", type=Path, action="append", default=[])
    sig_compare = sp.add_parser(
        "compare-signatures", help="Compare A/B claimed identities, fingerprints, algorithms and separate trust paths"
    )
    sig_compare.add_argument("original", type=Path)
    sig_compare.add_argument("produced", type=Path)
    sig_compare.add_argument("--original-trust-root", type=Path, action="append", default=[])
    sig_compare.add_argument("--produced-trust-root", type=Path, action="append", default=[])
    render = sp.add_parser("render", help="Render pages with PDFium")
    render.add_argument("pdf", type=Path)
    render.add_argument("--out", type=Path, required=True)
    render.add_argument("--scale", type=float, default=1.5)
    render.add_argument("--max-pages", type=int, default=100)
    picture = sp.add_parser("visual-diff", help="Pixel comparison of two PDFs")
    picture.add_argument("a", type=Path)
    picture.add_argument("b", type=Path)
    picture.add_argument("--out", type=Path, required=True)
    picture.add_argument("--scale", type=float, default=1.5)
    deep = sp.add_parser("details", help="Metadata, bookmarks, forms, annotations, attachments and glyph coordinates")
    deep.add_argument("pdf", type=Path)
    deep.add_argument("--out", type=Path, required=True)
    deep.add_argument("--max-pages", type=int, default=300)
    deep.add_argument("--max-glyphs", type=int, default=25000)
    page_edit = sp.add_parser("pages", help="Create new PDF with selected/reordered/rotated pages")
    page_edit.add_argument("pdf", type=Path)
    page_edit.add_argument("--out", type=Path, required=True)
    page_edit.add_argument("--select", help="1-based page sequence, e.g. 3,1,2")
    page_edit.add_argument("--rotate", type=int, default=0, choices=(0,90,180,270))
    merge = sp.add_parser("merge", help="Combine PDF documents into a separate PDF")
    merge.add_argument("pdfs", nargs="+", type=Path)
    merge.add_argument("--out", type=Path, required=True)
    comprehensive = sp.add_parser("analyze-all", help="Export qpdf objects, semantic data, signature status and rendered pages")
    comprehensive.add_argument("pdf", type=Path)
    comprehensive.add_argument("--out", type=Path, required=True)
    comprehensive.add_argument("--max-pages", type=int, default=150)
    properties = sp.add_parser("properties", help="Read standard Info dictionary properties")
    properties.add_argument("pdf", type=Path)
    property_edit = sp.add_parser("edit-properties", help="Create edited copy with audited metadata changes")
    property_edit.add_argument("pdf", type=Path)
    property_edit.add_argument("--out", type=Path, required=True)
    property_edit.add_argument("--title")
    property_edit.add_argument("--subject")
    property_edit.add_argument("--author")
    revision = sp.add_parser("revisions", help="Inspect xref history, EOF evidence and signature byte ranges")
    revision.add_argument("pdf", type=Path)
    revision.add_argument("--out", type=Path, required=True)
    note = sp.add_parser("add-note", help="Append an auditable note annotation to a PDF")
    note.add_argument("pdf", type=Path)
    note.add_argument("--out", type=Path, required=True)
    note.add_argument("--page", type=int, required=True)
    note.add_argument("--x", type=float, required=True)
    note.add_argument("--y", type=float, required=True)
    note.add_argument("--text", required=True)
    note.add_argument("--author", default="PDF Studio")
    paint = sp.add_parser("paint", help="Ordered page paint operations and non-conclusive graphics observations")
    paint.add_argument("pdf", type=Path)
    paint.add_argument("--out", type=Path, required=True)
    object_list = sp.add_parser(
        "native-objects", help="Identify single-use image XObjects and simple native vector paths"
    )
    object_list.add_argument("pdf", type=Path)
    object_list.add_argument("--page", type=int, required=True)
    object_edit = sp.add_parser(
        "edit-native-object", help="Edit native Image XObject RGB bytes or original vector fill operands"
    )
    object_edit.add_argument("pdf", type=Path)
    object_edit.add_argument("--page", type=int, required=True)
    object_edit.add_argument("--operator-index", type=int, required=True)
    object_edit.add_argument("--kind", choices=("image","vector-fill"), required=True)
    object_edit.add_argument("--out", type=Path, required=True)
    object_edit.add_argument("--image", type=Path)
    object_edit.add_argument("--color", help="New existing vector fill color: #RRGGBB")
    attachments = sp.add_parser("extract-attachments", help="Read-only export of embedded file attachments")
    attachments.add_argument("pdf", type=Path)
    attachments.add_argument("--out", type=Path, required=True)
    form_fields = sp.add_parser("form-fields", help="List editable standard AcroForm text fields")
    form_fields.add_argument("pdf", type=Path)
    fill = sp.add_parser("fill-form", help="Fill text fields of a PDF into a new file")
    fill.add_argument("pdf", type=Path)
    fill.add_argument("--out", type=Path, required=True)
    fill.add_argument("--field", action="append", required=True,
                      help="Field assignment NAME=TEXT; repeat for multiple fields")
    search = sp.add_parser("find-text", help="Search PDFium text and report glyph rectangles")
    search.add_argument("pdf", type=Path)
    search.add_argument("--query", required=True)
    search.add_argument("--out", type=Path, required=True)
    body = sp.add_parser("add-body-text", help="Add visible text to PDF page contents")
    body.add_argument("pdf", type=Path)
    body.add_argument("--out", type=Path, required=True)
    body.add_argument("--page", type=int, required=True)
    body.add_argument("--x", type=float, required=True)
    body.add_argument("--y", type=float, required=True)
    body.add_argument("--text", required=True)
    body.add_argument("--size", type=float, default=14)
    body.add_argument("--color", default="#18385C")
    body.add_argument("--font", type=Path)
    runs = sp.add_parser("text-runs", help="List original text-showing operands on a PDF page")
    runs.add_argument("pdf", type=Path)
    runs.add_argument("--page", type=int, required=True)
    replacement = sp.add_parser(
        "replace-text", help="Directly replace a supported existing PDF text operand"
    )
    replacement.add_argument("pdf", type=Path)
    replacement.add_argument("--out", type=Path, required=True)
    replacement.add_argument("--page", type=int, required=True)
    replacement.add_argument("--operator-index", type=int, required=True)
    replacement.add_argument("--array-index", type=int)
    replacement.add_argument("--expected", required=True)
    replacement.add_argument("--replacement", required=True)
    spans = sp.add_parser("tj-spans", help="List directly editable complete multi-fragment TJ text operands")
    spans.add_argument("pdf", type=Path)
    spans.add_argument("--page", type=int, required=True)
    span_edit = sp.add_parser(
        "replace-tj-span",
        help="Directly replace every text chunk of one TJ array, retaining numeric spacing",
    )
    span_edit.add_argument("pdf", type=Path)
    span_edit.add_argument("--out", type=Path, required=True)
    span_edit.add_argument("--page", type=int, required=True)
    span_edit.add_argument("--operator-index", type=int, required=True)
    span_edit.add_argument("--expected", required=True)
    span_edit.add_argument("--replacement", required=True)
    groups = sp.add_parser(
        "text-spans",
        help="List uninterrupted sequences of adjacent Tj/TJ operations within BT/ET",
    )
    groups.add_argument("pdf", type=Path)
    groups.add_argument("--page", type=int, required=True)
    group_edit = sp.add_parser(
        "replace-text-span",
        help="Directly edit adjacent PDF text operations with no state/position changes",
    )
    group_edit.add_argument("pdf", type=Path)
    group_edit.add_argument("--out", type=Path, required=True)
    group_edit.add_argument("--page", type=int, required=True)
    group_edit.add_argument("--start-operator", type=int, required=True)
    group_edit.add_argument("--expected", required=True)
    group_edit.add_argument("--replacement", required=True)
    paragraphs = sp.add_parser(
        "reflow-blocks", help="List isolated PDF BT/ET text paragraphs safe for bounded direct reflow"
    )
    paragraphs.add_argument("pdf", type=Path)
    paragraphs.add_argument("--page", type=int, required=True)
    paragraph_edit = sp.add_parser(
        "reflow-text",
        help="Rewrite original paragraph text operands with measured line wrapping",
    )
    paragraph_edit.add_argument("pdf", type=Path)
    paragraph_edit.add_argument("--out", type=Path, required=True)
    paragraph_edit.add_argument("--page", type=int, required=True)
    paragraph_edit.add_argument("--start-operator", type=int, required=True)
    paragraph_edit.add_argument("--expected", required=True)
    paragraph_edit.add_argument("--replacement", required=True)
    paragraph_edit.add_argument("--width", type=float, required=True)
    paragraph_edit.add_argument("--height", type=float, required=True)
    paragraph_edit.add_argument(
        "--move-following-text", action="store_true",
        help="Optionally adjust separate following Tm positions on simple same-column text-only PDFs",
    )
    source_layout = sp.add_parser(
        "original-layout",
        help="Read stored font, size, leading and origin; label inferred paragraph dimensions",
    )
    source_layout.add_argument("pdf", type=Path)
    source_layout.add_argument("--page", type=int, required=True)
    source_layout.add_argument("--start-operator", type=int, required=True)
    source_reflow = sp.add_parser(
        "reflow-like-original",
        help="Directly reflow text using inferred bounds and exact original PDF text properties",
    )
    source_reflow.add_argument("pdf", type=Path)
    source_reflow.add_argument("--out", type=Path, required=True)
    source_reflow.add_argument("--page", type=int, required=True)
    source_reflow.add_argument("--start-operator", type=int, required=True)
    source_reflow.add_argument("--expected", required=True)
    source_reflow.add_argument("--replacement", required=True)
    sp.add_parser("desktop", help="Launch the local desktop viewer and comparison UI")
    args = p.parse_args()
    if args.action == "inspect":
        item = inspect_pdf(args.pdf, args.out, args.streams,
                           operators=args.operators, text_geometry=args.text_geometry,
                           assets=args.assets)
        result = {"manifest": str(args.out / "manifest.json"), "pages": item["page_count"],
                  "objects": item["object_count"], "signatures": len(item["signatures"])}
    elif args.action == "compare":
        a = json.loads(args.a.read_text(encoding="utf-8"))
        b = json.loads(args.b.read_text(encoding="utf-8"))
        result = compare(a, b)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result = {"report": str(args.out), "same_file_hash": result["same_file_hash"]}
    elif args.action == "sign":
        result = {"signed_pdf": str(sign_document(args.pdf, args.out, pfx=args.pfx, cert=args.cert,
                                                  key=args.key, password_env=args.password_env))}
    elif args.action == "signature-profile":
        result = inspect_signature_profile(args.pdf, trust_roots=args.trust_root)
    elif args.action == "compare-signatures":
        result = compare_signature_parameters(
            args.original, args.produced,
            original_roots=args.original_trust_root,
            new_roots=args.produced_trust_root,
        )
    elif args.action in ("generate-keys", "reissue-selfsigned"):
        import getpass
        import os
        if args.password_env:
            if args.password_env not in os.environ:
                raise ValueError("Specified private-key password environment variable was not found")
            password = os.environ[args.password_env]
        else:
            password = getpass.getpass("Private key password (input hidden): ")
        if args.action == "generate-keys":
            result = generate_personal_credentials(
                args.out, label=args.name, password=password,
                algorithm=args.algorithm, days_valid=args.days,
            )
        else:
            result = reissue_personal_certificate(
                args.existing, args.out, label=args.name,
                password=password, days_valid=args.days,
            )
    elif args.action == "verify":
        result = audit_signatures(
            args.pdf, args.trust_root,
            allow_network=args.allow_network,
            require_revocation=args.strict_revocation,
        )
        if args.html_out:
            saved = save_signature_html(result, args.html_out)
            result["html_report"] = str(saved)
    elif args.action == "render":
        result = render_document(args.pdf, args.out, args.scale, args.max_pages)
    elif args.action == "details":
        result = semantic_inventory(args.pdf, max_pages=args.max_pages,
                                    max_glyphs_per_page=args.max_glyphs)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"details_report": str(args.out), "pages": len(result["pages"]),
                  "form_fields": len(result["form_fields"]), "attachments": len(result["attachments"])}
    elif args.action == "pages":
        chosen = [int(x.strip()) for x in args.select.split(",")] if args.select else None
        result = transform_pages(args.pdf, args.out, pages=chosen, rotate=args.rotate)
    elif args.action == "merge":
        result = combine_documents(args.pdfs, args.out)
    elif args.action == "analyze-all":
        result = analyze_complete(args.pdf, args.out, max_pages=args.max_pages)
    elif args.action == "properties":
        result = document_properties(args.pdf)
    elif args.action == "edit-properties":
        updates = {key: getattr(args, key.lower()) for key in ("Title", "Subject", "Author")
                   if getattr(args, key.lower()) is not None}
        result = edit_properties(args.pdf, args.out, updates)
    elif args.action == "revisions":
        result = inspect_revisions(args.pdf)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.out.exists():
            raise ValueError("Revision report already exists; use a new filename")
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"report": str(args.out), "xref_chain": len(result["revision_chain"]),
                  "signature_ranges": len(result["byte_ranges"])}
    elif args.action == "add-note":
        result = add_note(args.pdf, args.out, page_number=args.page, x=args.x,
                          y=args.y, contents=args.text, author=args.author)
    elif args.action == "paint":
        result = inspect_paint_operations(args.pdf)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.out.exists():
            raise ValueError("Paint report already exists; choose a new output")
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"report": str(args.out), "pages": len(result["pages"])}
    elif args.action == "native-objects":
        result = list_editable_objects(args.pdf, args.page)
    elif args.action == "edit-native-object":
        result = edit_existing_object(
            args.pdf, args.out, page=args.page, operator_index=args.operator_index,
            kind=args.kind, replacement_image=args.image, fill_hex=args.color,
        )
    elif args.action == "extract-attachments":
        result = extract_attachments(args.pdf, args.out)
    elif args.action == "form-fields":
        result = text_fields(args.pdf)
    elif args.action == "fill-form":
        updates = {}
        for item in args.field:
            if "=" not in item:
                raise ValueError("--field requires NAME=TEXT")
            key, value = item.split("=", 1)
            if not key:
                raise ValueError("Field name cannot be empty")  # 限制：表单字段名不能为空
            updates[key] = value
        result = fill_text_fields(args.pdf, args.out, updates)
    elif args.action == "find-text":
        result = find_text(args.pdf, args.query)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.out.exists():
            raise ValueError("Search output already exists; provide a new filename")
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"report": str(args.out), "matches": result["count"]}
    elif args.action == "add-body-text":
        result = add_body_text(args.pdf, args.out, page=args.page,
                               x=args.x, y=args.y, text=args.text,
                               size=args.size, color=args.color, font_path=args.font)
    elif args.action == "text-runs":
        result = list_text_runs(args.pdf, args.page)
    elif args.action == "replace-text":
        result = replace_text_run(
            args.pdf, args.out, page=args.page,
            operator_index=args.operator_index,
            array_index=args.array_index,
            expected_text=args.expected,
            replacement=args.replacement,
        )
    elif args.action == "tj-spans":
        result = list_tj_spans(args.pdf, args.page)
    elif args.action == "replace-tj-span":
        result = replace_tj_span(
            args.pdf, args.out, page=args.page,
            operator_index=args.operator_index,
            expected_text=args.expected, replacement=args.replacement,
        )
    elif args.action == "text-spans":
        result = list_adjacent_text_spans(args.pdf, args.page)
    elif args.action == "replace-text-span":
        result = replace_adjacent_text_span(
            args.pdf, args.out, page=args.page,
            start_operator_index=args.start_operator,
            expected_text=args.expected, replacement=args.replacement,
        )
    elif args.action == "reflow-blocks":
        result = list_reflow_blocks(args.pdf, args.page)
    elif args.action == "reflow-text":
        operation = reflow_with_following_text if args.move_following_text else reflow_text_object
        result = operation(
            args.pdf, args.out, page=args.page,
            start_operator_index=args.start_operator,
            expected_text=args.expected, replacement=args.replacement,
            width_pt=args.width, height_pt=args.height,
        )
    elif args.action == "original-layout":
        result = read_original_layout(
            args.pdf, page=args.page,
            start_operator_index=args.start_operator,
        )
    elif args.action == "reflow-like-original":
        result = reflow_like_original(
            args.pdf, args.out, page=args.page,
            start_operator_index=args.start_operator,
            expected_text=args.expected, replacement=args.replacement,
        )
    elif args.action == "desktop":
        from .qt_desktop import launch
        launch()
        return
    else:
        result = visual_diff(args.a, args.b, args.out, args.scale)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
