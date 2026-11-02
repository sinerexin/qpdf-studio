"""生成后检查流程回归；只用虚构文件和替身，不创建窗口、密钥或真实 PDF 签名。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from pdf_studio import automatic_signing as module
from pdf_studio.file_identity import file_identity
from pdf_studio.qt_workbench_shell import WorkbenchShell


def test_gui_and_worker_share_exact_file_identity_format(tmp_path):
    source=tmp_path/'B.pdf'
    source.write_bytes(b'fixture')
    shell=SimpleNamespace(w=SimpleNamespace(docs={'B':source}))
    assert WorkbenchShell._signature_identity(shell,'B')==file_identity(source)
    assert len(file_identity(source))==6


def test_raw_a_report_supplies_reference_without_reusing_b_ui_values():
    entry={'field':'A-signed','signer_certificate':{'subject':'CN=Example',
           'public_key':{'type':'RSAPublicKey','bits':2048}},'audit':{'digest_algorithm':'sha384'}}
    report={'full_evidence':{'signatures':[entry]}}
    before=deepcopy(report)
    reference=module.generation_reference(report,'A-signed')
    assert module.reference_defaults(reference['entry'],reference['audit'])['algorithm']=='RSA-2048'
    assert module.reference_defaults(reference['entry'],reference['audit'])['digest']=='sha384'
    reference['entry']['signer_certificate']['subject']='changed copy'
    assert report==before
    assert module.generation_reference(None)=={'entry':{},'audit':{}}


def test_new_signature_is_matched_by_certificate_not_array_order():
    report={'full_evidence':{'signatures':[
        {'field':'new','signer_certificate':{'sha256':'aa:bb'}},
        {'field':'old','signer_certificate':{'sha256':'ccdd'}}]}}
    assert module.generated_signature_entries(report,{'certificate_sha256_fingerprint':'AABB'})[0]['field']=='new'
    assert module.generated_signature_entries(report,{})==[]


def test_b_background_read_is_queued_only_after_generation():
    shell=SimpleNamespace(_signed_output=None,_mode=1,_signature_requests=set(),
         _signature_reading={},_signature_timer=Mock(),_cached_signature=lambda slot:None,
         _signature_identity=lambda slot:('identity',slot),
         w=SimpleNamespace(docs={'A':Path('A.pdf'),'B':Path('B-edited.pdf')}))
    WorkbenchShell.request_signature_reads(shell)
    assert shell._signature_requests=={'A'}
    shell._signed_output=Path('B-signed.pdf')
    shell.w.docs['B']=shell._signed_output
    WorkbenchShell.request_signature_reads(shell)
    assert shell._signature_requests=={'A','B'}


def test_generate_click_does_not_require_a_display_cache(monkeypatch):
    # 主按钮直接提交完整流水线，不再等待 A 卡片缓存或打开强制确认窗口。
    from pdf_studio import qt_automatic_signing as ui
    start = Mock()
    monkeypatch.setattr(ui, 'start_automatic_b_signing', start)
    shell = SimpleNamespace(_pending_b_signing=None,
        w=SimpleNamespace(_busy=False, _error=Mock()))
    WorkbenchShell.generate_b_signature(shell)
    start.assert_called_once_with(shell)
    shell.w._error.assert_not_called()


def test_waiting_generation_request_is_canceled_when_documents_change():
    shell=SimpleNamespace(_pending_b_signing=('old-A','old-B'),_mode=1,
        _signing_context_identity=lambda:('new-A','new-B'),
        w=SimpleNamespace(_notify=Mock()), refresh_signing_flow=Mock())
    WorkbenchShell._resume_b_signing(shell)
    assert shell._pending_b_signing is None
