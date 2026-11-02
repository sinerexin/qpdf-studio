"""Reject reintroduction of the cover-and-overlay approach in editing code."""
import ast
from pathlib import Path


def test_text_editing_modules_do_not_use_page_overlay_or_masking_apis() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "pdf_studio"
    # Read-only difference images are not editing and are intentionally
    # excluded. The editor must write actual page text /Contents instead.
    editors = [root / name for name in (
        "body_text.py", "replace_text.py", "adjacent_text.py",
        "paragraph_reflow.py", "original_layout.py", "qt_paragraph_dialog.py",
        "flow_layout.py", "qt_replace_dialog.py", "qt_desktop.py",
        "object_editor.py", "qt_object_dialog.py",
    )]
    forbidden = {
        "add_overlay", "add_underlay", "drawRect", "drawRectangle",
        "insert_image", "insertImage", "add_redact_annot", "apply_redactions",
    }
    for editor in editors:
        tree = ast.parse(editor.read_text(encoding="utf-8"))
        referenced = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not (referenced & forbidden), (
            f"{editor.name} invokes prohibited overlay/cover API: "
            f"{referenced & forbidden}"
        )


def test_existing_text_replacement_does_not_generate_new_paint_operations(
    tmp_path: Path,
) -> None:
    import pikepdf
    from reportlab.pdfgen.canvas import Canvas
    from pdf_studio.replace_text import list_text_runs, replace_text_run

    src, dst = tmp_path / "source.pdf", tmp_path / "new.pdf"
    canvas = Canvas(str(src))
    canvas.drawString(80, 700, "Sample text operand")
    canvas.save()
    run = next(r for r in list_text_runs(src, 1)["runs"] if r["editable"])
    replace_text_run(src, dst, page=1, operator_index=run["operator_index"],
                     expected_text=run["text"], replacement="Replaced text operand")
    with pikepdf.Pdf.open(src) as a, pikepdf.Pdf.open(dst) as b:
        old = list(pikepdf.parse_content_stream(a.pages[0]))
        new = list(pikepdf.parse_content_stream(b.pages[0]))
        # There must be no extra draw commands: a string operand is changed,
        # without a rectangle or form/image operation hiding existing text.
        assert len(new) == len(old)
        assert [str(op.operator) for op in new] == [str(op.operator) for op in old]
        assert str(new[run["operator_index"]].operands[0]) == "Replaced text operand"
