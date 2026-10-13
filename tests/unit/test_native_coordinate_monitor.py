"""Worker lifecycle tests use a fake reader and never create a desktop window."""
from datetime import datetime
from unittest.mock import Mock

from utils.core.coordinate_monitor_view import MonitorView
from utils.drivers.coordinate_point_store import PointStore
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.ui.native_coordinate_monitor import CoordinateWindow, MonitorWorker


class FakeMonitor:
    def __init__(self):
        self.calls = 0
        self.reconnections = 0
        self.closed = False

    def status(self):
        self.calls += 1
        return {'state': 'connected', 'sequence': self.calls}

    def reconnect(self):
        self.reconnections += 1

    def close(self):
        self.closed = True


def test_worker_keeps_only_latest_result_and_pauses_reads():
    monitor = FakeMonitor()
    worker = MonitorWorker(monitor)
    worker.poll_once()
    worker.poll_once()
    assert worker.take_latest()['sequence'] == 2
    assert worker.take_latest() is None
    worker.set_paused(True)
    worker.poll_once()
    assert monitor.calls == 2
    worker.set_paused(False)
    worker.reconnect()
    worker.poll_once()
    assert monitor.reconnections == 1
    assert worker.take_latest()['sequence'] == 3


def test_worker_stop_has_a_clear_exit_and_closes_reader():
    monitor = FakeMonitor()
    worker = MonitorWorker(monitor)
    worker.stop_event.set()
    worker.run()
    assert monitor.closed
    assert monitor.calls == 0


def capture_window(tmp_path):
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.view = MonitorView()
    window.view.accept({'state': 'connected', 'session': 'a', 'sequence': 1,
                        'sample_at': '2026-10-11T22:30:00+08:00',
                        'position': [-45.5, 23.25, 128.2]}, 10.)
    window.clock = lambda: 10.1
    window.wall_clock = lambda: datetime.fromisoformat('2026-10-11T22:31:00+08:00')
    window.point_store = PointStore(tmp_path / '点位.json')
    window.routes = PointRoutes(window.point_store.path)
    window.route_name = '点位'
    window.route_var = Mock()
    window.refresh_route_menu = Mock()
    window.saved_points = []
    window.playback = Mock(active=False)
    window.playback_status = {'state': 'idle', 'message': ''}
    window.start_mode_var = Mock()
    window.start_mode_var.get.return_value = '完整回放'
    window.recovery_count_var = Mock()
    window.recovery_count_var.get.return_value = '3'
    window.recovery_jump_var = Mock()
    window.recovery_jump_var.get.return_value = False
    window.collect_wait_var = Mock()
    window.collect_wait_var.get.return_value = '3.0'
    window.loop_count_var = Mock()
    window.loop_count_var.get.return_value = '1'
    window.points_error = ''
    window.hint = Mock()
    window.render = Mock()
    window.root = Mock()
    window.wait_editor = None
    return window


def test_save_button_appends_to_disk_then_updates_count(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    assert len(window.saved_points) == 1
    assert window.point_store.load()['points'] == window.saved_points
    assert window.saved_points[0]['position'] == [-45.5, 23.25, 128.2]
    window.render.assert_called_once()
    assert '点位 1' in window.hint.configure.call_args.kwargs['text']


def test_save_button_reports_write_error_without_claiming_success(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.point_store.append = Mock(side_effect=PermissionError('不可写'))
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.save_point()
    assert window.saved_points == []
    assert not window.point_store.path.exists()
    error.assert_called_once()
    assert '添加失败' in window.hint.configure.call_args.kwargs['text']


def test_save_button_checks_freshness_at_click_time(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.clock = lambda: 12.
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.save_point()
    assert window.saved_points == []
    assert not window.point_store.path.exists()
    assert window.view.position is None
    error.assert_called_once()


def test_playback_button_uses_saved_file_order_and_current_game_pid(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    window.save_point()
    window.view.status['pid'] = 77
    window.start_playback()
    data, pid = window.playback.start.call_args.args
    assert pid == 77
    assert [point['id'] for point in data['points']] == [1, 2]
    assert window.playback_status['state'] == 'playback_wait'


def test_empty_points_cannot_start_playback(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.start_playback()
    window.playback.start.assert_not_called()
    error.assert_called_once()


def test_stop_button_requests_stop_without_blocking_ui(tmp_path):
    window = capture_window(tmp_path)
    window.stop_playback()
    window.playback.stop.assert_called_once()
    assert window.playback_status['state'] == 'stopping'


def test_nearest_mode_is_passed_to_worker_without_selecting_from_ui_cached_position(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    window.view.status['pid'] = 77
    window.start_mode_var.get.return_value = '最近点接入'
    window.start_playback()
    assert window.playback.start.call_args.kwargs == {'start_mode': 'nearest',
                                                     'recovery_attempts': 3, 'jump_on_recovery': False,
                                                     'collect_wait_seconds': 3., 'loop_count': 1}


def test_ui_passes_loop_count_and_displays_requested_rounds(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    window.view.status['pid'] = 77
    window.loop_count_var.get.return_value = '3'
    window.start_playback()
    assert window.playback.start.call_args.kwargs['loop_count'] == 3
    assert '3' in window.playback_status['message']


def test_ui_rejects_invalid_loop_count_without_starting_playback(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    window.loop_count_var.get.return_value = '0'
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.start_playback()
    window.playback.start.assert_not_called()
    error.assert_called_once()


def test_ui_saves_collection_wait_and_passes_default_to_worker(tmp_path):
    window = capture_window(tmp_path)
    window.collect_wait_var.get.return_value = '4.5'
    window.save_point()
    assert window.saved_points[0]['collect_wait'] == 4.5
    window.view.status['pid'] = 77
    window.start_playback()
    assert window.playback.start.call_args.kwargs['collect_wait_seconds'] == 4.5


def test_ui_passes_selected_recovery_options_to_worker(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    window.view.status['pid'] = 77
    window.recovery_count_var.get.return_value = '5'
    window.recovery_jump_var.get.return_value = True
    window.start_playback()
    assert window.playback.start.call_args.kwargs['recovery_attempts'] == 5
    assert window.playback.start.call_args.kwargs['jump_on_recovery'] is True


def test_semantic_ce_export_uses_current_live_address_and_never_copies_the_old_table(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(locator='mono-local-player', pid=77, address=0x123450)
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    window.export_ct()
    assert '123450' in file.read_text(encoding='utf-8')
    assert '77' in file.read_text(encoding='utf-8')


def test_semantic_ce_export_refuses_expired_display_data(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(locator='mono-local-player', pid=77, address=0x123450)
    window.clock = lambda: 100.
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.export_ct()
    assert not file.exists()
    error.assert_called_once()


def test_disconnected_ce_export_never_exports_an_old_bundled_pointer_table(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.resources = tmp_path
    (tmp_path / 'coordinates.CT').write_text('stale pointer table', encoding='utf-8')
    window.view.accept({'state': 'disconnected', 'position': None}, 10.1)
    file = tmp_path / 'export.CT'
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.export_ct()
    assert not file.exists()
    error.assert_called_once()


def test_legacy_live_reader_exports_the_current_observation_without_a_static_template(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(pid=77, address=0x234560)
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    window.export_ct()
    assert '234560' in file.read_text(encoding='utf-8')


def test_switch_route_uses_only_selected_points_for_saving_waits_and_playback(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    window.routes.create('苹果')
    window.switch_route('苹果')
    assert window.route_name == '苹果' and window.saved_points == []
    window.view.status['pid'] = 77
    window.save_point()
    window.start_playback()
    data, pid = window.playback.start.call_args.args
    assert pid == 77 and len(data['points']) == 1
    assert window.point_store.path.name == '苹果.json'
    assert (tmp_path / '点位.json').read_bytes() == original


def test_route_switch_during_playback_is_rejected_without_changing_store(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    window.routes.create('蓝莓')
    window.playback.active = True
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.point_store.path.name == '点位.json'
    error.assert_called_once()


def test_failed_route_selection_keeps_current_points_and_wait_editor_store(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    before = window.saved_points.copy()
    (tmp_path / '蓝莓.json').write_text('{bad', encoding='utf-8')
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', Mock())
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.saved_points == before
    assert window.routes.selected_name() == '点位'


def test_new_and_save_as_route_buttons_keep_original_points(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    ask = Mock(side_effect=['苹果', '蓝莓'])
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.simpledialog.askstring', ask)
    window.manage_route('copy')
    assert window.route_name == '苹果' and len(window.saved_points) == 1
    window.manage_route('new')
    assert window.route_name == '蓝莓' and window.saved_points == []
    assert (tmp_path / '点位.json').read_bytes() == original


def test_delete_route_requires_user_confirmation_and_archives_only_current_file(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    window.routes.create('苹果', window.point_store.load())
    window.switch_route('苹果')
    confirm = Mock(side_effect=[False, True])
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', confirm)
    window.manage_route('delete')
    assert window.route_name == '苹果' and window.point_store.path.exists()
    window.manage_route('delete')
    assert window.route_name == '点位' and not (tmp_path / '苹果.json').exists()
    assert (tmp_path / '点位.json').read_bytes() == original
    assert len(list((tmp_path / '路线回收站').glob('*.json'))) == 1


def test_route_switch_while_wait_editor_is_open_cannot_retarget_its_callback(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    window.routes.create('蓝莓')
    window.wait_editor = Mock()
    window.wait_editor.root.winfo_exists.return_value = True
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.point_store.path.name == '点位.json'
    error.assert_called_once()


def test_clear_saved_points_confirms_current_route_and_refreshes_preview(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    window.routes.create('蓝莓', window.point_store.load())
    confirm = Mock(side_effect=[False, True])
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', confirm)
    window.clear_saved_points()
    assert window.point_store.path.read_bytes() == original and len(window.saved_points) == 1
    window.clear_saved_points()
    assert window.route_name == '点位' and window.point_store.load()['points'] == []
    assert window.saved_points == [] and window.view.trail == []
    assert window.routes.store('蓝莓').path.read_bytes() == original
    assert window.playback_status['state'] == 'idle'
    assert '点位' in confirm.call_args.args[1]
    assert len(list((tmp_path / '路线回收站').glob('*.json'))) == 1


def test_clear_points_while_playing_or_editing_is_rejected_before_confirmation(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    before = window.point_store.path.read_bytes()
    confirm, error = Mock(), Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', confirm)
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.playback.active = True
    window.clear_saved_points()
    window.playback.active = False
    window.wait_editor = Mock()
    window.wait_editor.root.winfo_exists.return_value = True
    window.clear_saved_points()
    assert confirm.call_count == 0 and error.call_count == 2
    assert window.point_store.path.read_bytes() == before and len(window.saved_points) == 1


def test_failed_clear_keeps_visible_and_persisted_points(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    before = window.point_store.path.read_bytes()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', Mock(return_value=True))
    error = Mock()
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', error)
    window.routes.clear_points = Mock(side_effect=PermissionError('busy'))
    window.clear_saved_points()
    assert window.point_store.path.read_bytes() == before and len(window.saved_points) == 1
    error.assert_called_once()
