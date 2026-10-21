"""Visit order is independent of devices and never edits source routes."""
from copy import deepcopy
import itertools
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import manual_points
from tests.unit.test_coordinate_navigation import box, scene
from tests.unit.test_native_coordinate_monitor import capture_window
from utils.core.route_optimization import optimize_route, optimize_order, route_cost
from utils.core.visit_progress import VisitProgress
from utils.ui.route_optimization import RouteOptimizationJob


def test_open_route_starts_at_current_position_and_preserves_all_point_metadata():
    data = manual_points([(10, 0, 0), (0, 0, 0), (9, 0, 0), (1, 0, 0)])
    data['points'][0].update(name='橘子树', collect_wait=2.5, custom='keep')
    original = deepcopy(data)
    result = optimize_route(data, (0, 0, 0), scene(), water_areas=())
    assert data == original
    assert [p['position'][0] for p in result['points']] == [0, 1, 9, 10]
    assert result['points'][-1]['collect_wait'] == 2.5 and result['points'][-1]['custom'] == 'keep'
    assert [p['id'] for p in result['points']] == [1, 2, 3, 4]
    detail = result['route_optimization']
    assert detail['estimated_cost_after'] == pytest.approx(10.)
    assert detail['estimated_cost_before'] == pytest.approx(37.)
    assert detail['source_point_ids'] == [2, 4, 3, 1]


def test_loop_objective_includes_each_internal_leg_and_only_actual_return_edges():
    matrix = [[0, 2, 4, 6], [2, 0, 3, 7], [4, 3, 0, 5], [6, 7, 5, 0]]
    assert route_cost([0, 1, 2], matrix, 1) == 10
    assert route_cost([0, 1, 2], matrix, 3) == 2+3*8+2*7


def test_larger_heuristic_keeps_every_point_and_improves_a_zigzag_route():
    xs = [30, 1, 29, 2, 28, 3, 27, 4, 26, 5]
    positions = [0, *xs]
    matrix = [[abs(a-b) for b in positions] for a in positions]
    for loops in (1, 4):
        order = optimize_order(matrix, loops)
        assert sorted(order) == list(range(10))
        assert route_cost(order, matrix, loops) < route_cost(list(range(10)), matrix, loops) / 2


def test_loop_mode_can_choose_a_different_order_than_a_single_open_trip():
    matrix = [[0, 0, 50, 50], [0, 0, 1, 3], [50, 1, 0, 1], [50, 100, 3, 0]]
    assert optimize_order(matrix, 1) == [0, 1, 2]
    assert optimize_order(matrix, 4) == [0, 2, 1]


@pytest.mark.parametrize('loops', [1, 2, 8])
def test_directed_costs_and_reversals_never_degrade_original_order(loops):
    matrix = [[0, 8, 1, 5, 2], [3, 0, 10, 2, 9], [7, 2, 0, 11, 4],
              [8, 9, 1, 0, 8], [3, 1, 5, 2, 0]]
    order = optimize_order(matrix, loops)
    assert sorted(order) == [0, 1, 2, 3]
    assert route_cost(order, matrix, loops) <= route_cost([0, 1, 2, 3], matrix, loops)
    # A small fixture also allows an independent exact optimum comparison.
    optimum = min(route_cost(p, matrix, loops) for p in itertools.permutations(range(4)))
    assert route_cost(order, matrix, loops) == optimum


def test_river_or_wall_changes_ordering_cost_instead_of_straight_line_sorting():
    data = manual_points([(0, 0, 8), (-8, 0, 0)])
    result = optimize_route(data, (0, 0, 0), scene(box(-15, 15, 3, 4)),
                            water_areas=(), clock=lambda: 0.)
    assert result['points'][0]['position'] == [-8, 0, 0]
    assert result['route_optimization']['estimated_cost_before'] > 35


def test_unknown_floor_is_penalized_and_never_reported_as_certified_geometry():
    result = optimize_route(manual_points([(1, 20, 1)]), (0, 0, 0), scene(), water_areas=())
    assert result['route_optimization']['unknown_edges'] == 2
    assert result['route_optimization']['checked_edges'] == 0


def test_expired_planning_budget_returns_all_points_with_explicit_uncertainty():
    clock = Mock(side_effect=itertools.count())
    data = manual_points([(0, 0, 1), (4, 0, 1), (2, 0, 3)])
    result = optimize_route(data, (0, 0, 0), scene(), water_areas=(), clock=clock)
    assert len(result['points']) == 3
    assert result['route_optimization']['budget_reached']


def test_cancellation_propagates_without_a_route_result():
    def stop():
        raise RuntimeError('closed')
    with pytest.raises(RuntimeError, match='closed'):
        optimize_route(manual_points([(0, 0, 1)]), (0, 0, 0), scene(), water_areas=(), check=stop)


@pytest.mark.parametrize('current_scene', [None, {}, {'state': 'loading'}])
def test_missing_geometry_is_rejected_instead_of_claiming_a_safe_shortcut(current_scene):
    with pytest.raises(ValueError, match='场景'):
        optimize_route(manual_points([(0, 0, 1)]), (0, 0, 0), current_scene)


def test_static_optimizer_does_not_freeze_dynamic_resource_positions():
    data = manual_points([(0, 0, 1)])
    data['points'][0]['special_resource'] = {'category': 'roaming_oak', 'static_id': 401}
    with pytest.raises(ValueError, match='动态资源'):
        optimize_route(data, (0, 0, 0), scene())


def test_background_job_returns_result_without_tk_or_file_writes():
    job = RouteOptimizationJob()
    job.start(manual_points([(0, 0, 0)]), (0, 0, 0), scene(), 1, {'name': 'source'})
    job.thread.join(3)
    assert job.active  # Pending results must be consumed before another job.
    assert job.take_latest()['context']['name'] == 'source'
    assert not job.active
    job.close()


def test_ui_saves_copy_and_keeps_source_bytes(tmp_path):
    window = capture_window(tmp_path)
    source = manual_points([(1, 0, 0), (0, 0, 0)])
    window.point_store.create(source)
    raw = window.point_store.path.read_bytes()
    window.navigation_scene = lambda pid: scene()
    result = optimize_route(source, (0, 0, 0), scene(), water_areas=())
    window.finish_route_optimization({'data': result, 'context': {
        'name': window.route_name, 'data': source, 'pid': None, 'scene': None}})
    assert (tmp_path/'点位.json').read_bytes() == raw
    assert '优化' in window.route_name
    assert window.point_store.load()['points'][0]['position'] == [0, 0, 0]


def test_ui_rejects_stale_optimization_after_source_edit(tmp_path, monkeypatch):
    window = capture_window(tmp_path)
    source = manual_points([(1, 0, 0)])
    window.point_store.create(source)
    window.navigation_scene = lambda pid: scene()
    window.point_store.set_collect_wait(1, .4)
    error = Mock()
    monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.finish_route_optimization({'data': source, 'context': {
        'name': window.route_name, 'data': source, 'pid': None, 'scene': None}})
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert '变化' in error.call_args.args[1]


def test_long_detour_progress_can_move_away_from_target_without_being_skipped():
    progress = VisitProgress((0, 0, 0), (0, 0, 10), 0.)
    for t in range(1, 45):
        assert progress.observe((0, 0, -t), t) is None
    assert progress.observe((0, 0, -44), 80) == '长时间未找到新的通行位置'


def test_pauses_and_missing_scene_do_not_use_the_visit_budget():
    progress = VisitProgress((0, 0, 0), (0, 0, 10), 0.)
    progress.pause(100)
    assert progress.observe((0, 0, 0), 101) is None
    assert progress.observe((0, 0, 0), 200, ready=False) is None
    assert progress.observe((0, 0, 0), 201) is None
    assert progress.elapsed == 2
