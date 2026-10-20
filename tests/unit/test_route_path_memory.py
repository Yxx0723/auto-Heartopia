"""Successful real movement is persisted, rechecked, and isolated per route."""
from copy import deepcopy
import json
import math

import pytest

from tests.unit.test_coordinate_navigation import NavigationGame, box, scene
from tests.unit.test_coordinate_player import manual_points
from utils.core.coordinate_navigation import LocalNavigator
from utils.core.coordinate_route import distance
from utils.drivers.route_path_memory import RoutePathMemory
from utils.flows.coordinate_player import drive_points


def trace():
    return [[0., 0., float(z)] for z in range(9)]


def test_save_restart_route_isolation_and_coordinate_change_invalidates_cache(tmp_path):
    data = manual_points([(0, 0, 8)])
    route = tmp_path / 'oranges.json'
    memory = RoutePathMemory(route, data)
    assert memory.remember(0, trace())
    restarted = RoutePathMemory(route, data)
    assert restarted.count == 1 and restarted.lookup(0, (0, 0, 0))
    assert not RoutePathMemory(tmp_path / 'apples.json', data).lookup(0, (0, 0, 0))
    edited = deepcopy(data)
    edited['points'][0]['position'][0] = 3.
    assert RoutePathMemory(route, edited).count == 0
    edited = deepcopy(data)
    edited['points'][0]['collect_wait'] = 12.
    assert RoutePathMemory(route, edited).count == 1
    assert not restarted.lookup(0, (0, 4, 0))  # Same XZ on another floor.
    assert not restarted.lookup(0, (30, 0, 0))


@pytest.mark.parametrize('bad', [[], [[0, 0, 0]], [[0, 0, 0], [0, 0, 8]],
                                    [[0, 0, 0], [math.nan, 0, 8]], [[0, 0, 0], [0, 0, 2]]])
def test_invalid_unfinished_or_teleported_walks_are_rejected(tmp_path, bad):
    memory = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    assert not memory.remember(0, bad)
    assert not memory.path.exists()


def test_damage_is_ignored_variants_are_bounded_and_clear_preserves_route(tmp_path):
    data = manual_points([(0, 0, 8)])
    route = tmp_path / 'route.json'
    route.write_text(json.dumps(data), encoding='utf-8')
    original = route.read_bytes()
    memory = RoutePathMemory(route, data)
    memory.path.parent.mkdir()
    memory.path.write_text('{broken', encoding='utf-8')
    assert RoutePathMemory(route, data).count == 0
    for start in (0, -4, -8, -12):
        path = [[float(x), 0., 0.] for x in range(start, 1)] + trace()[1:]
        assert memory.remember(0, path)
    assert len(RoutePathMemory(route, data).legs['0']) == 3
    memory.clear()
    assert not memory.path.exists() and memory.count == 0
    assert route.read_bytes() == original


def test_backtracking_loops_are_removed_from_the_saved_walk(tmp_path):
    memory = RoutePathMemory(tmp_path/'route.json', manual_points([(0, 0, 8)]))
    path = [[0., 0., float(z)] for z in (0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 4, 5, 6, 7, 8)]
    assert memory.remember(0, path)
    saved = RoutePathMemory(tmp_path/'route.json', manual_points([(0, 0, 8)])).legs['0'][0]
    assert len(saved) < len(path) and saved[-1] == [0., 0., 8.]


def run_walk(game, data, memory, snapshot, report=lambda *a, **kw: None):
    nav = LocalNavigator(lambda: snapshot, clock=game.monotonic, water_areas=(), path_memory=memory)
    modes = []
    def status(state, message, **extra):
        modes.append(nav.memory_state)
        report(state, message, **extra)
    result = drive_points(data, game.size(), lambda: game, game, game, game, status,
                          clock=game, navigator=nav, collect_wait_seconds=0.)
    return result, nav, modes


@pytest.mark.parametrize('angle', [0., math.pi / 3, -math.pi / 2])
def test_completed_detour_is_reused_after_restart_without_seeing_the_original_wall(tmp_path, angle):
    obstacle = box()
    data = manual_points([(0, 0, 8)])
    route = tmp_path / 'route.json'
    first = NavigationGame(physical=[obstacle['polygon']])
    first_memory = RoutePathMemory(route, data)
    assert run_walk(first, data, first_memory, scene(obstacle))[0]
    assert first_memory.count == 1
    # The second scene omits the original wall. Only the successful cached
    # detour can avoid it; a fresh straight-line planner would hit it.
    second = NavigationGame(angle, physical=[obstacle['polygon']])
    fresh_memory = RoutePathMemory(route, data)
    result, _, modes = run_walk(second, data, fresh_memory, scene())
    assert result and 'reusing' in modes
    assert second.collisions == 0 and distance(second.read(), (0, 0, 8)) <= .3
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in second.events) == 1
    assert not second.keys and second.closed


def test_new_obstacle_invalidates_remembered_corridor_and_fresh_route_is_learned(tmp_path):
    data = manual_points([(0, 0, 8)])
    route = tmp_path / 'route.json'
    memory = RoutePathMemory(route, data)
    assert memory.remember(0, trace())
    obstacle = box()
    game = NavigationGame(physical=[obstacle['polygon']])
    result, _, modes = run_walk(game, data, memory, scene(obstacle))
    assert result and 'learning' in modes and game.collisions == 0
    updated = RoutePathMemory(route, data)
    assert max(abs(p[0]) for p in updated.legs['0'][0]) > 2.6
    assert not game.keys and game.closed


def test_interrupted_walk_is_not_saved_and_io_failure_does_not_interrupt_collection(tmp_path, monkeypatch):
    data = manual_points([(0, 0, 8)])
    memory = RoutePathMemory(tmp_path / 'route.json', data)
    stopped = NavigationGame()
    stopped.stop_at = 3.
    assert not run_walk(stopped, data, memory, scene())[0]
    assert not memory.path.exists() and not stopped.keys
    def failure(*a):
        raise OSError('disk unavailable')
    monkeypatch.setattr(memory, 'remember', failure)
    game = NavigationGame()
    result, nav, _ = run_walk(game, data, memory, scene())
    assert result and nav.memory_state == 'write_error'
    assert any(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events)
    assert not game.keys and game.closed


@pytest.mark.parametrize('angle', [0., math.pi / 3, -math.pi / 2])
def test_river_crossing_walk_is_saved_and_reused_with_current_water_checks(tmp_path, angle):
    from tests.unit.test_northern_river_navigation import POINT6, POINT7
    from utils.core.navigation_water import NORTHERN_RIVER_AREAS
    target = (POINT7[0], POINT6[1], POINT7[2])
    data = manual_points([target])
    route = tmp_path / 'oranges.json'
    for repeat in (False, True):
        game = NavigationGame(angle if repeat else 0., physical=NORTHERN_RIVER_AREAS)
        game.position[:] = POINT6
        memory = RoutePathMemory(route, data)
        nav = LocalNavigator(lambda: scene(), clock=game.monotonic, path_memory=memory)
        modes = []
        assert drive_points(data, game.size(), lambda: game, game, game, game,
                            lambda *a, **kw: modes.append(nav.memory_state),
                            clock=game, navigator=nav, collect_wait_seconds=0.)
        assert memory.count == 1 and game.collisions == 0
        if repeat:
            assert 'reusing' in modes
        assert distance(game.read(), target) <= .3
        assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1
        assert not game.keys and game.closed


def test_continuous_learning_keeps_faster_previous_walk_and_counts_every_success(tmp_path):
    memory = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    direct = trace()
    detour = [[0., 0., 0.], [4., 0., 0.], [4., 0., 4.], [4., 0., 8.], [0., 0., 8.]]
    assert memory.remember(0, direct, 3.)
    assert memory.remember(0, detour, 9.)
    assert memory.lookup(0, (0, 0, 0)) == [tuple(p) for p in direct]
    assert memory.remember(0, direct, 4.)
    loaded = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    stats = loaded.statistics[loaded._identity(direct)]
    assert stats['successes'] == 2 and stats['best_seconds'] == 3.
    assert len(loaded.legs['0']) == 2
    assert loaded.learning_summary['0']['completed_walks'] == 3


def test_longer_but_measured_faster_successful_corridor_can_win(tmp_path):
    memory = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    direct = trace()
    detour = [[0., 0., 0.], [4., 0., 0.], [4., 0., 4.], [4., 0., 8.], [0., 0., 8.]]
    assert memory.remember(0, direct, 20.)
    assert memory.remember(0, detour, 8.)
    assert memory.lookup(0, (0, 0, 0))[1] == (4., 0., 0.)


def test_collect_wait_is_not_counted_as_travel_time(tmp_path):
    data = manual_points([(0, 0, 8)])
    memory = RoutePathMemory(tmp_path / 'route.json', data)
    game = NavigationGame()
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=(), path_memory=memory)
    assert drive_points(data, game.size(), lambda: game, game, game, game, lambda *a, **kw: None,
                        clock=game, navigator=nav, collect_wait_seconds=20.)
    record = next(iter(memory.statistics.values()))
    assert 0 < record['best_seconds'] < 20.


def test_legacy_geometry_survives_first_learning_update(tmp_path):
    memory = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    memory.path.parent.mkdir()
    memory.path.write_text(json.dumps({'schema': memory.schema, 'route_fingerprint': memory.fingerprint,
                                      'legs': {'0': [trace()]}}), encoding='utf-8')
    loaded = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    assert loaded.lookup(0, (0, 0, 0))
    assert loaded.remember(0, trace(), 3.)
    assert loaded.count == 1 and len(loaded.legs['0']) == 1


def test_blocked_corridor_is_demoted_when_alternative_finishes(tmp_path):
    data = manual_points([(0, 0, 8)])
    memory = RoutePathMemory(tmp_path / 'route.json', data)
    direct = trace()
    detour = [[0., 0., 0.], [4., 0., 0.], [4., 0., 4.], [4., 0., 8.], [0., 0., 8.]]
    assert memory.remember(0, direct, 3.)
    assert memory.lookup(0, (0, 0, 0))
    memory.reject_selected()
    assert memory.remember(0, detour, 5.)
    loaded = RoutePathMemory(tmp_path / 'route.json', data)
    assert loaded.lookup(0, (0, 0, 0))[1] == (4., 0., 0.)
    assert loaded.statistics[loaded._identity(direct)]['blocked'] == 1


def test_pause_time_is_excluded_from_learned_duration(tmp_path):
    data = manual_points([(0, 0, 8)])
    durations = []
    for paused in (False, True):
        memory = RoutePathMemory(tmp_path / ('paused.json' if paused else 'normal.json'), data)
        game = NavigationGame()
        if paused:
            game.pause_at, game.resume_at = 1., 11.
        assert run_walk(game, data, memory, scene())[0]
        durations.append(next(iter(memory.statistics.values()))['best_seconds'])
    assert abs(durations[0] - durations[1]) < 1.


def test_new_detours_do_not_evict_the_best_measured_corridor(tmp_path):
    memory = RoutePathMemory(tmp_path / 'route.json', manual_points([(0, 0, 8)]))
    assert memory.remember(0, trace(), 2.)
    for x in (1., 2., 3., 4., 5.):
        path = [[0., 0., 0.], [x, 0., 0.], [x, 0., 4.], [x, 0., 8.], [0., 0., 8.]]
        assert memory.remember(0, path, 8. + x)
    assert len(memory.legs['0']) == 3
    assert memory.lookup(0, (0, 0, 0)) == [tuple(p) for p in trace()]
    assert memory.learning_summary['0']['completed_walks'] == 6
