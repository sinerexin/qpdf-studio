"""A/B 实际文件、缓存与显示投影分离；仅使用测试替身，不创建 Qt 窗口。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import os

from pdf_studio.certificate_names import display_name_fields
from pdf_studio.qt_signature_cards import SignatureInformationPane
from pdf_studio.qt_workbench_shell import WorkbenchShell
from pdf_studio.signature_card_data import default_signature_index, report_source_details


def test_default_selection_has_no_a_b_branch_and_prefers_latest_linked():
    entries = [{'field':'first'}, {'field':'latest'}, {'field':'orphan', 'discovery':{}}]
    # 扫描标志要为实际非空结构，空字典不代表已知扫描来源。
    entries[2]['discovery'] = {'field_tree_linked':False}
    assert default_signature_index(entries) == 1
    assert default_signature_index(deepcopy(entries)) == 1
    assert default_signature_index(entries, 'first') == 0
    assert default_signature_index(entries, 'orphan') == 2
    assert default_signature_index([]) == -1
    assert default_signature_index(entries[2:]) == 0


def routing_shell():
    identity = ('actual-B',)
    actual = {'count':1, 'signatures':[{'field':'B-real', 'intact':False}]}
    stale = {'count':1, 'signatures':[{'field':'A-real', 'intact':True}]}
    shell = SimpleNamespace(
        w=SimpleNamespace(_signature_audit=stale, active_slot=SimpleNamespace(currentText=lambda:'B'),
                          docs={'A':Path('A.pdf'), 'B':Path('B.pdf')}),
        _signature_cache={'B':(identity,actual,None)}, certificate=Mock(values={}),
        _refresh_signature_sources=Mock(), signature_context=Mock())
    shell._cached_signature = lambda slot: shell._signature_cache.get(slot)
    return shell, actual, stale


def test_display_uses_active_side_cache_and_never_writes_it():
    shell, actual, stale = routing_shell()
    before = dict(shell._signature_cache)
    WorkbenchShell.update_signature(shell)
    assert shell.w._signature_audit is actual
    assert shell._signature_cache == before
    assert shell._signature_cache['B'][1] is actual
    assert shell._audit_owner == ('B', ('actual-B',))
    shell.certificate.set_signature.assert_called_once_with(actual['signatures'][0])
    assert stale['signatures'][0]['intact'] is True


def test_old_side_clicked_row_is_not_shown_as_current_side():
    shell, actual, stale = routing_shell()
    WorkbenchShell.update_signature(shell, stale['signatures'][0])
    shell.certificate.set_signature.assert_called_once_with(actual['signatures'][0])


def test_no_current_report_does_not_reuse_global_a_report():
    shell, _, _ = routing_shell()
    shell._signature_cache.clear()
    WorkbenchShell.update_signature(shell)
    assert shell.w._signature_audit is None
    shell.certificate.clear.assert_called_once()
    shell.certificate.set_signature.assert_not_called()
    assert not shell._signature_cache


def test_legacy_summary_follows_actual_selected_signature():
    shell, actual, _ = routing_shell()
    actual['signatures'].append({'field':'B-last', 'intact':True})
    shell.signature_comparison = SimpleNamespace(panes={'B':SimpleNamespace(_selected=lambda:{'field':'B-last'})})
    WorkbenchShell.update_signature(shell)
    shell.certificate.set_signature.assert_called_once_with(actual['signatures'][-1])


def test_unlinked_data_is_not_reported_as_no_signature():
    shell, actual, _ = routing_shell()
    actual['count']=0
    actual['signatures']=[]
    actual['full_evidence']={'signatures':[{'discovery':{'field_tree_linked':False}}]}
    WorkbenchShell.update_signature(shell)
    shell.certificate.state.setText.assert_called_once()
    assert '已读取 1 项签名数据' in shell.certificate.state.setText.call_args.args[0]


def test_a_template_does_not_populate_b_actual_information_cards():
    template={'label':'CN=Reference@QPDF-demo', 'preview_entry':{'field':'A-signature',
              'cms_length':1234,'byte_range':[0,10,30,50],
              'signer_certificate':{'subject':'CN=Source A','public_key':{'bits':4096}}},
              'preview_audit':{'intact':True}}
    cards={index:Mock() for index in (0,1,2,5)}
    pane=SimpleNamespace(_card_ready=True, _selected=lambda:{}, _audit=lambda:{},
                         generated_credentials=None, reference_template=template,
                         cards=cards, state=Mock(), operation_note=Mock(),
                         _base_state='B 未签名', _refresh_detail=Mock())
    SignatureInformationPane._render_tab(pane,0)
    for card in cards.values():
        rows,note=card.set_rows.call_args.args
        assert all(value is None for _,value in rows)
        assert 'Source A' not in str(rows)
    assert '不代表 B 已包含' in pane.operation_note.setText.call_args.args[0]
    pane._base_state = '读取失败，不能断言无签名：ValueError: synthetic'
    SignatureInformationPane._render_tab(pane, 0)
    failed = pane.operation_note.setText.call_args.args[0]
    assert failed.startswith('读取失败')
    assert '不代表 B 已包含' not in failed


def test_name_reformatting_never_changes_report_bytes_hash_or_values():
    report={'full_evidence':{'source_path':'F:/sample/A.pdf','source_size':123,
                            'source_sha256':'actual-file-hash',
                            'signatures':[{'signer_certificate':{'subject':'CN=Demo@ID,O=Example'}}]}}
    before=deepcopy(report)
    display_name_fields(report)
    assert report == before
    assert report_source_details(report)['实际文件_SHA256']=='actual-file-hash'
    assert report_source_details(report)['实际读取文件']=='F:/sample/A.pdf'


def test_same_size_and_mtime_replaced_file_does_not_reuse_cache(tmp_path):
    path=tmp_path/'B.pdf'
    path.write_bytes(b'aaaa')
    shell=SimpleNamespace(w=SimpleNamespace(docs={'B':path}))
    old=WorkbenchShell._signature_identity(shell,'B')
    stat=path.stat()
    replacement=tmp_path/'replacement.pdf'
    replacement.write_bytes(b'bbbb')
    os.utime(replacement,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    os.replace(replacement,path)
    assert WorkbenchShell._signature_identity(shell,'B') != old
