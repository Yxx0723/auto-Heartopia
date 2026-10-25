"""Exercise compact tab/group geometry with no reader thread or game input."""
import tkinter as tk

from tests.unit.test_native_coordinate_monitor import FakeMonitor
from utils.ui.native_coordinate_monitor import CoordinateWindow, MonitorWorker


def test_compact_tabs_groups_and_map_restore_at_both_window_sizes(tmp_path, monkeypatch):
    monkeypatch.setattr(MonitorWorker, 'start', lambda self: None)
    root = tk.Tk()
    window = None
    try:
        window = CoordinateWindow(root, FakeMonitor(), tmp_path, point_path=tmp_path/'points.json')
        tabs = window.control_tabs
        assert [tabs.tab(tab, 'text') for tab in tabs.tabs()] == ['采集设定', '参数设定', '地图连接']
        for geometry in ['490x760', '460x740']:
            root.geometry(geometry); tabs.select(0); root.update()
            assert tabs.winfo_rooty() > window.canvas.winfo_rooty()+window.canvas.winfo_height()
            assert window.canvas.winfo_width() > root.winfo_width()*.9
            assert window.route_menu.winfo_ismapped()
            assert not hasattr(window, 'special_gather_button')
            assert window.patrol_route_label in window.route_menu.cget('values')
            assert window.save_point_button.grid_info()['row'] == 1
            assert window.clear_points_button.grid_info()['column'] == 3
            for widget in (window.clear_points_button, window.loop_count_spin, window.point_wait_button,
                           window.play_button, window.stop_button):
                assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()
                assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()
            assert window.canvas.winfo_height() >= 100
            tab_y = tabs.winfo_rooty()
            window.toggle_scene_filters(); root.update()
            for widget in window.scene_filter_buttons.values():
                assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()
                assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()
            assert tabs.winfo_rooty() == tab_y
            window.toggle_scene_filters(); root.update()
            tabs.select(1); root.update()
            assert window.recovery_jump_check.winfo_ismapped()
            assert not window.route_menu.winfo_ismapped()
            tabs.select(2); root.update()
            sidebar = window.sidebar_canvas.master
            assert sidebar.winfo_ismapped()
            old_y = tabs.winfo_rooty()
            window.toggle_map_size(); root.update()
            assert not tabs.winfo_ismapped()
            window.toggle_map_size(); root.update()
            assert sidebar.winfo_ismapped() and tabs.winfo_rooty() == old_y
        assert window.special_press_var.get() == '3'
        assert window.scene_radius == 150
        window.activate_route(window.patrol_route_label)
        window.view.position = [1., 2., 3.]
        window.render()
        assert window.play_button.cget('state') == 'normal'
        assert window.save_point_button.cget('state') == 'disabled'
        assert window.clear_points_button.cget('state') == 'disabled'
        assert window.route_buttons['delete'].cget('state') == 'disabled'
        assert window.fields['saved_count'].get() == '内置巡查路线'
    finally:
        if window is not None: window.close()
        else: root.destroy()
