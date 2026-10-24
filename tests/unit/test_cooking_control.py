from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import threading
import tkinter as tk

import pytest

from utils.drivers.input_task_gate import InputTaskGate
from utils.ui.cooking_control import CookingControl
from utils.ui.cooking_dialog import CookingDialog
from tests.unit.test_cooking import session, stock


def test_input_gate_rejects_concurrent_tasks_and_releases_after_failure():
    gate = InputTaskGate()
    with pytest.raises(RuntimeError):
        with gate.claim('cooking'):
            with pytest.raises(ValueError):
                with gate.claim('replay'):
                    pytest.fail('Both tasks acquired input')
            raise RuntimeError('worker failure')
    assert gate.owner is None
    with gate.claim('replay'):
        assert gate.owner == 'replay'


def test_log_deduplicates_one_pot_and_keeps_input_baseline(tmp_path):
    control = CookingControl(tmp_path/'profile.json', tmp_path)
    control.stream = (tmp_path/'events.jsonl').open('w', encoding='utf-8')
    s = session(); s.update(SimpleNamespace(stage='menu'), stock(), 0)
    for _ in range(3): control.update('cooking', 'starting', **s.record())
    control.stream.close(); control.stream = None
    rows = (tmp_path/'events.jsonl').read_text('utf-8').splitlines()
    assert len(rows) == 1
    data = json.loads(rows[0])
    assert data['pot']['inventory_before'][str(100)] == 0
    assert len(control.pots) == 1


def test_popup_builds_without_input_or_unsupported_widget_options(tmp_path):
    root = tk.Tk(); root.withdraw()
    control = Mock(active=False)
    control.take_latest.return_value = None
    try:
        dialog = CookingDialog(root, control, 123, tmp_path/'local.json', tmp_path/'not-present.json')
        dialog.top.withdraw()
        assert dialog.count.get() == '1'
        assert not dialog.confirm.get()
        dialog.close()
        control.stop.assert_called_once()
        control.start.assert_not_called()
    finally:
        root.destroy()


def test_existing_input_task_prevents_cooking_start_without_reader_or_device(tmp_path):
    control = CookingControl(tmp_path/'profile.json', tmp_path, busy=lambda: True, runner=Mock(),
                             reader_factory=Mock(), device_factory=Mock())
    with pytest.raises(ValueError, match='任务'):
        control.start(123, {})
    control.reader_factory.assert_not_called()
    control.device_factory.assert_not_called()


def test_worker_failure_releases_device_controls_reader_and_gate(tmp_path):
    gate = InputTaskGate()
    device, controls, reader = Mock(), Mock(), Mock()
    control = CookingControl(tmp_path/'profile.json', tmp_path, input_gate=gate,
        device_factory=lambda window: device, controls_factory=lambda *args: controls,
        reader_factory=lambda *args, **kw: reader, runner=Mock(side_effect=RuntimeError('failed')))
    window = SimpleNamespace(hwnd=1, size=lambda: [1366, 768])
    control._run(123, window, SimpleNamespace(observe=lambda image: None), session(), 1)
    device.release_all.assert_called_once()
    controls.close.assert_called_once()
    reader.close.assert_called_once()
    assert gate.owner is None
    assert control.stream is None
    assert json.loads((control.path/'summary.json').read_text('utf-8'))['task_state'] == 'error'
