"""Real failed patrol coordinates and simulated collision/arrival regressions."""
import json
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_special_patrol_flow import run, target
from tests.unit.test_coordinate_navigation import box
from utils.core import thresholds as policy
from utils.core.coordinate_navigation import LocalNavigator
from utils.core.coordinate_route import key_vector
from utils.drivers.playback_history import PlaybackHistory
from utils.flows.coordinate_player import drive_points
from utils.flows.coordinate_player import _Follower


def empty_scene():
    return {'state': 'ready', 'world_state': 'ready', 'global_special_state': 'ready',
            'objects': [], 'colliders': []}


def test_actual_first_patrol_tree_altitude_cannot_force_waiting_for_a_nonexistent_floor():
    game, report = Game(), Mock()
    game.position = [-94.73339080810547, 16.36632537841797, -146.55116271972656]
    seed = [-91.125, 20.11185646057129, -148.7550048828125]
    assert run(game, empty_scene, [seed], report)
    assert game.time < 5. and not game.events
    assert any(c.kwargs.get('patrol_phase') == 'scan' for c in report.call_args_list)
    assert not any(c.args[0] == 'calibrating' for c in report.call_args_list)
    assert not game.keys


def test_far_search_area_stops_on_entry_even_if_definition_altitude_is_above_the_ground():
    game, report = Game(), Mock()
    assert run(game, empty_scene, [[0., 5., 25.]], report)
    assert game.position[2] >= 17. and game.position[1] == 0.
    assert game.time < 20.
    assert not any(e.get('key') == 'f' for _, e in game.events)
    assert not any('等待落地' in c.args[1] for c in report.call_args_list)
    assert not game.keys


def test_discovery_inside_search_area_still_requires_actual_interaction_range_before_f():
    game = Game()
    obj = target([0., 0., 25.])
    loaded = False
    def scene():
        nonlocal loaded
        loaded = loaded or game.position[2] >= 10.
        return {**empty_scene(), 'objects': [obj] if loaded else []}
    positions = []
    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            positions.append(game.read())
    game.on_input = on_input
    assert run(game, scene, [[0., 5., 25.]])
    assert len(positions) == 2 and all(abs(p[2]-25.) <= 1.25 for p in positions)
    assert not game.keys


def test_f9_still_interrupts_nearby_area_observation_without_any_collection():
    game = Game(); game.stop_at = .2
    assert not run(game, empty_scene, [[0., 5., 2.]])
    assert not game.events and not game.keys


def test_ordinary_collection_point_keeps_strict_floor_check():
    game, report = Game(), Mock(); game.stop_at = 4.
    nav = LocalNavigator(empty_scene, clock=game.monotonic, water_areas=())
    assert not drive_points(manual_points([(0., 5., 0.)]), game.size(), lambda: game,
                            game, game, game, report, clock=game, navigator=nav,
                            continuous_recovery=True, recovery_attempts=5, collect_wait_seconds=0.)
    assert any('等待落地' in c.args[1] for c in report.call_args_list)
    assert not any(e.get('key') == 'f' for _, e in game.events)


@pytest.mark.parametrize('category', ['fence', 'handrail', 'other'])
def test_ground_detour_never_uses_a_direction_through_live_solids(category):
    collider = {**box(.3, .5, -2, 2, (-.1, .8)), 'category': category}
    data = {**empty_scene(), 'colliders': [collider]}
    nav = LocalNavigator(lambda: data, water_areas=())
    basis = {'forward': (1., 0.), 'right': (0., 1.)}
    keys = nav.detour_keys((0, 0, 0), (3, 0, 0), basis, 1.)
    assert keys != {'w'}
    if keys:
        vector = key_vector(keys, basis)
        norm = sum(v*v for v in vector)**.5
        assert nav.obstacles.clear((0, 0), tuple(v/norm for v in vector), escape=True)


def test_jump_can_probe_low_rail_but_never_assume_a_building_is_jumpable():
    low = box(.3, .5, -2, 2, (-.1, .8))
    tall = box(.3, .5, -2, 2, (-.1, 7.))
    data = {**empty_scene(), 'colliders': [low]}
    nav = LocalNavigator(lambda: data, water_areas=())
    basis = {'forward': (1., 0.), 'right': (0., 1.)}
    assert nav.recovery_keys((0, 0, 0), (3, 0, 0), basis, 1.) == {'w'}
    data = {**empty_scene(), 'colliders': [tall]}
    assert nav.recovery_keys((0, 0, 0), (3, 0, 0), basis, 1.) != {'w'}


def test_missing_scene_never_allows_a_ground_detour():
    nav = LocalNavigator(lambda: None, water_areas=())
    assert nav.detour_keys((0, 0, 0), (3, 0, 0), {}, 1.) is None


def test_recorded_collision_evidence_is_bounded_and_does_not_claim_the_nearest_box_caused_a_hit():
    colliders = [dict(box(i*.05, i*.05+.1, 1, 2), source='scene', category='fence') for i in range(20)]
    nav = LocalNavigator(lambda: {**empty_scene(), 'colliders': colliders}, water_areas=())
    evidence = nav.blocked((0, 0, 0), (0, 0, 6))
    assert evidence['kind'] == 'stalled-motion'
    assert len(evidence['nearby_colliders']) == policy.COORD_NAV_EVIDENCE_LIMIT
    assert evidence['target'] == [0, 0, 6] and not nav.path


def test_history_keeps_distinct_plans_separate_from_actual_trace_and_keeps_stall_evidence(tmp_path):
    history = PlaybackHistory(tmp_path, {}, '萤石', {})
    status = {'state': 'playing', 'position': [0., 0., 0.],
              'navigation_path': [(0., 0., 3.)], 'patrol_checked': 2, 'patrol_total': 126}
    history.event(status)
    history.event(status)
    history.sample([0., 0., 1.], 1.)
    history.event({'state': 'navigating', 'navigation_path': [], 'navigation_event': 'blocked',
                   'obstruction': {'kind': 'stalled-motion', 'nearby_colliders': []}})
    history.close()
    plans = [json.loads(line) for line in (history.path/'navigation.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(plans) == 2 and plans[0]['path'] == [[0., 0., 3.]] and plans[1]['path'] == []
    summary = json.loads((history.path/'summary.json').read_text(encoding='utf-8'))
    assert summary['navigation_plan_count'] == 1 and summary['navigation_blocked_count'] == 1
    assert summary['patrol_checked'] == 2 and summary['patrol_total'] == 126
    actual = [json.loads(line) for line in (history.path/'trajectory.jsonl').read_text(encoding='utf-8').splitlines()]
    assert actual[0]['position'] == [0., 0., 1.]


def test_vertical_movement_is_not_learned_as_camera_direction(monkeypatch):
    import utils.flows.coordinate_player as player
    original = player.rotate_basis
    class BouncingGame(Game):
        def sleep(self, dt):
            super().sleep(dt)
            self.position[1] = max(0., 2.-abs(self.time-4.)*3.)
    game = BouncingGame()
    calls = []
    def observed(basis, keys, displacement):
        assert abs(displacement[1]) <= policy.COORD_DIRECTION_MAX_VERTICAL_DELTA
        calls.append(game.time)
        return original(basis, keys, displacement)
    monkeypatch.setattr(player, 'rotate_basis', observed)
    nav = LocalNavigator(empty_scene, clock=game.monotonic, water_areas=())
    assert drive_points(manual_points([(0., 0., 30.)]), game.size(), lambda: game,
                        game, game, game, Mock(), clock=game, navigator=nav,
                        recovery_attempts=5, continuous_recovery=True)
    assert calls and not game.keys


def test_direction_learning_waits_until_the_recovery_observation_quiet_period_ends(monkeypatch):
    import utils.flows.coordinate_player as player
    game, calls = Game(), []
    original = player.rotate_basis
    def observed(basis, keys, displacement):
        calls.append(game.time)
        return original(basis, keys, displacement)
    monkeypatch.setattr(player, 'rotate_basis', observed)
    nav = LocalNavigator(empty_scene, clock=game.monotonic, water_areas=())
    follower = _Follower({'duration':0., 'client_size':list(game.size())}, game, game,
                         game, game, Mock(), game, navigator=nav,
                         waypoints=[{**p, 'wait': 0.} for p in manual_points([(0., 0., 30.)])['points']])
    follower.direction_quiet_until = 5.
    try:
        assert follower.follow()
    finally:
        follower.release()
    assert calls and min(calls) >= 5. and not game.keys
