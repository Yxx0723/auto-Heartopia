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
        for geometry in ['980x700', '900x620']:
            root.geometry(geometry); tabs.select(0); root.update()
            assert tabs.winfo_rooty() > window.canvas.winfo_rooty()+window.canvas.winfo_height()
            assert window.canvas.winfo_width() > root.winfo_width()*.9
            assert window.route_menu.winfo_ismapped()
            assert window.special_gather_button.winfo_ismapped()
            assert window.save_point_button.grid_info()['row'] == 1
            assert window.clear_points_button.grid_info()['column'] == 3
            for widget in (window.clear_points_button, window.loop_count_spin, window.point_wait_button):
                assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()
            tabs.select(1); root.update()
            assert window.recovery_jump_check.winfo_ismapped()
            assert not window.route_menu.winfo_ismapped()
            assert not window.special_gather_button.winfo_ismapped()
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
    finally:
        if window is not None: window.close()
        else: root.destroy()
