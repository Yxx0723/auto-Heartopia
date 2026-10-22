"""A connected role with saved points must update controls and live values."""
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock

from utils.ui.native_coordinate_monitor import CoordinateWindow


def test_connected_saved_route_renders_values_and_enables_playback():
    ui = CoordinateWindow.__new__(CoordinateWindow)
    ui.clock, ui.interacting_until = lambda: 1., 0.
    ui.canvas = Mock()
    ui.view = SimpleNamespace(position=(1., 2., 3.), paused=False, status={},
                              trail=[], speed=0., notice='')
    ui.playback = SimpleNamespace(active=False)
    ui.saved_points = [{'id':1, 'position':[1., 2., 3.], 'name':'测试点'}]
    ui.points_error, ui.route_buttons = '', {}
    ui.playback_status = {'state':'idle', 'message':'就绪'}
    ui.scene_state = {'state':'ready', 'objects':[], 'colliders':[]}
    ui.fields = defaultdict(Mock)
    ui.render_notice, ui.draw_if_changed = Mock(), Mock()
    for name in ('route_menu', 'start_menu', 'recovery_menu', 'recovery_jump_check',
                 'navigation_check', 'repair_auto_check', 'collect_wait_spin', 'loop_count_spin',
                 'point_wait_button', 'clear_points_button', 'save_point_button', 'play_button',
                 'stop_button', 'playback_label', 'expanded_status', 'saved_summary',
                 'tool_value', 'tool_hint', 'tool_progress', 'scene_status'):
        setattr(ui, name, Mock())
    ui.render()
    ui.play_button.configure.assert_called_with(state='normal')
    ui.fields['X'].set.assert_called_with('1.0000')
    ui.fields['saved_count'].set.assert_called_with('已保存 1 个点')
    ui.draw_if_changed.assert_called_once()
