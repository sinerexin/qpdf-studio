"""五阶段协作回归；全用替身，不生成真实密钥、不签实际 PDF、不创建 GUI。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pdf_studio import signing_stages as stages
from pdf_studio.qt_certificate_tools import TOOL_DEFINITIONS


def fake_session(tmp_path, monkeypatch):
    source = tmp_path / 'B.pdf'
    source.write_bytes(b'synthetic B with all edits')
    events, key = [], Mock()
    params = dict(label='Example@NEW', algorithm='RSA-2048', digest='sha384', days_valid=365)
    monkeypatch.setattr(stages, 'prepare_a_parameters', lambda *a, **k:
        (events.append('read A') or dict(parameters=params, original_audit={'A': 'actual report'})))
    monkeypatch.setattr(stages, 'validate_generation_request', lambda *a, **k: None)
    monkeypatch.setattr(stages, '_new_private_key', lambda algorithm: events.append('keys') or key)
    monkeypatch.setattr(stages, 'public_key_details', lambda public: {'type': 'fake', 'spki_sha256': 'NEW-KEY'})
    monkeypatch.setattr(stages, 'new_password', lambda: 'synthetic password only')
    def certificate(folder, **options):
        assert options['prepared_key'] is key and options['store_password'] is True
        assert options['password'] == 'synthetic password only'
        events.append('certificate')
        return {'certificate_sha256_fingerprint': 'AABB'}
    monkeypatch.setattr(stages, 'generate_personal_credentials', certificate)
    monkeypatch.setattr(stages, 'load_managed_password', lambda path: 'synthetic password only')
    def sign(source, target, **options):
        events.append('sign B')
        assert options['digest_algorithm'] == 'sha384'
        assert source.read_bytes() == b'synthetic B with all edits'
        target.write_bytes(b'synthetic FINAL signed B')
        return target
    monkeypatch.setattr(stages, 'sign_document', sign)
    def read(path, roots, **options):
        events.append('check final B')
        assert path.read_bytes() == b'synthetic FINAL signed B'
        return {'full_evidence': {'signatures': [{'field': 'ActualNew',
            'signer_certificate': {'sha256': 'aabb'}, 'audit': {'intact': True}}]}}
    monkeypatch.setattr(stages, 'read_signature_audit', read)
    session = stages.SigningStages(source, tmp_path / 'signed.pdf', tmp_path / 'credentials')
    return session, events, key


def test_five_manual_steps_reuse_same_material_and_order(tmp_path, monkeypatch):
    session, events, key = fake_session(tmp_path, monkeypatch)
    session.run_to(1)
    session.run_to(2)
    assert events == ['read A', 'keys']
    assert session._key is key and 'password' not in session.result()
    session.run_to(3)
    assert session._key is None
    result = session.run_to(4)
    assert result['audit_pending'] is True and result['audit'] is None
    assert events == ['read A', 'keys', 'certificate', 'sign B']
    result = session.run_to(5)
    assert events == ['read A', 'keys', 'certificate', 'sign B', 'check final B']
    assert result['new_signature_field'] == 'ActualNew' and result['audit_pending'] is False
    assert result['pipeline_stages'][-1] == '检查与对照'


def test_one_click_runs_identical_operations_without_duplicates(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    session.run_to(2)
    session.run_to(5)
    assert events == ['read A', 'keys', 'certificate', 'sign B', 'check final B']


def test_later_step_automatically_completes_only_missing_preconditions(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    session.run_to(3)
    session.run_to(3)
    assert events == ['read A', 'keys', 'certificate']


def test_failed_check_keeps_pdf_and_does_not_fabricate_success(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    monkeypatch.setattr(stages, 'read_signature_audit', Mock(side_effect=ValueError('synthetic read failure')))
    result = session.run_to(5)
    assert result['phase'] == 5 and result['pdf'].exists() and result['audit'] is None
    assert 'synthetic read failure' in result['audit_error']
    assert result['pipeline_stages'] == ['读取 A 参数', '生成 B 密钥', '生成 B 证书', '签署 B']
    assert 'check final B' not in events


def test_stage_five_reads_original_directly_and_generated_b_separately(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    original = tmp_path / 'A.pdf'
    original.write_bytes(b'original file')
    session.options['original'] = original
    seen = []

    def read(path, roots, **options):
        seen.append(Path(path).read_bytes())
        if Path(path) == original:
            return {'count': 1, 'full_evidence': {'signatures': [{'field': 'A-old'}]}}
        events.append('check final B')
        assert Path(path).read_bytes() == b'synthetic FINAL signed B'
        return {'full_evidence': {'signatures': [{'field': 'ActualNew',
            'signer_certificate': {'sha256': 'aabb'}}]}}

    monkeypatch.setattr(stages, 'read_signature_audit', read)
    session.run_to(4)
    result = session.run_to(5)
    assert seen == [b'original file', b'synthetic FINAL signed B']
    assert result['original_checked'] is True
    assert result['original_audit']['full_evidence']['signatures'][0]['field'] == 'A-old'
    assert result['original_read_error'] is None
    assert result['audit']['full_evidence']['signatures'][0]['field'] == 'ActualNew'


def test_original_read_failure_still_checks_generated_b(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    original = tmp_path / 'A.pdf'
    original.write_bytes(b'original file')
    session.options['original'] = original

    def read(path, roots, **options):
        if Path(path) == original:
            raise ValueError('synthetic A failure')
        events.append('check final B')
        return {'full_evidence': {'signatures': [{'field': 'ActualNew',
            'signer_certificate': {'sha256': 'aabb'}}]}}

    monkeypatch.setattr(stages, 'read_signature_audit', read)
    result = session.run_to(5)
    assert result['phase'] == 5 and result['original_audit'] is None
    assert 'synthetic A failure' in result['original_read_error']
    assert result['audit']['full_evidence']['signatures'][0]['field'] == 'ActualNew'
    assert result['audit_error'] is None


def test_failure_retains_completed_stage_and_names_failure(tmp_path, monkeypatch):
    session, events, _ = fake_session(tmp_path, monkeypatch)
    monkeypatch.setattr(stages, 'generate_personal_credentials', Mock(side_effect=ValueError('synthetic certificate error')))
    with pytest.raises(RuntimeError, match='第 3 环节.*synthetic certificate error'):
        session.run_to(5)
    assert session.phase == 2


def test_five_labels_match_shared_stage_order():
    assert [title.split(' ', 1)[1] for _, title, _ in TOOL_DEFINITIONS] == list(stages.STAGE_TITLES)


def test_failed_check_summary_does_not_list_the_check_as_finished():
    from pdf_studio.qt_signing_stages import _show_stage_result
    notes = []
    phases = []
    session = SimpleNamespace(phase=5, source=Path('b.pdf'), ui_context=None)
    result = dict(original_identity=None, signed_identity='signed', pdf=Path('signed.pdf'),
                  credential_folder=Path('credentials'), audit=None,
                  audit_error='ValueError: synthetic read failure', new_signature_field=None)
    shell = SimpleNamespace(
        w=SimpleNamespace(_notify=notes.append, active_slot=SimpleNamespace(setCurrentText=lambda text: None)),
        _signature_identity=lambda slot: 'signed' if slot == 'B' else None,
        _signature_cache={}, _select_signing_value=Mock(), signed_output_completed=Mock(),
        _signing_context_identity=lambda: ('A', 'signed'),
        certificate_tools=SimpleNamespace(set_phase=lambda phase, **kwargs: phases.append((phase, kwargs))),
        generated_credentials_note=SimpleNamespace(setText=notes.append))
    _show_stage_result(shell, session, result)
    assert phases == [(5, {'check_failed': True})]
    assert notes[-1].startswith('已完成：读取 A 参数 → 生成 B 密钥 → 生成 B 证书 → 签署 B')
    assert '→ 检查与对照' not in notes[-1]
    assert '第 5 环节检查未完成：ValueError: synthetic read failure' in notes[-1]


def test_signed_output_waiting_for_stage_five_is_not_a_queued_read():
    from pdf_studio.qt_workbench_shell import WorkbenchShell
    texts, calls = {}, []
    panes = {slot: SimpleNamespace(
        set_generation_pending=lambda: calls.append('generation'),
        set_check_pending=lambda: calls.append('check'),
        set_reading=lambda reading: calls.append(('reading', reading)),
        show_display_error=lambda exc: calls.append(('error', type(exc).__name__))) for slot in ('A', 'B')}
    shell = SimpleNamespace(
        signature_sources={slot: SimpleNamespace(setText=lambda text, slot=slot: texts.__setitem__(slot, text),
                                                 setToolTip=lambda text: None) for slot in ('A', 'B')},
        w=SimpleNamespace(docs={'A': 'a.pdf', 'B': 'signed.pdf'}, _notify=lambda text: None),
        _cached_signature=lambda slot: None, _signature_identity=lambda slot: 'id-' + slot,
        _staged_check_pending_identity='id-B', _manual_signature_reads={},
        _signed_output='signed.pdf', _signature_reading={},
        signature_comparison=SimpleNamespace(update_slot=lambda *args: None, panes=panes),
        signature_workspace=SimpleNamespace(update_slot=lambda *args: None, panes=panes))
    WorkbenchShell._refresh_signature_sources(shell)
    assert texts['B'] == 'B 工作副本：已签署，尚未检查'
    assert calls == [('reading', False), ('reading', False), 'check', 'check']
    assert '未签署' not in texts['B'] and '等待读取' not in texts['B']


def test_check_button_never_creates_a_new_signing_session():
    from pdf_studio.qt_signing_stages import run_signature_stage
    shell = SimpleNamespace(w=SimpleNamespace(_busy=False),
        _signing_context_identity=lambda: ('A', 'B'), _signing_stage_session=None,
        signature_workspace=SimpleNamespace(panes={'A': SimpleNamespace(_selected=lambda: {})}),
        compare_current_signatures=Mock())
    run_signature_stage(shell, 5)
    shell.compare_current_signatures.assert_called_once_with()
