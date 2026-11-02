"""只用虚构字节和替身描述流水线回归；不签署真实 PDF、不生成真实密钥。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pdf_studio import automatic_pipeline as pipeline
from pdf_studio.certificate_names import build_subject, name_attributes
from pdf_studio.file_identity import file_identity
from pdf_studio.keygen import _check_config
from pdf_studio.qt_workbench_shell import WorkbenchShell


def make_files(tmp_path):
    original, working = tmp_path / 'A.pdf', tmp_path / 'B.pdf'
    original.write_bytes(b'synthetic A original')
    working.write_bytes(b'synthetic B with all accumulated edits')
    return original, working


def test_actual_a_parameters_are_prepared_without_signing_b(tmp_path, monkeypatch):
    a, b = make_files(tmp_path)
    entry = {'field': 'A-selected', 'signer_certificate': {
        'subject': 'CN=Example@OLD,O=Example Lab,C=CN',
        'public_key': {'type': 'RSAPublicKey', 'bits': 2048}},
        'audit': {'digest_algorithm': 'sha384', 'signature_valid': False}}
    a_report = {'full_evidence': {'signatures': [entry]}}
    before = deepcopy(a_report)
    read = Mock(return_value=a_report)
    monkeypatch.setattr(pipeline, 'read_signature_audit', read)
    result = pipeline.prepare_a_parameters(
        original=a, selected_field='A-selected', expected_original_identity=file_identity(a))
    read.assert_called_once_with(a, [], allow_network=False)
    parameters = result['parameters']
    assert parameters['algorithm'] == 'RSA-2048' and parameters['digest'] == 'sha384'
    assert parameters['label'] == 'Example@OLD'
    assert 'QPDF-' not in parameters['label']
    assert any(attr['value'] == 'Example Lab' for attr in parameters['subject_attributes'])
    assert result['original_audit'] is a_report and a_report == before
    assert a.read_bytes() == b'synthetic A original'
    assert b.read_bytes() == b'synthetic B with all accumulated edits'
    assert not (tmp_path / 'signed.pdf').exists()


def test_a_read_failure_uses_default_parameters_and_reports_error(tmp_path, monkeypatch):
    a, b = make_files(tmp_path)
    monkeypatch.setattr(pipeline, 'read_signature_audit', Mock(side_effect=ValueError('synthetic A error')))
    result = pipeline.prepare_a_parameters(original=a)
    assert 'synthetic A error' in result['original_read_error']
    assert result['original_audit'] is None
    assert result['parameters']['label'] == 'B 文档签署者'


def test_template_preparation_error_names_the_failed_stage(tmp_path, monkeypatch):
    a, b = make_files(tmp_path)
    monkeypatch.setattr(pipeline, 'read_signature_audit', lambda *a, **k: {})
    monkeypatch.setattr(pipeline, 'build_reference_template', Mock(side_effect=ValueError('synthetic template error')))
    with pytest.raises(RuntimeError, match='准备 A 参考参数阶段.*synthetic template error'):
        pipeline.prepare_a_parameters(original=a)


def test_long_a_names_are_not_rejected_by_old_product_length_limit():
    label = 'Example-' + '主体名称' * 35 + '@QPDF-NEW'
    _check_config(label, 'synthetic-password-only', 365)
    for attrs in (None, name_attributes('O=Example Lab,C=CN')):
        # 包含 CN 和仅有 O/C 的模板都允许完整编码，不因分支遗漏仍报 64 字节错误。
        assert label in build_subject(label, attrs).rfc4514_string()


def test_busy_click_is_queued_once_not_discarded(monkeypatch):
    from pdf_studio import qt_workbench_shell as ui
    timer = Mock()
    monkeypatch.setattr(ui.QTimer, 'singleShot', timer)
    context = ('current A', 'current B')
    shell = SimpleNamespace(_pending_b_signing=None, _b_generation_active=False,
        _signing_context_identity=lambda: context, _resume_b_signing=Mock(), refresh_signing_flow=Mock(),
        w=SimpleNamespace(_busy=True, _notify=Mock()))
    WorkbenchShell.generate_b_signature(shell)
    WorkbenchShell.generate_b_signature(shell)
    assert shell._pending_b_signing == context and timer.call_count == 1


def test_button_entry_exception_is_visible(monkeypatch):
    from pdf_studio import qt_automatic_signing as ui
    monkeypatch.setattr(ui, 'start_automatic_b_signing', Mock(side_effect=ValueError('synthetic entry error')))
    shell = SimpleNamespace(_pending_b_signing=None, w=SimpleNamespace(_busy=False, _error=Mock()))
    WorkbenchShell.generate_b_signature(shell)
    assert 'synthetic entry error' in shell.w._error.call_args.args[0]


def test_existing_read_timer_is_not_postponed_by_refresh():
    timer = Mock()
    timer.isActive.return_value = True
    shell = SimpleNamespace(_signed_output=None, _mode=1, _signature_requests=set(), _signature_reading={},
        _signature_timer=timer, _cached_signature=lambda slot: None,
        _signature_identity=lambda slot: (slot,), w=SimpleNamespace(docs={'A': 'A.pdf', 'B': 'B.pdf'}))
    WorkbenchShell.request_signature_reads(shell)
    timer.start.assert_not_called()


