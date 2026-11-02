"""工作区按钮接线回归；不创建 Qt 窗口、不操作实际 PDF。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from pdf_studio.qt_workbench_shell import WorkbenchShell


def test_explicit_check_requeues_both_current_files():
    identities={'A':('A-version',),'B':('B-edited-version',)}
    shell=SimpleNamespace(w=SimpleNamespace(_busy=False),
        _signature_identity=lambda slot:identities[slot],
        _signature_cache={'A':'old-A','B':'old-B'},
        clear_signature=Mock(),show_full_signature_evidence=Mock())
    WorkbenchShell.reload_signature_evidence(shell)
    assert shell._manual_signature_reads==identities
    assert not shell._signature_cache
    shell.show_full_signature_evidence.assert_called_once()


def test_explicit_check_includes_b_even_before_generation():
    identities={'A':('A-version',),'B':('B-edited-version',)}
    shell=SimpleNamespace(_signed_output=None,_mode=1,
        _manual_signature_reads=identities.copy(),_signature_requests=set(),
        _signature_reading={},_signature_timer=Mock(),
        _signature_identity=lambda slot:identities[slot],_cached_signature=lambda slot:None,
        w=SimpleNamespace(docs={'A':Path('A.pdf'),'B':Path('B.pdf')}))
    WorkbenchShell.request_signature_reads(shell)
    assert shell._signature_requests=={'A','B'}


def test_old_manual_request_does_not_check_replaced_unsigned_b():
    identities={'A':('A-version',),'B':('new-B-version',)}
    shell=SimpleNamespace(_signed_output=None,_mode=1,
        _manual_signature_reads={'B':('old-B-version',)},_signature_requests=set(),
        _signature_reading={},_signature_timer=Mock(),
        _signature_identity=lambda slot:identities[slot],_cached_signature=lambda slot:None,
        w=SimpleNamespace(docs={'A':Path('A.pdf'),'B':Path('new-B.pdf')}))
    WorkbenchShell.request_signature_reads(shell)
    assert shell._signature_requests=={'A'}


def test_busy_check_keeps_existing_results():
    cache={'A':'current-result'}
    shell=SimpleNamespace(w=SimpleNamespace(_busy=True,_notify=Mock()),_signature_cache=cache,
                          show_full_signature_evidence=Mock())
    WorkbenchShell.reload_signature_evidence(shell)
    assert cache=={'A':'current-result'}
    shell.show_full_signature_evidence.assert_not_called()


def test_next_button_reuses_navigation_without_export_or_signing():
    shell=SimpleNamespace(signing_dialog=Mock(),w=SimpleNamespace(workspace_tabs=Mock()))
    WorkbenchShell.finish_signing(shell)
    shell.w.workspace_tabs.setCurrentIndex.assert_called_once_with(2)
    shell.signing_dialog.hide.assert_called_once()
