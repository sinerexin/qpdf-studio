"""Routing regressions; these cases do not instantiate GUI widgets or sign PDFs."""
from types import SimpleNamespace

import pytest

from pdf_studio.qt_workbench_shell import WorkbenchShell


@pytest.mark.parametrize(('mode', 'stage_index'), [(1, 1), (0, 0)])
def test_evidence_action_routes_stage_and_opens_right_inspector(mode, stage_index):
    events = []
    shell = SimpleNamespace(
        document_stage=SimpleNamespace(setCurrentIndex=lambda index: events.append(('stage', index))),
        evidence_index=7,
        _mode=mode,
        w=SimpleNamespace(
            show_advanced_inspector=lambda index: events.append(('inspector', index)),
            active_slot=SimpleNamespace(currentText=lambda: 'B')),
        signature_comparison=SimpleNamespace(select_slot=lambda slot: events.append(('slot', slot))),
        _refresh_signature_sources=lambda: events.append(('refresh',)),
        request_signature_reads=lambda: events.append(('read',)),
    )
    WorkbenchShell.show_full_signature_evidence(shell)
    assert ('stage', stage_index) in events
    assert ('inspector', 7) in events
    assert ('slot', 'B') in events
    assert shell._inspector_requested


def test_enter_step_three_opens_inspector_not_signing_dialog():
    events = []
    shell = SimpleNamespace(w=SimpleNamespace(
        workspace_tabs=SimpleNamespace(setCurrentIndex=lambda index: events.append(('workspace', index))),
        active_slot=SimpleNamespace(setCurrentText=lambda slot: events.append(('slot', slot))),
        _notify=lambda text: None),
        show_full_signature_evidence=lambda: events.append(('evidence',)),
    )
    WorkbenchShell.advance_to_signing(shell)
    assert events == [('workspace', 1), ('slot', 'B'), ('evidence',)]


def test_finished_signing_loads_evidence_and_closes_settings():
    events = []
    shell = SimpleNamespace(w=SimpleNamespace(
        docs={'A': 'original.pdf'},
        selection_editor=SimpleNamespace(_preview_note=lambda *args: None,
                                        save_state=SimpleNamespace(setText=lambda text: None))),
        refresh_signing_flow=lambda: None,
        signing_dialog=SimpleNamespace(hide=lambda: events.append(('settings', 'hidden'))),
        show_signature_evidence=lambda slot: events.append(('evidence', slot)))
    WorkbenchShell.signed_output_completed(shell, 'working.pdf', 'signed.pdf')
    assert shell._signed_output == 'signed.pdf'
    assert events == [('settings', 'hidden'), ('evidence', 'B')]


def test_signature_information_uses_facts_not_validation_status():
    from pdf_studio.qt_signature_evidence import signature_information
    row = signature_information({'field': 'Sig1', 'byte_range': [0, 20, 40, 10]},
                                {'intact': False, 'signature_valid': False,
                                 'digest_algorithm': 'sha256'}, '已读取')
    assert row['签名字段'] == 'Sig1'
    assert row['ByteRange'] == [0, 20, 40, 10]
    assert row['摘要算法'] == 'sha256'
    assert '完整性' not in row and '数学签名' not in row


def test_right_verification_selection_does_not_hide_either_side():
    from pdf_studio.qt_signature_cards import SignatureVerificationWorkspace
    events = []
    style = SimpleNamespace(unpolish=lambda widget: None, polish=lambda widget: None)
    panes = {slot: SimpleNamespace(setProperty=lambda key, value, s=slot: events.append((s, value)),
                                  style=lambda: style) for slot in ('A', 'B')}
    SignatureVerificationWorkspace.select_slot(SimpleNamespace(panes=panes), 'B')
    assert events == [('A', False), ('B', True)]
