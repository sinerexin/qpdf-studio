"""Focused workflow regression cases, without creating GUI windows."""
from pathlib import Path
from types import SimpleNamespace

from pdf_studio.qt_context_editor import ContextSelectionEditor
from pdf_studio.qt_workbench_shell import WorkbenchShell


def _editor():
    a, b = Path('source.pdf'), Path('preview.pdf')
    # Use a plain proxy to avoid constructing a QWidget in these cases.
    proxy = SimpleNamespace(_generated_source=a, _generated_result=b,
                            workbench=SimpleNamespace(docs={'A': a, 'B': b}))
    proxy.has_pending_edits = lambda: False
    return proxy


def test_only_generated_b_for_current_a_is_ready():
    editor = _editor()
    assert ContextSelectionEditor.current_working_copy_ready(editor)
    editor.workbench.docs['A'] = Path('other-source.pdf')
    assert not ContextSelectionEditor.current_working_copy_ready(editor)


def test_replaced_or_pending_b_is_not_ready():
    editor = _editor()
    editor.workbench.docs['B'] = Path('other-result.pdf')
    assert not ContextSelectionEditor.current_working_copy_ready(editor)
    editor.workbench.docs['B'] = editor._generated_result
    editor.has_pending_edits = lambda: True
    assert not ContextSelectionEditor.current_working_copy_ready(editor)


def test_enter_signing_without_edit_preview():
    class Selector:
        def setCurrentIndex(self, value):
            self.value = value
        def setCurrentText(self, value):
            self.value = value
    tabs, slot = Selector(), Selector()
    shell = SimpleNamespace(w=SimpleNamespace(
        workspace_tabs=tabs, active_slot=slot, _notify=lambda message: None),
        show_full_signature_evidence=lambda: None)
    WorkbenchShell.advance_to_signing(shell)
    assert tabs.value == 1 and slot.value == 'B'


def test_default_output_is_beside_original_not_temporary_preview():
    original = Path(r'F:/practice/original.pdf')
    shell = SimpleNamespace(w=SimpleNamespace(docs={'A': original}))
    output = WorkbenchShell.default_signed_output(shell, Path(r'C:/temp/preview.pdf'))
    assert output == original.with_name('original-edited-signed.pdf')
