"""Built-in patrol selection shares Start without changing saved route files."""
from unittest.mock import Mock

import pytest

from tests.unit.test_native_coordinate_monitor import capture_window
from utils.ui.native_coordinate_monitor import CoordinateWindow


def prepare(tmp_path):
    w = capture_window(tmp_path)
    w.save_point()
    w.route_menu = Mock()
    w.refresh_route_menu = lambda: CoordinateWindow.refresh_route_menu(w)
    w.refresh_route_menu()
    return w


def test_patrol_is_selectable_and_switching_back_keeps_saved_route(tmp_path):
    w = prepare(tmp_path)
    before = w.point_store.path.read_bytes()
    saved_name = w.route_name
    w.activate_route(w.patrol_route_label)
    assert w.patrol_route_selected and w.saved_points == []
    assert w.point_store.path.read_bytes() == before
    w.activate_route(saved_name)
    assert not w.patrol_route_selected and len(w.saved_points) == 1
    assert w.point_store.path.read_bytes() == before


def test_shared_start_uses_existing_joint_patrol_not_saved_points(tmp_path):
    w = prepare(tmp_path); w.patrol_route_selected = True
    w.start_special_gather = Mock()
    w.start_playback()
    w.start_special_gather.assert_called_once_with('both')
    w.playback.start.assert_not_called()


def test_patrol_label_cannot_shadow_user_saved_route(tmp_path):
    w = prepare(tmp_path); first = w.patrol_route_label
    w.routes.create(first)
    w.refresh_route_menu()
    assert w.patrol_route_label != first
    assert first in w.route_menu.configure.call_args.kwargs['values']
    assert w.patrol_route_label in w.route_menu.configure.call_args.kwargs['values']


@pytest.mark.parametrize('action', ['copy', 'rename', 'delete'])
def test_patrol_route_management_cannot_modify_previous_saved_route(tmp_path, monkeypatch, action):
    w = prepare(tmp_path); before = w.point_store.path.read_bytes()
    w.patrol_route_selected = True
    show = Mock(); confirm = Mock(return_value=True); prompt = Mock(return_value='other')
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', show)
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', confirm)
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.simpledialog.askstring', prompt)
    w.manage_route(action)
    assert w.point_store.path.read_bytes() == before
    confirm.assert_not_called(); prompt.assert_not_called(); show.assert_called_once()


@pytest.mark.parametrize('operation', ['clear_saved_points', 'save_point', 'edit_point_waits', 'clear_path_memory'])
def test_patrol_edits_do_not_mutate_previous_route(tmp_path, monkeypatch, operation):
    w = prepare(tmp_path); before = w.point_store.path.read_bytes()
    w.patrol_route_selected = True
    show = Mock(); confirm = Mock(return_value=True)
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.showerror', show)
    monkeypatch.setattr('utils.ui.native_coordinate_monitor.messagebox.askyesno', confirm)
    getattr(w, operation)()
    assert w.point_store.path.read_bytes() == before
    confirm.assert_not_called()
    show.assert_called_once()
