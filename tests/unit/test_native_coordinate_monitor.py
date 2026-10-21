"""Worker lifecycle tests use a fake reader and never create a desktop window."""
from datetime import datetime
import tkinter as tk
from unittest.mock import Mock
import pytest

from utils.core.coordinate_monitor_view import MonitorView
from utils.drivers.coordinate_point_store import PointStore
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.ui.native_coordinate_monitor import CoordinateWindow
from utils.ui.monitor_worker import MonitorWorker
from utils.ui.monitor_style import SCENE_CATEGORIES, SCENE_RANKS, MAP_PIXELS_PER_UNIT
from utils.ui.map_draw_cache import MapDrawCache
from utils.core.lake_areas import WATER_AREAS
from utils.core.map_reference_roads import REFERENCE_ROADS


@pytest.mark.parametrize('raw,expected', [
    ('ReadProcessMemory failed at 0x123456', '暂时无法读取角色坐标，请进入角色后重新连接。'),
    ('ReadProcessMemory failed at 0XDEADBEEF', '暂时无法读取角色坐标，请进入角色后重新连接。'),
    ('点位回放停止：ReadProcessMemory 0x123456', '角色坐标读取中断，回放已停止；请进入角色后重新开始。'),
    ('UnityPlayer.dll 版本指纹不匹配', '当前游戏版本暂不支持，请更新程序后重试。'),
    ('OpenProcess failed: Access is denied', '无法读取游戏，请以管理员身份运行程序。'),
    ('点位 2/6 · 连续失败 3/5，已暂停', '点位 2/6 · 连续失败 3/5，已暂停'),
    ('已暂停 · 最后读数', '已暂停 · 最后读数'),
])
def test_user_feedback_preserves_actions_without_showing_memory_details(raw, expected):
    from utils.ui.monitor_feedback import user_feedback
    assert user_feedback(raw) == expected


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
    window.navigation_var = Mock()
    window.navigation_var.get.return_value = True
    window.repair_auto_var = Mock()
    window.repair_auto_var.get.return_value = True
    window.food_auto_var = Mock()
    window.food_auto_var.get.return_value = True
    window.collect_wait_var = Mock()
    window.collect_wait_var.get.return_value = '3.0'
    window.loop_count_var = Mock()
    window.loop_count_var.get.return_value = '1'
    window.points_error = ''
    window.hint = Mock()
    window.render = Mock()
    window.root = Mock()
    window.wait_editor = None
    window.scene_categories = None
    window.map_view = 'nearby'
    return window


def test_save_button_appends_to_disk_then_updates_count(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    assert len(window.saved_points) == 1
    assert window.point_store.load()['points'] == window.saved_points
    assert window.saved_points[0]['position'] == [-45.5, 23.25, 128.2]
    window.render.assert_called_once()
    assert '点位 1' in window.hint.configure.call_args.kwargs['text']


def test_lake_reference_layer_draws_only_when_enabled():
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.canvas = Mock()
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS)
    window.draw_job, window.closed = None, False
    window.canvas.winfo_width.return_value = 600
    window.canvas.winfo_height.return_value = 400
    window.view = MonitorView()
    window.view.accept({'state': 'connected', 'session': 'a', 'sequence': 1,
                        'position': [-115.467, 24.627, 64.448]}, 10.)
    window.scene_state = {'objects': [], 'colliders': []}
    window.scene_objects_var = Mock()
    window.scene_colliders_var = Mock()
    window.scene_water_var = Mock()
    window.scene_roads_var = Mock()
    window.scene_roads_var.get.return_value = True
    window.scene_objects_var.get.return_value = False
    window.scene_colliders_var.get.return_value = False
    window.scene_water_var.get.return_value = True
    window.scene_categories = None
    window.playback_status = {'state': 'idle'}
    window.saved_points = []
    window.zoom = .2
    window.map_view = 'nearby'
    window.fields = {'scale': Mock()}
    window.draw()
    assert any(call.kwargs.get('tags') == 'reference-water'
               for call in window.canvas.create_polygon.call_args_list)
    window.canvas.reset_mock()
    window.scene_water_var.get.return_value = False
    window.draw()
    assert not window.canvas.create_polygon.called


def test_berry_species_and_ordinary_bushes_have_distinct_map_symbols(tmp_path):
    window = capture_window(tmp_path)
    window.canvas = Mock()
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS)
    window.draw_job, window.closed = None, False
    window.canvas.winfo_width.return_value = 900
    window.canvas.winfo_height.return_value = 500
    window.scene_state = {'objects': [
        {'position': [-44., 23., 128.], 'kind': 'entity', 'category': 'shrub'},
        {'position': [-43., 23., 128.], 'kind': 'entity', 'category': 'other'},
        {'position': [-42., 23., 128.], 'kind': 'object'},
        {'position': [-41., 23., 128.], 'kind': 'entity', 'category': 'unknown'},
        {'position': [-40., 23., 128.], 'kind': 'entity', 'category': 'blueberry'},
        {'position': [-39., 23., 128.], 'kind': 'entity', 'category': 'raspberry'},
        {'position': [-38., 23., 128.], 'kind': 'vegetation', 'category': 'bush'},
    ], 'colliders': []}
    window.scene_objects_var = Mock()
    window.scene_objects_var.get.return_value = True
    window.scene_colliders_var = Mock()
    window.scene_colliders_var.get.return_value = False
    window.scene_water_var = Mock()
    window.scene_roads_var = Mock()
    window.scene_roads_var.get.return_value = True
    window.scene_water_var.get.return_value = False
    window.zoom = 1.
    window.fields = {'scale': Mock()}

    window.draw()
    plants = {call.kwargs.get('tags'): call for call in window.canvas.create_polygon.call_args_list
              if call.kwargs.get('tags') in ('scene-shrub', 'scene-blueberry', 'scene-raspberry', 'scene-bush')}
    other_objects = [call for call in window.canvas.create_oval.call_args_list
                     if call.kwargs.get('tags') == 'scene-object']
    assert len(plants) == 4
    assert len(other_objects) == 3
    assert len({call.kwargs['fill'] for call in plants.values()}) == 4
    assert all(call.kwargs['outline'] for call in plants.values())
    assert other_objects[0].kwargs['fill'] not in {call.kwargs['fill'] for call in plants.values()}
    assert any(call.kwargs.get('tags') == 'scene-blueberry-fruit'
               for call in window.canvas.create_oval.call_args_list)
    assert any(call.kwargs.get('tags') == 'scene-raspberry-fruit'
               for call in window.canvas.create_polygon.call_args_list)

    window.canvas.reset_mock()
    window.scene_objects_var.get.return_value = False
    window.draw()
    assert not any(call.kwargs.get('tags') in plants
                   for call in window.canvas.create_polygon.call_args_list)
    assert not any(call.kwargs.get('tags') == 'scene-object'
                   for call in window.canvas.create_oval.call_args_list)


@pytest.mark.parametrize('width,height,zoom', [(600, 400, 1.), (1000, 700, .2), (800, 500, 5.)])
def test_map_follows_current_character_instead_of_distant_saved_points(width, height, zoom):
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.canvas = Mock()
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS)
    window.draw_job, window.closed = None, False
    window.canvas.winfo_width.return_value = width
    window.canvas.winfo_height.return_value = height
    window.view = MonitorView()
    window.scene_state = {'objects': [], 'colliders': []}
    window.scene_objects_var = Mock()
    window.scene_colliders_var = Mock()
    window.scene_water_var = Mock()
    window.scene_roads_var = Mock()
    window.scene_roads_var.get.return_value = True
    for layer in (window.scene_objects_var, window.scene_colliders_var, window.scene_water_var):
        layer.get.return_value = False
    window.saved_points = [{'id': 1, 'position': [75., 20., 93.]},
                           {'id': 2, 'position': [60., 20., 100.]}]
    window.zoom = zoom
    window.map_view = 'nearby'
    window.fields = {'scale': Mock()}
    window.scene_categories = None
    window.playback_status = {'state': 'idle'}

    for sequence, position in enumerate(([-118., 20., -42.], [-115., 20., -40.]), 1):
        window.view.accept({'state': 'connected', 'session': 'a', 'sequence': sequence,
                            'position': position}, 10. + sequence * .5)
        window.canvas.reset_mock()
        window.draw()
        assert window.canvas.create_oval.call_args.args == pytest.approx(
            (width / 2 - 2.5, height / 2 - 2.5, width / 2 + 2.5, height / 2 + 2.5))


def test_fauna_symbols_are_distinct_and_hide_with_the_scene_object_layer(tmp_path):
    window = capture_window(tmp_path)
    window.canvas = Mock()
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS)
    window.draw_job, window.closed = None, False
    window.canvas.winfo_width.return_value = 900
    window.canvas.winfo_height.return_value = 500
    window.scene_state = {'objects': [
        {'position': [-44. + index, 23., 128.], 'kind': 'entity', 'category': category}
        for index, category in enumerate(('butterfly', 'insect', 'bird', 'animal'))], 'colliders': []}
    window.scene_objects_var = Mock()
    window.scene_objects_var.get.return_value = True
    window.scene_colliders_var = Mock()
    window.scene_colliders_var.get.return_value = False
    window.scene_water_var = Mock()
    window.scene_roads_var = Mock()
    window.scene_roads_var.get.return_value = True
    window.scene_water_var.get.return_value = False
    window.zoom = 1.
    window.fields = {'scale': Mock()}
    window.draw()

    tags = {'scene-butterfly', 'scene-insect', 'scene-bird', 'scene-animal'}
    colors = {call.kwargs['tags']: call.kwargs.get('outline') or call.kwargs['fill']
              for call in window.canvas.create_polygon.call_args_list + window.canvas.create_oval.call_args_list
              + window.canvas.create_line.call_args_list if call.kwargs.get('tags') in tags}
    assert colors.keys() == tags
    assert len(set(colors.values())) == 4
    assert not any(call.kwargs.get('tags') == 'scene-object'
                   for call in window.canvas.create_oval.call_args_list)

    window.canvas.reset_mock()
    window.scene_objects_var.get.return_value = False
    window.draw()
    assert not any(call.kwargs.get('tags') in tags for call in
                   window.canvas.create_polygon.call_args_list + window.canvas.create_oval.call_args_list
                   + window.canvas.create_line.call_args_list)


def category_window(tmp_path):
    window = capture_window(tmp_path)
    window.canvas = Mock()
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS)
    window.draw_job, window.closed = None, False
    window.canvas.winfo_width.return_value = 900
    window.canvas.winfo_height.return_value = 500
    window.scene_state = {'objects': [
        {'position': [-44. + index, 23., 128.], 'kind': 'entity', 'category': category}
        for index, category in enumerate(SCENE_CATEGORIES)], 'colliders': []}
    tcl = tk.Tcl()
    window.scene_objects_var = tk.BooleanVar(tcl, True)
    window.scene_colliders_var = tk.BooleanVar(tcl, False)
    window.scene_water_var = tk.BooleanVar(tcl, False)
    window.scene_roads_var = tk.BooleanVar(tcl, True)
    window.scene_all_button, window.scene_none_button, window.scene_filter_hint = Mock(), Mock(), Mock()
    window.scene_filter_buttons = {category: Mock() for category in SCENE_CATEGORIES}
    window.saved_points = [{'id': 1, 'position': [-43., 23., 130.]}]
    window.zoom = 1.
    window.fields = {'scale': Mock()}
    return window


def drawn_object_tags(window):
    return {tag for tag in window.map_cache.visible_tags if tag.startswith('scene-')}


def test_map_hover_picks_only_visible_categories_without_changing_routes(tmp_path):
    window = category_window(tmp_path)
    window.map_hover = Mock()
    original = [point.copy() for point in window.saved_points]
    window.toggle_scene_category('blueberry')
    targets = window.map_hover.update.call_args.args[1]
    assert [target.title for target in targets] == ['蓝莓', '路线点 1']
    assert '平面距离' in targets[0].detail
    window.scene_state['objects'][0]['map_cached'] = True
    window.draw()
    assert '缓存参考' in window.map_hover.update.call_args.args[1][0].detail
    assert window.saved_points == original


def test_hover_motion_is_throttled_without_repainting_the_map(tmp_path):
    window = category_window(tmp_path)
    window.map_hover, window.map_hover_job, window.map_pointer = Mock(), None, None
    window.root.after.return_value = 'hover-pending'
    for x in range(30):
        window.map_motion(Mock(x=x, y=100))
    window.root.after.assert_called_once()
    window.show_map_hover()
    window.map_hover.show.assert_called_once_with(29, 100)
    window.render.assert_not_called()
    window.map_motion(Mock(x=20, y=30))
    window.map_leave()
    assert window.map_hover_job is None and window.map_pointer is None
    window.root.after_cancel.assert_called_once_with('hover-pending')
    window.map_hover.hide.assert_called_once()


def test_offscreen_active_target_has_indicator_and_remains_in_the_route(tmp_path):
    window = category_window(tmp_path)
    position = [5000., 23., 5000.]
    window.saved_points = [{'id': 1, 'position': position}]
    window.playback_status = {'state': 'moving', 'waypoint': 1}
    window.canvas.bbox.return_value = (850, -20, 990, 0)
    window.draw()
    assert any(call.kwargs.get('tags') == 'target-indicator'
               for call in window.canvas.create_text.call_args_list)
    assert not any(call.kwargs.get('tags') == 'saved-point'
                   for call in window.canvas.create_rectangle.call_args_list)
    assert window.saved_points == [{'id': 1, 'position': position}]
    assert window.map_cache.viewport.x == window.view.position[0]


def test_route_overview_uses_the_height_of_a_compact_map(tmp_path):
    window = category_window(tmp_path)
    window.canvas.winfo_width.return_value = 600
    window.canvas.winfo_height.return_value = 200
    x, y, z = window.view.position
    window.saved_points = [{'id': 1, 'position': [x, y, z-200]}]
    window.show_route_overview()
    window.draw()
    _, screen_y = window.map_cache.viewport.screen(x, z-200)
    assert 160 <= screen_y <= 170


def test_tree_filter_draws_canopy_and_trunk_and_reuses_cached_items(tmp_path):
    window = category_window(tmp_path)
    window.toggle_scene_category('tree')
    assert drawn_object_tags(window) == {'scene-tree'}
    assert window.scene_filter_buttons['tree'].configure.call_args.kwargs['text'] == '✓ 树木'
    assert any(call.kwargs.get('tags') == 'scene-tree' for call in window.canvas.create_polygon.call_args_list)
    assert any(call.kwargs.get('tags') == 'scene-tree' and call.kwargs['fill'] == '#c99b6c'
               for call in window.canvas.create_line.call_args_list)
    entry = next(entry for key, entry in window.map_cache.fixed.items() if key[:2] == ('object', 'tree'))
    commands = entry.commands[:]
    window.canvas.reset_mock()
    window.set_scene_categories(False)
    assert not drawn_object_tags(window)
    window.toggle_scene_category('tree')
    assert entry.commands == commands and not window.canvas.create_polygon.called
    assert drawn_object_tags(window) == {'scene-tree'}


def test_category_click_isolates_then_multiselects_and_toggles_off(tmp_path):
    window = category_window(tmp_path)
    window.toggle_scene_category('blueberry')
    assert drawn_object_tags(window) == {'scene-blueberry', 'scene-blueberry-fruit'}
    assert window.scene_filter_buttons['blueberry'].configure.call_args.kwargs['text'] == '✓ 蓝莓'
    assert window.scene_filter_hint.configure.call_args.kwargs['text'].startswith('已选 1 类')

    window.canvas.reset_mock()
    window.toggle_scene_category('raspberry')
    assert drawn_object_tags(window) == {'scene-blueberry', 'scene-blueberry-fruit',
                                               'scene-raspberry', 'scene-raspberry-fruit'}
    window.canvas.reset_mock()
    window.toggle_scene_category('blueberry')
    assert drawn_object_tags(window) == {'scene-raspberry', 'scene-raspberry-fruit'}
    window.canvas.reset_mock()
    window.toggle_scene_category('raspberry')
    assert drawn_object_tags(window) == set()
    # Category filtering never removes the route point or the centered player.
    assert sum(call.kwargs.get('tags') == 'saved-point'
               for call in window.canvas.create_rectangle.call_args_list) == 1
    assert window.canvas.create_oval.call_args.args == pytest.approx((447.5, 247.5, 452.5, 252.5))
    assert window.saved_points == [{'id': 1, 'position': [-43., 23., 130.]}]

    window.canvas.reset_mock()
    window.set_scene_categories(True)
    assert {'scene-blueberry', 'scene-raspberry', 'scene-bush', 'scene-tree', 'scene-shrub', 'scene-butterfly',
            'scene-insect', 'scene-bird', 'scene-animal', 'scene-object'} <= drawn_object_tags(window)
    assert window.scene_filter_hint.configure.call_args.kwargs['text'].startswith('全部分类')


def test_other_filter_includes_unknown_and_missing_types_but_not_unconfirmed_fruit(tmp_path):
    window = category_window(tmp_path)
    window.scene_state['objects'].extend([
        {'position': [-44., 23., 129.], 'kind': 'object'},
        {'position': [-43., 23., 129.], 'kind': 'entity', 'category': 'unrecognized'},
    ])
    window.toggle_scene_category('other')
    assert drawn_object_tags(window) == {'scene-object'}
    assert sum(call.kwargs.get('tags') == 'scene-object'
               for call in window.canvas.create_oval.call_args_list) == 3
    window.canvas.reset_mock()
    window.set_scene_categories(False)
    window.toggle_scene_category('shrub')
    assert drawn_object_tags(window) == {'scene-shrub'}


def test_category_selection_survives_scene_refresh_and_master_switch(tmp_path):
    window = category_window(tmp_path)
    window.toggle_scene_category('butterfly')
    original_scene = window.scene_state
    window.scene_state = {'objects': [], 'colliders': []}
    window.canvas.reset_mock()
    window.draw()
    assert drawn_object_tags(window) == set()
    assert window.scene_categories == {'butterfly'}
    assert window.scene_filter_buttons.keys() == SCENE_CATEGORIES.keys()
    window.scene_state = original_scene
    window.canvas.reset_mock()
    window.draw()
    assert drawn_object_tags(window) == {'scene-butterfly'}

    window.canvas.reset_mock()
    window.scene_objects_var.set(False)
    window.scene_layer_changed()
    assert drawn_object_tags(window) == set()
    window.canvas.reset_mock()
    window.scene_objects_var.set(True)
    window.scene_layer_changed()
    assert drawn_object_tags(window) == {'scene-butterfly'}
    window.scene_objects_var.set(False)
    window.canvas.reset_mock()
    window.toggle_scene_category('insect')
    assert window.scene_objects_var.get() is True
    assert drawn_object_tags(window) == {'scene-insect'}


def test_clear_category_filter_preserves_water_and_collision_layers(tmp_path):
    window = category_window(tmp_path)
    window.view.accept({'state': 'connected', 'session': 'a', 'sequence': 2,
                        'position': [-115.467, 24.627, 64.448]}, 10.1)
    window.zoom = .2
    window.scene_colliders_var.set(True)
    window.scene_water_var.set(True)
    window.scene_state['colliders'] = [{'center': [-114., 24., 64.],
                                      'polygon': [[-115., 63.], [-113., 63.], [-113., 65.], [-115., 65.]]}]
    window.set_scene_categories(False)
    assert drawn_object_tags(window) == {'scene-collider'}
    assert any(call.kwargs.get('tags') == 'reference-water'
               for call in window.canvas.create_polygon.call_args_list)


def test_hidden_categories_do_not_expand_map_scale(tmp_path):
    window = category_window(tmp_path)
    near = {'position': [-44., 23., 128.], 'kind': 'entity', 'category': 'blueberry'}
    far = {'position': [30., 23., 128.], 'kind': 'object', 'category': 'other'}
    window.scene_state['objects'] = [near, far]
    window.toggle_scene_category('blueberry')
    filtered_scale = window.fields['scale'].set.call_args.args[0]
    window.scene_state['objects'] = [near]
    window.draw()
    assert window.fields['scale'].set.call_args.args[0] == filtered_scale


def test_nearby_view_keeps_local_detail_when_distant_points_and_trails_are_added(tmp_path):
    window = category_window(tmp_path)
    window.draw()
    entries = {key: entry.commands[:] for key, entry in window.map_cache.fixed.items()}
    initial_scale = window.fields['scale'].set.call_args.args[0]
    window.saved_points.append({'id': 2, 'position': [5000., 23., -5000.]})
    window.view.trail.append({'x': -8000., 'z': 2000., 'segment': 0})
    window.canvas.reset_mock()
    window.draw()
    assert {key: entry.commands for key, entry in window.map_cache.fixed.items()} == entries
    assert not any(call.kwargs.get('tags', '').startswith(('scene-', 'reference-'))
                   for call in window.canvas.create_polygon.call_args_list)
    assert window.fields['scale'].set.call_args.args[0] == initial_scale


def test_route_overview_fits_saved_points_and_reset_restores_nearby_detail(tmp_path):
    window = category_window(tmp_path)
    window.saved_points = [{'id': 1, 'position': [1000., 23., 800.]},
                           {'id': 2, 'position': [-800., 23., -1000.]}]
    original = [p.copy() for p in window.saved_points]
    window.show_route_overview()
    window.draw()
    for call in window.canvas.create_rectangle.call_args_list:
        if call.kwargs.get('tags') != 'saved-point':
            continue
        x1, y1, x2, y2 = call.args
        assert 35 <= x1 < x2 <= 865
        assert 35 <= y1 < y2 <= 465
    assert window.canvas.create_oval.call_args.args == pytest.approx((447.5, 247.5, 452.5, 252.5))
    window.reset_map_view()
    assert window.map_view == 'nearby' and window.zoom == 1.
    assert window.saved_points == original


def test_range_selection_expands_visible_landmarks_keeps_player_centered_and_preserves_routes(tmp_path):
    window = category_window(tmp_path)
    window.canvas.winfo_width.return_value = 900
    window.canvas.winfo_height.return_value = 500
    position = window.view.position
    landmark = {'position': [position[0], position[1], position[2]+140], 'category': 'tree'}
    window.scene_state['objects'] = [landmark]
    original = [p.copy() for p in window.saved_points]
    assert window.set_scene_radius('80 单位')
    window.draw()
    assert not any('scene-tree' in entry.tags and entry.visible for entry in window.map_cache.fixed.values())
    assert window.set_scene_radius('200 单位')
    window.draw()
    assert window.scene_radius == 200 and window.map_view == 'range'
    assert any('scene-tree' in entry.tags and entry.visible for entry in window.map_cache.fixed.values())
    assert window.canvas.create_oval.call_args.args == pytest.approx((447.5, 247.5, 452.5, 252.5))
    assert window.saved_points == original
    assert not window.set_scene_radius('9999 单位') and window.scene_radius == 200


def test_wheel_zoom_enlarges_local_markers_and_keeps_player_centered(tmp_path):
    window = category_window(tmp_path)
    window.draw()
    before = next(call.args for call in window.canvas.create_rectangle.call_args_list
                  if call.kwargs.get('tags') == 'saved-point')
    assert window.zoom_map(Mock(delta=120)) == 'break'
    window.draw()
    after = next(call.args for call in reversed(window.canvas.create_rectangle.call_args_list)
                 if call.kwargs.get('tags') == 'saved-point')
    assert ((after[0] + after[2]) / 2 - 450) > ((before[0] + before[2]) / 2 - 450)
    assert window.canvas.create_oval.call_args.args == pytest.approx((447.5, 247.5, 452.5, 252.5))


def test_rapid_zoom_requests_coalesce_and_render_the_latest_value(tmp_path):
    window = category_window(tmp_path)
    window.root.after.return_value = 'draw-pending'
    window.set_zoom(1.1)
    window.set_zoom(1.2)
    window.set_zoom(1.3)
    assert window.root.after.call_count == 1
    window.draw()
    window.root.after_cancel.assert_called_once_with('draw-pending')
    assert window.map_cache.viewport.scale == pytest.approx(MAP_PIXELS_PER_UNIT * 1.3)
    assert window.draw_job is None


def test_save_button_reports_write_error_without_claiming_success(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.point_store.append = Mock(side_effect=PermissionError('不可写'))
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.save_point()
    assert window.saved_points == []
    assert not window.point_store.path.exists()
    error.assert_called_once()
    assert '添加失败' in window.hint.configure.call_args.kwargs['text']


def test_save_button_checks_freshness_at_click_time(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.clock = lambda: 12.
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
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
        'stable_navigation': True, 'skip_unreachable': True,
                                                     'recovery_attempts': 3, 'jump_on_recovery': False,
                                                     'collect_wait_seconds': 3., 'loop_count': 1, 'auto_navigate': True,
                                                     'auto_repair': True, 'auto_eat': True,
                                                     'route_path': window.point_store.path, 'remember_paths': False}


@pytest.mark.parametrize('requested', [False, True])
def test_path_memory_is_temporarily_disabled_even_if_option_is_set(tmp_path, requested):
    window = capture_window(tmp_path)
    window.save_point()
    window.view.status['pid'] = 77
    window.path_memory_var = Mock()
    window.path_memory_var.get.return_value = requested
    window.start_playback()
    assert window.playback.start.call_args.kwargs['remember_paths'] is False


@pytest.mark.parametrize('confirm', [False, True])
def test_clear_path_memory_preserves_saved_collection_points(tmp_path, monkeypatch, confirm):
    from utils.drivers.route_path_memory import RoutePathMemory
    window = capture_window(tmp_path)
    window.save_point()
    before = window.point_store.path.read_bytes()
    memory = RoutePathMemory(window.point_store.path, window.point_store.load())
    target = window.saved_points[0]['position']
    assert memory.remember(0, [[target[0] + 2, target[1], target[2]], target])
    window.path_memory_label = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.askyesno', Mock(return_value=confirm))
    window.clear_path_memory()
    assert memory.path.exists() is not confirm
    assert window.point_store.path.read_bytes() == before


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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
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


def test_ui_can_disable_navigation_and_forwards_the_choice(tmp_path):
    window = capture_window(tmp_path)
    window.save_point()
    window.view.status['pid'] = 77
    window.navigation_var.get.return_value = False
    window.start_playback()
    assert window.playback.start.call_args.kwargs['auto_navigate'] is False
    assert '直接前往' in window.playback_status['message']


def test_planned_path_remains_visible_when_object_categories_are_hidden(tmp_path):
    window = category_window(tmp_path)
    window.playback_status['navigation_path'] = [[-44., 23., 128.], [-43., 23., 132.]]
    window.set_scene_categories(False)
    paths = [call for call in window.canvas.create_line.call_args_list
             if call.kwargs.get('tags') == 'navigation-path']
    assert len(paths) == 1 and len(paths[0].args) == 6
    assert not drawn_object_tags(window)


def test_reference_road_visibility_is_independent_of_navigation_and_saved_points(tmp_path):
    window = category_window(tmp_path)
    window.map_cache = MapDrawCache(window.canvas, WATER_AREAS, SCENE_RANKS, roads=REFERENCE_ROADS)
    window.view.accept({'state': 'connected', 'session': 'a', 'sequence': 2,
                        'position': [97., 23., 18.]}, 10.1)
    window.playback_status['navigation_path'] = [[98., 23., 18.], [99., 23., 20.]]
    original_points = window.saved_points.copy()
    window.draw()
    assert 'reference-road' in window.map_cache.visible_tags
    entries = {key: entry.commands[:] for key, entry in window.map_cache.fixed.items() if key[0] == 'road'}
    window.scene_roads_var.set(False)
    window.canvas.reset_mock()
    window.scene_layer_changed()
    assert 'reference-road' not in window.map_cache.visible_tags
    assert any(call.kwargs.get('tags') == 'navigation-path' for call in window.canvas.create_line.call_args_list)
    assert window.saved_points == original_points
    window.scene_roads_var.set(True)
    window.scene_layer_changed()
    assert {key: entry.commands for key, entry in window.map_cache.fixed.items() if key[0] == 'road'} == entries


def test_semantic_ce_export_uses_current_live_address_and_never_copies_the_old_table(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(locator='mono-local-player', pid=77, address=0x123450)
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    window.export_ct()
    assert '123450' in file.read_text(encoding='utf-8')
    assert '77' in file.read_text(encoding='utf-8')


def test_semantic_ce_export_refuses_expired_display_data(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(locator='mono-local-player', pid=77, address=0x123450)
    window.clock = lambda: 100.
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.export_ct()
    assert not file.exists()
    error.assert_called_once()


def test_disconnected_ce_export_never_exports_an_old_bundled_pointer_table(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.resources = tmp_path
    (tmp_path / 'coordinates.CT').write_text('stale pointer table', encoding='utf-8')
    window.view.accept({'state': 'disconnected', 'position': None}, 10.1)
    file = tmp_path / 'export.CT'
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename',
                        lambda **kwargs: str(file))
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.export_ct()
    assert not file.exists()
    error.assert_called_once()


def test_legacy_live_reader_exports_the_current_observation_without_a_static_template(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.view.status.update(pid=77, address=0x234560)
    file = tmp_path / 'current.CT'
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename',
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.point_store.path.name == '点位.json'
    error.assert_called_once()


def test_failed_route_selection_keeps_current_points_and_wait_editor_store(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    before = window.saved_points.copy()
    (tmp_path / '蓝莓.json').write_text('{bad', encoding='utf-8')
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', Mock())
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.saved_points == before
    assert window.routes.selected_name() == '点位'


def test_new_and_save_as_route_buttons_keep_original_points(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    ask = Mock(side_effect=['苹果', '蓝莓'])
    monkeypatch.setattr('utils.ui.monitor_controller.simpledialog.askstring', ask)
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.askyesno', confirm)
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.switch_route('蓝莓')
    assert window.route_name == '点位' and window.point_store.path.name == '点位.json'
    error.assert_called_once()


def test_clear_saved_points_confirms_current_route_and_refreshes_preview(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    window.save_point()
    original = window.point_store.path.read_bytes()
    window.routes.create('蓝莓', window.point_store.load())
    confirm = Mock(side_effect=[False, True])
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.askyesno', confirm)
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.askyesno', confirm)
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
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
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.askyesno', Mock(return_value=True))
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.routes.clear_points = Mock(side_effect=PermissionError('busy'))
    window.clear_saved_points()
    assert window.point_store.path.read_bytes() == before and len(window.saved_points) == 1
    error.assert_called_once()


def test_export_walk_trail_writes_segmented_xz_json(tmp_path, monkeypatch):
    import json

    window = capture_window(tmp_path)
    window.view.trail = [
        {'x': -10.25, 'z': 5.5, 'segment': 0},
        {'x': -9.75, 'z': 6.0, 'segment': 0},
        {'x': 40., 'z': 80., 'segment': 1},
        {'x': 41., 'z': 81., 'segment': 1},
    ]
    window.trail_export_dir = tmp_path / '道路数据' / '实走轨迹'
    target = window.trail_export_dir / '森林西侧支路.json'
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename',
                        Mock(return_value=str(target)))

    window.export_walk_trail()

    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload['name'] == '森林西侧支路'
    assert payload['coordinate_space'] == 'game-world-XZ'
    assert [segment['points_xz'] for segment in payload['segments']] == [
        [[-10.25, 5.5], [-9.75, 6.0]],
        [[40.0, 80.0], [41.0, 81.0]],
    ]
    assert window.hint.configure.call_args.kwargs['text'] == '实走路段已导出：2 段 · 4 个点'


def test_export_walk_trail_without_data_does_not_open_save_dialog(monkeypatch):
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.view = MonitorView()
    window.root = Mock()
    info = Mock()
    picker = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showinfo', info)
    monkeypatch.setattr('utils.ui.monitor_controller.filedialog.asksaveasfilename', picker)

    window.export_walk_trail()

    info.assert_called_once()
    picker.assert_not_called()
