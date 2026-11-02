"""Regression cases for Qt value storage and A/B display isolation.

These use in-memory stand-ins; no QApplication, PDF files or credentials.
"""
import json
from types import SimpleNamespace

from pdf_studio import qt_evidence_section as module
from pdf_studio.qt_workbench_shell import WorkbenchShell


class Item:
    def __init__(self, parent=None, labels=()):
        self.values = {}
        if parent is not None:
            parent.children.append(self)
        self.children = []
    def setData(self, column, role, value):
        assert type(value) is int and -(2 ** 63) <= value < 2 ** 63
        self.values[column, role] = value
    def data(self, column, role):
        return self.values.get((column, role))
    def setToolTip(self, *args):
        pass


def test_nested_certificate_values_never_cross_qt_as_big_integers(monkeypatch):
    monkeypatch.setattr(module, 'QTreeWidgetItem', Item)
    original = {'serial': 2 ** 159 + 7, 'negative': -(2 ** 100),
                'nested': [True, None, {'n': 2 ** 2048 + 1}]}
    section = SimpleNamespace(_nodes=0, _limited=False, _values={})
    section._add = lambda *args: module.EvidenceSection._add(section, *args)
    section._item_value = lambda item: module.EvidenceSection._item_value(section, item)
    root = Item()
    assert section._add(root, '证书', original)
    assert section._item_value(root.children[0]) is original
    serial_item = root.children[0].children[0]
    assert section._item_value(serial_item) == 2 ** 159 + 7
    assert str(2 ** 159 + 7) in module._text(original)
    assert json.loads(module._text(original)) == original


def test_placeholder_is_not_a_copyable_null_field():
    section = SimpleNamespace(_values={})
    assert module.EvidenceSection._item_value(section, None) is module._MISSING
    assert module.EvidenceSection._item_value(section, Item()) is module._MISSING


def test_b_display_failure_does_not_stop_a_or_other_b_pane():
    events = []
    def update(slot, *args):
        events.append(('inspector', slot))
        if slot == 'B':
            raise OverflowError()
    inspector = SimpleNamespace(update_slot=update, panes={
        slot: SimpleNamespace(show_display_error=lambda exc: events.append(('error', type(exc).__name__)))
        for slot in ('A', 'B')})
    central = SimpleNamespace(update_slot=lambda slot, *args: events.append(('center', slot)))
    shell = SimpleNamespace(
        signature_sources={slot: SimpleNamespace(setText=lambda text: None, setToolTip=lambda text: None)
                           for slot in ('A', 'B')},
        w=SimpleNamespace(docs={'A': None, 'B': None}, _notify=lambda text: events.append(('notice', text))),
        _cached_signature=lambda slot: None, _signature_identity=lambda slot: None,
        signature_comparison=inspector, signature_workspace=central)
    WorkbenchShell._refresh_signature_sources(shell)
    assert ('center', 'A') in events and ('center', 'B') in events
    assert ('error', 'OverflowError') in events
    assert any(kind == 'notice' and 'OverflowError' in value for kind, value in events)


def test_queue_continues_if_completion_display_raises(monkeypatch):
    import pytest
    from pdf_studio import signature_evidence
    monkeypatch.setattr(signature_evidence, 'read_signature_audit', lambda *args, **kwargs: {})
    callbacks = []
    events = []
    def broken_display():
        raise OverflowError()
    shell = SimpleNamespace(_mode=1, _signature_requests={'A', 'B'}, _signature_cache={}, _signature_reading={},
        _signature_identity=lambda slot: ('identity', slot), _cached_signature=lambda slot: None,
        _refresh_signature_sources=lambda: None,
        signature_slot_changed=broken_display,
        request_signature_reads=lambda: events.append('continue'),
        w=SimpleNamespace(_busy=False, _notify=lambda text: None, docs={'B': SimpleNamespace(name='b.pdf')},
            active_slot=SimpleNamespace(currentText=lambda: 'B'),
            signature_summary=SimpleNamespace(setText=lambda text: None),
            _task=lambda desc, fn, callback, **kwargs: callbacks.append(callback)))
    WorkbenchShell._read_next_signature(shell)
    with pytest.raises(OverflowError):
        callbacks[0]({'count': 1})
    assert events == ['continue']
    assert shell._signature_cache['B'][1] == {'count': 1}
    assert 'A' in shell._signature_requests
