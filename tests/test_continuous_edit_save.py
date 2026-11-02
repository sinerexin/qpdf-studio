"""累计预览与保存收尾回归用例；只使用临时测试数据，不创建 GUI 窗口。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pdf_studio.qt_context_editor import ContextSelectionEditor
from pdf_studio.working_copy import save_working_copy


def queue_editor(**changes):
    # 用普通代理测试状态机，不启动 QApplication 或桌面程序。
    values = dict(_editing_finished=False,
                  workbench=SimpleNamespace(_busy=False),
                  _queue_timer=Mock(), _preview_requested=False,
                  _finish_requested=False, _queued_selection=None,
                  save_state=Mock(), preview_edit=Mock(),
                  _finish_editing=Mock(), _select_now=Mock(),
                  has_pending_edits=lambda: False)
    values.update(changes)
    return SimpleNamespace(**values)


def test_next_a_selection_waits_for_explicit_preview():
    editor = queue_editor(has_pending_edits=lambda: True)
    point = (Path('A.pdf'), 1, 12, 24)
    ContextSelectionEditor.select_at(editor, *point)
    assert editor._queued_selection == point
    ContextSelectionEditor._continue_actions(editor)
    editor.preview_edit.assert_not_called()
    editor._select_now.assert_not_called()
    # 用户点预览后才提交；之前的输入未被下一次点击直接覆盖。
    editor._preview_requested = True
    ContextSelectionEditor._continue_actions(editor)
    editor.preview_edit.assert_called_once()


def test_finished_preview_continues_queued_a_selection():
    point = (Path('A.pdf'), 2, 12, 24)
    editor = queue_editor(_queued_selection=point)
    ContextSelectionEditor._continue_actions(editor)
    editor._select_now.assert_called_once_with(*point)
    assert editor._queued_selection is None


def test_finish_first_flushes_latest_input_then_enters_third_step():
    editor = queue_editor(has_pending_edits=lambda: True, _finish_requested=True)
    ContextSelectionEditor._continue_actions(editor)
    editor.preview_edit.assert_called_once()
    editor._finish_editing.assert_not_called()
    editor.has_pending_edits = lambda: False
    ContextSelectionEditor._continue_actions(editor)
    editor._finish_editing.assert_called_once_with()


def test_busy_preview_request_is_kept():
    editor = queue_editor(workbench=SimpleNamespace(_busy=True))
    ContextSelectionEditor.preview_edit(editor)
    assert editor._preview_requested
    editor._queue_timer.start.assert_called_once_with(100)


def test_repeated_preview_without_new_input_is_not_applied_again():
    editor = queue_editor()
    ContextSelectionEditor.preview_edit(editor)
    assert not editor._preview_requested
    editor.preview_edit.assert_not_called()


def test_button_uses_current_b_and_navigates_without_export(tmp_path, monkeypatch):
    import pdf_studio.qt_context_editor as ui
    a, b = tmp_path/'A.pdf', tmp_path/'B.pdf'
    # 两个文件均不存在也能做状态接线测试：流程确认不读写 PDF。
    editor = queue_editor(_ignored_b_on_reset=None,
                          fields=Mock(), choice=Mock(), preview_button=Mock(),
                          save_button=Mock(), _preview_timer=Mock(),
                          _preview_note=Mock(), _cleanup_previews=Mock())
    editor.workbench.docs = {'A':a, 'B':b}
    editor.workbench.studio_shell = Mock()
    editor.workbench.load_path = Mock()
    dialog = Mock(side_effect=AssertionError('进入第三步不应弹出保存对话框'))
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', dialog)
    editor._continue_actions = lambda: ContextSelectionEditor._continue_actions(editor)
    editor._finish_editing = lambda: ContextSelectionEditor._finish_editing(editor)
    ContextSelectionEditor.save_edit(editor)
    assert editor._editing_finished
    assert editor._generated_result == b
    assert editor.workbench.docs == {'A':a, 'B':b}
    editor.workbench.studio_shell.advance_to_signing.assert_called_once()
    editor.workbench.load_path.assert_not_called()
    dialog.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_busy_finish_click_is_retained_until_preview_completes():
    editor = queue_editor(_preview_timer=Mock())
    editor.workbench._busy = True
    editor._continue_actions = lambda: ContextSelectionEditor._continue_actions(editor)
    ContextSelectionEditor.save_edit(editor)
    assert editor._finish_requested
    editor._finish_editing.assert_not_called()
    editor.workbench._busy = False
    ContextSelectionEditor._continue_actions(editor)
    editor._finish_editing.assert_called_once()


def test_copy_can_replace_existing_working_file_and_not_original(tmp_path):
    a, b, output = (tmp_path / name for name in ('A.pdf', 'B.pdf', 'saved.pdf'))
    a.write_bytes(b'original')
    b.write_bytes(b'first + second')
    output.write_bytes(b'old output')
    assert save_working_copy(b, output, a) == output
    assert output.read_bytes() == b'first + second'
    b.write_bytes(b'first + second + third')
    save_working_copy(b, output, a)
    assert output.read_bytes() == b'first + second + third'
    assert save_working_copy(output, output, a) == output
    assert a.read_bytes() == b'original'
    with pytest.raises(ValueError):
        save_working_copy(b, a, a)
    assert not list(tmp_path.glob('*.tmp'))


def test_failed_atomic_replace_keeps_previous_saved_copy(tmp_path, monkeypatch):
    import pdf_studio.working_copy as copies
    b, output = tmp_path/'B.pdf', tmp_path/'saved.pdf'
    b.write_bytes(b'new result')
    output.write_bytes(b'previous saved result')
    def fail_replace(*args):
        raise PermissionError('模拟文件被占用')
    monkeypatch.setattr(copies.os, 'replace', fail_replace)
    with pytest.raises(PermissionError):
        save_working_copy(b, output)
    assert output.read_bytes() == b'previous saved result'
    assert not list(tmp_path.glob('*.tmp'))
