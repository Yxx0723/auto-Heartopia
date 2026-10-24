"""Exercise real Tk geometry with no reader thread or game input."""
import tkinter as tk

from tests.unit.test_native_coordinate_monitor import FakeMonitor
from utils.ui.native_coordinate_monitor import CoordinateWindow, MonitorWorker


def test_connection_panel_is_below_map_left_of_route_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(MonitorWorker, 'start', lambda self: None)
    root = tk.Tk()
    window = None
    try:
        window = CoordinateWindow(root, FakeMonitor(), tmp_path, point_path=tmp_path/'points.json')
        for geometry in ['980x700', '900x620']:
            root.geometry(geometry)
            root.update()
            sidebar = window.sidebar_canvas.master
            assert sidebar.winfo_rooty() > window.canvas.winfo_rooty()+window.canvas.winfo_height()
            assert sidebar.winfo_rootx() < window.route_menu.winfo_rootx()
            assert window.canvas.winfo_width() > root.winfo_width()*.9
            for widget in (window.clear_points_button, window.loop_count_spin, window.point_wait_button,
                           window.recovery_jump_check, window.clear_path_memory_button):
                assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()
            old_y = sidebar.winfo_rooty()
            window.toggle_map_size(); root.update()
            assert not sidebar.winfo_ismapped()
            window.toggle_map_size(); root.update()
            assert sidebar.winfo_ismapped() and sidebar.winfo_rooty() == old_y
    finally:
        if window is not None: window.close()
        else: root.destroy()
