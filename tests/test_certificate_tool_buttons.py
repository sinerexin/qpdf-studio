"""独立按钮与设置页回归描述；不生成真实密钥或签名。"""
import ast
import inspect
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from pdf_studio.qt_certificate_tools import CertificateToolsBar, TOOL_DEFINITIONS
from pdf_studio.qt_signature_cards import SignatureInformationPane
from pdf_studio.qt_signature_evidence import SignatureEvidencePane
from pdf_studio.qt_workbench_shell import WorkbenchShell


def test_five_tool_keys_are_unique_and_each_has_description():
    assert len(TOOL_DEFINITIONS) == 5
    assert len({row[0] for row in TOOL_DEFINITIONS}) == 5
    assert all(title and description for _, title, description in TOOL_DEFINITIONS)


class _Style:
    def unpolish(self, card):
        pass

    def polish(self, card):
        pass


class _Card:
    def __init__(self):
        self.text = ''
        self.tone = None

    def setText(self, text):
        self.text = text

    def setProperty(self, name, value):
        if name == 'tone':
            self.tone = value

    def style(self):
        return _Style()


def test_failed_check_mark_survives_the_post_task_refresh():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    bar = CertificateToolsBar(SimpleNamespace())
    bar.set_phase(5, check_failed=True)
    assert bar.buttons['check'].text().startswith('✗ ')
    assert bar.buttons['sign'].text().startswith('✓ ')
    assert '未完成' in bar.hint.text()
    assert '5 / 5' not in bar.hint.text()
    bar.set_busy(True)
    bar.set_busy(False)
    assert bar.buttons['check'].text().startswith('✗ ')
    assert '未完成' in bar.hint.text()
    bar.set_phase(5)
    assert bar.buttons['check'].text().startswith('✓ ')
    assert '5 / 5' in bar.hint.text()
    bar.close()


def test_pending_check_and_failed_read_do_not_claim_unsigned_or_unread():
    cards = [_Card() for _ in range(5)]
    note, state = Mock(), Mock()
    pane = SimpleNamespace(report=None, status_cards=cards, operation_note=note, state=state, _base_state='')
    SignatureEvidencePane.set_check_pending(pane)
    assert '尚未检查' in pane._base_state
    assert '未签署' not in pane._base_state and '不是通过，也不是失败' in pane._base_state
    assert '尚未检查' in note.setText.call_args.args[0]
    assert all(card.text.endswith('尚未检查') and card.tone == 'neutral' for card in cards)

    info = SimpleNamespace(_card_ready=True, _selected=lambda: {}, _audit=lambda: {},
                           generated_credentials=None, reference_template={'label': 'CN=Reference'},
                           cards={index: Mock() for index in (0, 1, 2, 5)}, state=Mock(),
                           operation_note=Mock(), _base_state='读取失败，不能断言无签名：ValueError: synthetic',
                           _refresh_detail=Mock())
    SignatureInformationPane._render_tab(info, 0)
    shown = info.operation_note.setText.call_args.args[0]
    assert shown.startswith('读取失败')
    assert '不代表 B 已包含' not in shown


def test_clicked_key_calls_only_its_own_backend():
    callbacks = {key: Mock() for key, _, _ in TOOL_DEFINITIONS}
    proxy = SimpleNamespace(shell=SimpleNamespace(w=SimpleNamespace(_busy=False, _notify=Mock(), _error=Mock()),
                            _signature_timer=Mock(), request_signature_reads=Mock()),
                            callbacks=callbacks)
    CertificateToolsBar._invoke(proxy, 'check')
    callbacks['check'].assert_called_once_with()
    assert sum(callback.call_count for callback in callbacks.values()) == 1


def test_tool_exception_is_visible_in_window():
    error = Mock()
    proxy = SimpleNamespace(shell=SimpleNamespace(w=SimpleNamespace(_busy=False, _notify=Mock(), _error=error),
                            _signature_timer=Mock(), request_signature_reads=Mock()),
                            callbacks={'keys': Mock(side_effect=ValueError('synthetic error'))})
    CertificateToolsBar._invoke(proxy, 'keys')
    assert 'synthetic error' in error.call_args.args[0]


def test_settings_sign_button_uses_selected_credential_flow_not_automatic_replacement():
    from pdf_studio.qt_signing_panel import SigningPanel
    text = inspect.getsource(SigningPanel.__init__)
    assert "self.sign_button = w._button('使用所选凭据签署 B…', shell.sign_b_with_selected_credentials" in text
    assert 'shell.generate_b_signature' not in text


def test_step_three_builds_visible_tools_instead_of_more_menu():
    text = inspect.getsource(WorkbenchShell._add_modes)
    assert 'CertificateToolsBar(self)' in text
    assert 'w.signing_tools.hide()' in text and 'w.signing_tools.show()' not in text
    assert 'self.show_signing_settings' not in text
    assert 'self.configure_automatic_b_signing' not in text
    assert 'self.show_full_signature_evidence' not in text


def test_compare_routes_to_main_information_and_right_hand_checks():
    proxy = SimpleNamespace(show_full_signature_evidence=Mock(), reload_signature_evidence=Mock())
    WorkbenchShell.compare_current_signatures(proxy)
    proxy.show_full_signature_evidence.assert_called_once_with()
    proxy.reload_signature_evidence.assert_called_once_with()


def test_key_generation_reissue_keeps_b_callback_binding():
    text = inspect.getsource(WorkbenchShell.generate_b_credentials)
    tree = ast.parse(text.lstrip())
    assert 'preferred_algorithm=preferred_algorithm' in text and 'on_created=completed' in text
    assert 'reissue=reissue' in text
    assert tree.body


def test_selected_credential_signing_calls_real_signer_and_restores_read_queue():
    shell = SimpleNamespace(w=SimpleNamespace(_busy=False, sign_active=Mock(), _error=Mock()),
        _signature_timer=Mock(), request_signature_reads=Mock())
    WorkbenchShell.sign_b_with_selected_credentials(shell)
    shell.w.sign_active.assert_called_once_with()
    shell._signature_timer.stop.assert_called_once_with()
    shell.request_signature_reads.assert_called_once_with()


def test_reissue_tools_pass_managed_password_protection_to_new_outputs():
    from pdf_studio.qt_keygen_dialog import open_personal_certificate_editor
    from pdf_studio.qt_signer_output_dialog import open_signer_output_dialog
    from pdf_studio.keygen import reissue_personal_certificate
    assert 'load_managed_password' in inspect.getsource(open_personal_certificate_editor)
    assert 'store_password=managed' in inspect.getsource(open_personal_certificate_editor)
    assert 'store_password=managed' in inspect.getsource(open_signer_output_dialog)
    assert inspect.signature(reissue_personal_certificate).parameters['store_password'].default is False
