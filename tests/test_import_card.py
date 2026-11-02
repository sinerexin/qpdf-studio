"""Routing and URL classification only; no files, GUI windows or PDF writes."""
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QUrl
from pdf_studio.qt_import_card import dropped_pdf_path
from pdf_studio.qt_workbench_shell import WorkbenchShell


def test_drop_accepts_one_local_pdf_with_chinese_path():
    path = Path('F:/教学材料/原稿.PDF')
    assert dropped_pdf_path([QUrl.fromLocalFile(str(path))]) == path


def test_drop_rejects_remote_non_pdf_or_multiple_files():
    pdf = QUrl.fromLocalFile('F:/a.pdf')
    assert dropped_pdf_path([]) is None
    assert dropped_pdf_path([pdf, pdf]) is None
    assert dropped_pdf_path([QUrl('https://example.com/a.pdf')]) is None
    assert dropped_pdf_path([QUrl.fromLocalFile('F:/a.txt')]) is None


def test_import_routes_to_a_and_returns_to_editor():
    events = []
    shell = SimpleNamespace(
        _import_requested=True,
        w=SimpleNamespace(_busy=False,
            load_path=lambda slot, path: events.append(('load', slot, path))),
        show_workspace=lambda i: events.append(('workspace', i)))
    WorkbenchShell.import_original(shell, Path('F:/a.pdf'))
    assert events == [('load', 'A', Path('F:/a.pdf')), ('workspace', 0)]
    assert not shell._import_requested


def test_import_read_error_keeps_import_surface():
    errors = []
    def fail(*args):
        raise OSError('access denied')
    shell = SimpleNamespace(_import_requested=True,
        w=SimpleNamespace(_busy=False, load_path=fail, _error=errors.append))
    WorkbenchShell.import_original(shell, Path('F:/a.pdf'))
    assert shell._import_requested
    assert 'access denied' in errors[0]


def test_return_to_same_editor_tab_exits_import_view():
    events = []
    shell = SimpleNamespace(w=SimpleNamespace(workspace_tabs=SimpleNamespace(currentIndex=lambda: 0)),
                            select_workspace=lambda i: events.append(i))
    WorkbenchShell.show_workspace(shell, 0)
    assert events == [0]
