"""Unchanged frames and drag storms must not repeatedly repaint the map."""
from types import SimpleNamespace
from unittest.mock import Mock
import tkinter as tk

from utils.ui.native_coordinate_monitor import CoordinateWindow, DisplayValue, configure_changed


def window():
    ui = CoordinateWindow.__new__(CoordinateWindow)
    ui.root = Mock()
    ui.root.after.return_value = 'pending'
    ui.now = 0.
    ui.clock = lambda: ui.now
    ui.closed, ui.draw_job, ui.draw_signature = False, None, None
    ui.window_geometry, ui.map_size, ui.interacting_until = None, None, 0.
    ui.draw = Mock()
    ui.canvas = Mock()
    ui.canvas.winfo_width.return_value, ui.canvas.winfo_height.return_value = 800, 400
    ui.view = SimpleNamespace(position=(1., 20., 2.), paused=False, heading=None, trail=[])
    ui.scene_state = {'state': 'ready', 'objects': [], 'colliders': []}
    ui.saved_points, ui.playback_status = [], {'state': 'idle'}
    ui.map_view, ui.zoom, ui.scene_radius, ui.scene_categories = 'nearby', 1., 150., None
    for name in ('objects', 'colliders', 'water', 'roads', 'barriers'):
        setattr(ui, f'scene_{name}_var', SimpleNamespace(get=lambda: True))
    return ui


def test_stationary_frames_and_sample_metadata_changes_do_not_repaint():
    ui = window()
    for i in range(30):
        ui.view.status = {'sequence': i, 'tools': {'current': 900-i}}
        ui.draw_if_changed()
    ui.draw.assert_called_once()
    ui.view.position = (1.000001, 20.00001, 2.000001)
    ui.draw_if_changed()
    ui.draw.assert_called_once()


def test_moving_player_new_scene_and_navigation_updates_each_repaint():
    ui = window()
    ui.draw_if_changed()
    ui.view.position = (2., 20., 2.)
    ui.draw_if_changed()
    ui.scene_state = {'state': 'ready', 'objects': [{'position': [3, 20, 4]}], 'colliders': []}
    ui.draw_if_changed()
    ui.playback_status = {'navigation_path': [[4., 20., 5.]], 'waypoint': 1}
    ui.draw_if_changed()
    assert ui.draw.call_count == 4


def test_filter_zoom_route_edit_trail_clear_and_pause_are_not_lost():
    ui = window()
    ui.draw_if_changed()
    for mutate in (
        lambda: setattr(ui, 'scene_categories', {'blueberry'}),
        lambda: setattr(ui, 'zoom', 1.2),
        lambda: ui.saved_points.append({'id': 1, 'position': [2., 20., 3.]}),
        lambda: ui.view.trail.append({'x': 2., 'z': 3., 'segment': 1}),
        lambda: ui.view.trail.clear(),
        lambda: setattr(ui.view, 'paused', True),
        lambda: setattr(ui.view, 'heading', (0., 1.)),
    ):
        before = ui.draw.call_count
        mutate()
        ui.draw_if_changed()
        assert ui.draw.call_count == before+1


def test_drag_defers_drawing_and_renders_latest_data_once_after_settling():
    ui = window()
    def move(x):
        ui.window_configured(SimpleNamespace(widget=ui.root, x=x, y=50, width=1180, height=880))
    move(50)
    for x in range(51, 75):
        move(x)
    assert ui.root.after.call_count == 1
    ui.view.position = (8., 20., 9.)
    ui.now = .05
    ui.draw_if_changed()
    ui.draw.assert_not_called()
    assert ui.draw_job == 'pending'
    ui.now = .2
    ui.draw_if_changed()
    ui.draw.assert_called_once()
    assert ui.draw_signature == ui.map_signature() and ui.draw_job is None


def test_only_canvas_dimension_changes_request_a_map_redraw():
    ui = window()
    ui.request_draw = Mock()
    for x in range(20):
        ui.map_configured(SimpleNamespace(width=800, height=400, x=x, y=x))
    ui.request_draw.assert_called_once()
    ui.map_configured(SimpleNamespace(width=900, height=400))
    assert ui.request_draw.call_count == 2
    ui.window_configured(SimpleNamespace(widget=object(), x=1, y=1, width=2, height=2))
    assert ui.window_geometry is None


def test_tick_still_consumes_coordinate_scene_and_playback_updates_while_dragging():
    from utils.core.coordinate_monitor_view import MonitorView
    ui = window()
    ui.interacting_until = .15
    ui.view = MonitorView()
    ui.worker, ui.scene_worker, ui.playback = Mock(), Mock(), Mock()
    ui.worker.take_latest.return_value = {'state': 'connected', 'session': 'new', 'sequence': 2,
                                         'position': [8., 20., 9.], 'pid': 77}
    ui.scene_worker.set_pid.return_value = False
    scene = {'state': 'ready', 'objects': [], 'colliders': []}
    ui.scene_worker.take_latest.return_value = scene
    ui.playback.take_latest.return_value = {'state': 'finished', 'message': 'done'}
    ui.tick()
    assert ui.view.position == (8., 20., 9.) and ui.scene_state is scene
    assert ui.playback_status['state'] == 'finished'
    ui.draw.assert_not_called()
    ui.scene_worker.set_pid.assert_called_once_with(77)


def test_display_variables_and_widget_options_skip_identical_writes():
    runtime = tk.Tcl()
    value = DisplayValue(runtime, value='ready')
    writes = []
    value.trace_add('write', lambda *args: writes.append(value.get()))
    for _ in range(20):
        value.set('ready')
    assert not writes
    value.set('changed')
    assert writes == ['changed']
    widget = Mock()
    widget.cget.side_effect = lambda key: {'state': 'normal', 'fg': '#fff'}[key]
    configure_changed(widget, state='normal', fg='#fff')
    widget.configure.assert_not_called()
    configure_changed(widget, state='disabled', fg='#fff')
    widget.configure.assert_called_once_with(state='disabled')


def test_closed_window_never_reschedules_or_draws():
    ui = window()
    ui.closed = True
    ui.request_draw()
    ui.draw_if_changed()
    ui.root.after.assert_not_called()
    ui.draw.assert_not_called()
