"""Runtime log buffering and Windows Tk module-switch integration."""
from datetime import datetime
import tkinter as tk
from unittest.mock import Mock

from tests.unit.test_native_coordinate_monitor import FakeMonitor
from utils.ui.native_coordinate_monitor import CoordinateWindow, MonitorWorker
from utils.ui.point_playback import PointPlayback


def test_playback_log_keeps_intermediate_events_without_changing_status_or_clock(tmp_path):
    clock = Mock(return_value=datetime(2026, 11, 13))
    player = PointPlayback(tmp_path/'profile.json', wall_clock=clock)
    player.update('playing', '前往点位', waypoint=1)
    player.update('playing', '前往点位', waypoint=1)
    player.update('collecting', '准备按 F', waypoint=1)
    player.update('collection_wait', '等待 2 秒', waypoint=1)
    player.update('collection_wait', '等待 1 秒', waypoint=1)
    player.update('reached', '已完成采集与等待 1/1', waypoint=1)
    player.update('finished', '回放结束')
    assert player.take_latest()['state'] == 'finished'
    assert player.take_latest() is None
    assert [e['state'] for e in player.take_log_events()] == [
        'playing', 'collecting', 'collection_wait', 'reached', 'finished']
    clock.assert_called_once_with()


def test_playback_log_overflow_is_bounded_and_reported(tmp_path):
    player = PointPlayback(tmp_path/'profile.json')
    for index in range(2003):
        player.record_log('preparation', str(index))
    events = player.take_log_events(3)
    assert '3 条' in events[0]['message']
    assert [e['message'] for e in events[1:]] == ['3', '4']
    assert len(player.take_log_events(3000)) == 1998


def test_module_logs_keep_history_and_move_one_full_window(tmp_path, monkeypatch):
    monkeypatch.setattr(MonitorWorker, 'start', lambda self: None)
    root = tk.Tk()
    window = None
    try:
        window = CoordinateWindow(root, FakeMonitor(), tmp_path, point_path=tmp_path/'points.json')
        collection, cooking = window.collection_log, window.cooking_catalog
        playback, controller = window.playback, window.cooking
        assert '暂无运行记录' in collection.status.cget('text')
        assert window.log_title.cget('text') == '运行日志 · 自动采集'
        collection.apply_events([{'state': 'reached', 'message': '已完成采集与等待 1/1'}])
        cooking.apply_log_events([{'state': 'batch_wait', 'message': '烹饪测试记录'}])
        full = window.open_full_log()
        assert '产出未确认' in full.text.get('1.0', 'end')
        for module, source in ((2, collection), (1, cooking), (2, cooking),
                               (0, collection), (1, cooking), (0, collection)):
            window.select_module(module)
            root.update_idletasks()
            assert full.catalog is source
            assert window.open_full_log() is full
            assert collection.views == ([full] if source is collection else [])
            assert cooking.log_views == ([full] if source is cooking else [])
        assert collection.page.winfo_ismapped()
        assert not cooking.runtime_page.winfo_ismapped()
        cooking.apply_result({'state': 'batch_preparing', 'batch': {
            'target_pots': 10, 'current_pot': 3, 'completed_pots': 2, 'total_products': 2}})
        cooking.apply_log_events([{'state': 'batch_wait', 'message': '后台烹饪记录'}])
        assert '后台烹饪记录' not in full.text.get('1.0', 'end')
        window.select_module(1)
        root.update_idletasks()
        assert cooking.runtime_page.winfo_ismapped()
        assert not collection.page.winfo_ismapped()
        assert cooking.batch_progress.get() == '目标 10 · 当前 3 · 已完成 2 · 成品 +2'
        assert '后台烹饪记录' in full.text.get('1.0', 'end')
        collection.apply_events([{'message': '后台采集记录'}])
        assert '后台采集记录' not in full.text.get('1.0', 'end')
        window.control_tabs.select(0)
        root.update()
        assert '后台采集记录' in full.text.get('1.0', 'end')
        assert window.playback is playback and window.cooking is controller
        full.close()
        assert not collection.views and not cooking.log_views
        collection.apply_events([{'message': '关闭后继续追加'}])
        assert '关闭后继续追加' in window.open_full_log().text.get('1.0', 'end')
        for first in range(0, 2100, 100):
            collection.apply_events([
                {'state': 'playing', 'message': f'事件 {i}'} for i in range(first, first + 100)
            ])
        assert len(collection.history) == 2000
        assert len(collection.text.get('1.0', 'end-1c').splitlines()) == 500
        assert len(window.full_log_window.text.get('1.0', 'end-1c').splitlines()) == 2000
        assert '事件 2099' in window.full_log_window.text.get('1.0', 'end')
        for geometry in ('490x900', '460x860', '1024x900'):
            root.geometry(geometry)
            root.update_idletasks()
            assert window.log_full_button.winfo_ismapped()
            assert window.log_full_button.winfo_rootx()+window.log_full_button.winfo_width() <= (
                window.global_log_host.winfo_rootx()+window.global_log_host.winfo_width())
    finally:
        if window is not None:
            window.close()
        else:
            root.destroy()
