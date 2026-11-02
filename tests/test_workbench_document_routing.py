"""Small document-routing regression cases; no GUI windows are created."""
from types import SimpleNamespace

from pdf_studio.qt_context_editor import ContextSelectionEditor
from pdf_studio.qt_workbench_shell import WorkbenchShell


def _shell(tmp_path):
    paths = {key: tmp_path / (key + '.pdf') for key in ('A', 'B')}
    for key, path in paths.items():
        path.write_bytes(key.encode())
    shell = WorkbenchShell.__new__(WorkbenchShell)
    shell.w = SimpleNamespace(docs=paths)
    shell._signature_requests = set()
    shell._signature_reading = {}
    shell._generated_credentials = None
    shell._signature_cache = {}
    return shell


def test_replacing_b_does_not_erase_a_signature_cache(tmp_path):
    shell = _shell(tmp_path)
    audit_a, audit_b = {'count': 1}, {'count': 0}
    shell._signature_cache = {
        slot: (shell._signature_identity(slot), audit, None)
        for slot, audit in [('A', audit_a), ('B', audit_b)]
    }
    shell.invalidate_signature('B')
    assert shell._cached_signature('A')[1] is audit_a
    assert shell._cached_signature('B') is None


def test_same_path_new_file_contents_invalidates_cached_evidence(tmp_path):
    shell = _shell(tmp_path)
    shell._signature_cache['B'] = (shell._signature_identity('B'), {'count': 1}, None)
    shell.w.docs['B'].write_bytes(b'changed content and file length')
    assert shell._cached_signature('B') is None


def test_failed_read_is_not_treated_as_unsigned(tmp_path):
    shell = _shell(tmp_path)
    shell._signature_cache['A'] = (shell._signature_identity('A'), None, 'parser failed')
    cached = shell._cached_signature('A')
    assert cached[1] is None and cached[2] == 'parser failed'


def test_only_current_successful_edit_revision_can_proceed():
    state = SimpleNamespace(_dirty=True, _selection_epoch=3, _revision=2, _result_token=(3, 1))
    assert ContextSelectionEditor.has_pending_edits(state)
    state._result_token = (3, 2)
    assert not ContextSelectionEditor.has_pending_edits(state)
    state._selection_epoch = 4
    assert ContextSelectionEditor.has_pending_edits(state)
    state._dirty = False
    assert not ContextSelectionEditor.has_pending_edits(state)
