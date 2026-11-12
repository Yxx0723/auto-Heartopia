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
        assert not hasattr(dialog, 'confirm')
        assert '默认食谱' in dialog.top.title()
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
    window = SimpleNamespace(hwnd=1, size=lambda: [1366, 768], activate=lambda: True)
    control._run(123, window, SimpleNamespace(observe=lambda image: None), session(), 1)
    device.release_all.assert_called_once()
    controls.close.assert_called_once()
    reader.close.assert_called_once()
    assert gate.owner is None
    assert control.stream is None
    assert json.loads((control.path/'summary.json').read_text('utf-8'))['task_state'] == 'error'

@pytest.mark.parametrize('state', ['inventory_ready', 'inventory_error'])
def test_identical_inventory_results_survive_dedup_and_ui_polling(tmp_path, state):
    control = CookingControl(tmp_path/'profile.json', tmp_path)
    for _ in range(2):
        control.update(state, 'same result', inventory=None)
    control.update('playing', 'ordinary event')
    assert [control.take_latest()['state'] for _ in range(3)] == [state, state, 'playing']
    assert control.take_latest() is None


def test_inventory_refresh_repeats_reports_failure_and_recovers(tmp_path):
    from utils.ui.cooking_selection_control import CookingSelectionControl

    reader = Mock()
    stocks = [{'counts': {100: n}} for n in (2, 2, 2, 2, 3)]
    reader.cooking_inventory_snapshot.side_effect = [
        *stocks, OSError('process disappeared'), stocks[-1],
    ]
    control = CookingSelectionControl(tmp_path/'profile.json', tmp_path,
                                      reader_factory=Mock(return_value=reader))
    for expected in [*stocks, None, stocks[-1]]:
        assert control.read_inventory(123) is True
        control.inventory_thread.join(2)
        assert not control.inventory_thread.is_alive()
        result = control.take_latest()
        assert result['inventory'] == expected
        if expected is None:
            assert result['state'] == 'inventory_error'
            assert 'process disappeared' in result['message']
        else:
            assert result['state'] == 'inventory_ready'
    assert reader.close.call_count == 7


def test_refresh_button_preserves_stock_during_read_and_handles_failure(tmp_path, monkeypatch):
    from utils.ui.cooking_catalog_panel import CookingCatalogPanel
    from utils.ui.cooking_selection_control import CookingSelectionControl

    release = threading.Event()
    reader = Mock()
    latest = {'counts': {100: 7}}
    def snapshot(_):
        assert release.wait(2)
        return latest
    reader.cooking_inventory_snapshot.side_effect = snapshot
    control = CookingSelectionControl(tmp_path/'profile.json', tmp_path,
                                      reader_factory=Mock(return_value=reader))
    old_stock = {'counts': {100: 2}}
    panel = CookingCatalogPanel.__new__(CookingCatalogPanel)
    status = {'pid': 123}
    panel.owner = SimpleNamespace(view=SimpleNamespace(status=status), cooking=control, root=None)
    panel.inventory, panel.stock_button, panel.show_detail = old_stock, Mock(), Mock()

    control.thread = Mock()
    control.thread.is_alive.return_value = True
    panel.refresh_inventory()
    assert panel.inventory is old_stock and control.inventory_thread is None
    panel.stock_button.configure.assert_not_called()
    control.thread = None

    panel.refresh_inventory()
    thread = control.inventory_thread
    try:
        panel.refresh_inventory()
        assert thread is control.inventory_thread
        assert panel.inventory is old_stock
        panel.show_detail.assert_not_called()
        panel.stock_button.configure.assert_called_once_with(text='正在读取背包材料…')
    finally:
        release.set()
        thread.join(2)
    assert control.reader_factory.call_count == 1
    panel.apply_result(control.take_latest())
    assert panel.inventory == latest
    panel.stock_button.configure.assert_called_with(text='刷新背包材料')

    reader.cooking_inventory_snapshot.side_effect = OSError('process disappeared')
    panel.refresh_inventory()
    control.inventory_thread.join(2)
    panel.apply_result(control.take_latest())
    assert panel.inventory is None
    panel.stock_button.configure.assert_called_with(text='读取失败 · 点击重试')

    status.clear()
    error = Mock()
    monkeypatch.setattr('utils.ui.cooking_catalog_panel.messagebox.showerror', error)
    panel.refresh_inventory()
    error.assert_called_once()
    assert control.reader_factory.call_count == 2
