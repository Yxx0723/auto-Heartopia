"""Regression tests for the independent collection and cooking runtime logs."""
from datetime import datetime
from types import SimpleNamespace

from utils.ui.collection_log_panel import format_collection_event
from utils.ui.log_window import FullLogWindow
from utils.ui.native_coordinate_monitor import CoordinateWindow
from utils.ui.point_playback import PointPlayback


def test_collection_log_formats_waypoint_and_cycle_without_game_access():
    row = format_collection_event({
        'at': '2026-11-13T15:20:30', 'state': 'reached',
        'message': '到达目标，已确认采集',
        'waypoint': 4, 'waypoint_count': 12, 'cycle': 2, 'loop_count': 3,
    })
    assert row == ('● 15:20:30 | 循环 2/3 | 点位 4/12 | 已确认 | 到达目标，已确认采集\n', '已确认')
    assert format_collection_event({'state': 'playing', 'message': ''}) is None


def test_playback_log_retains_superseded_worker_reports():
    clock = lambda: datetime(2026, 11, 13, 15, 20, 30)
    playback = PointPlayback('unused.json', wall_clock=clock)
    playback.update('playing', '正在移动', waypoint=1)
    playback.update('collecting', '正在采集', waypoint=1)
    playback.update('reached', '已完成点位', waypoint=1)
    # The single latest status is intentionally coalesced, but the log isn't.
    assert playback.take_latest()['state'] == 'reached'
    assert [x['message'] for x in playback.take_log_events()] == [
        '正在移动', '正在采集', '已完成点位',
    ]
    assert playback.take_log_events() == []


class FakePane:
    def __init__(self):
        self.visible = False

    def pack(self, **kwargs):
        self.visible = True

    def pack_forget(self):
        self.visible = False


class FakeTitle:
    def __init__(self):
        self.value = ''

    def configure(self, **kwargs):
        self.value = kwargs['text']


class FakeFullLog:
    def __init__(self):
        self.changes = []

    def alive(self):
        return True

    def set_source(self, source, title):
        self.changes.append((source, title))


def test_module_switch_updates_both_log_views_and_keeps_map_selection():
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.active_module = 0
    window.log_module = None
    window.log_view = 'standard'
    window.collection_log = SimpleNamespace(runtime_page=FakePane())
    window.cooking_catalog = SimpleNamespace(runtime_page=FakePane())
    window.log_title = FakeTitle()
    window.full_log_window = FakeFullLog()

    window.sync_log_module()
    assert window.collection_log.runtime_page.visible
    assert not window.cooking_catalog.runtime_page.visible
    assert window.log_title.value == '运行日志 · 自动采集'

    window.active_module = 1
    window.sync_log_module()
    assert not window.collection_log.runtime_page.visible
    assert window.cooking_catalog.runtime_page.visible
    assert window.full_log_window.changes[-1][0] is window.cooking_catalog
    assert '自动烹饪' in window.full_log_window.changes[-1][1]

    window.active_module = 2
    window.sync_log_module()
    assert window.log_module == 'cooking'
    assert window.cooking_catalog.runtime_page.visible

    window.active_module = 0
    window.sync_log_module()
    assert window.log_module == 'collection'
    assert window.collection_log.runtime_page.visible
    assert window.full_log_window.changes[-1][0] is window.collection_log


def test_detached_log_switch_unsubscribes_old_source_and_subscribes_new_one():
    class FakeSource:
        def __init__(self):
            self.added = []
            self.removed = []

        def add_log_view(self, view):
            self.added.append(view)

        def remove_log_view(self, view):
            self.removed.append(view)

    old, new = FakeSource(), FakeSource()
    full = FullLogWindow.__new__(FullLogWindow)
    full.catalog = old
    names = []
    full.top = SimpleNamespace(title=lambda value: names.append(value))
    titles = []
    full.header_label = SimpleNamespace(configure=lambda **kwargs: titles.append(kwargs['text']))
    full.set_source(new, '运行日志 · 自动采集 · 完整记录')

    assert old.removed == [full]
    assert new.added == [full]
    assert '自动采集' in names[-1]
    assert '自动采集' in titles[-1]
