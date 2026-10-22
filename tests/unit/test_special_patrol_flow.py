"""Patrol/arrival/collection orchestration uses simulated movement only."""
from unittest.mock import Mock
import pytest
from tests.unit.test_coordinate_player import Game
from utils.core.coordinate_navigation import LocalNavigator
from utils.flows.coordinate_player import drive_points
from utils.flows.special_patrol import drive_special_patrol


@pytest.fixture(autouse=True)
def miniature_search_regions(monkeypatch):
    # These older synthetic worlds span only 3-8 units. Keep them testing
    # traversal rather than starting inside the production 8-unit search area.
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_SURVEY_RADIUS', .5)


def data(checkpoints):
    identity = {'category': 'roaming_oak', 'static_id': 42001, 'press_count': 2}
    stamp = '2026-10-21T12:00:00+08:00'
    return {'schema': 'coordinate-points-v1', 'special_patrol': {**identity, 'checkpoints': checkpoints},
            'points': [{'id': 1, 'name': '溜溜橡木', 'position': [0., 0., 0.],
                        'special_resource': identity, 'sample_at': stamp, 'saved_at': stamp}]}


def target(position):
    return {'category': 'roaming_oak', 'resource_static_id': 42001, 'resource_available': 2,
            'map_dynamic': True, 'position': position}


def run(game, scene, points, report=None):
    nav = LocalNavigator(scene, clock=game.monotonic, water_areas=())
    return drive_special_patrol(data(points), game.size(), lambda: game, game, game, game,
        report or Mock(), clock=game, navigator=nav, point_runner=drive_points,
        continuous_recovery=True, recovery_attempts=5, collect_wait_seconds=0., loop_count=1)


def test_no_known_target_starts_patrol_and_live_detection_interrupts_search_to_collect():
    game = Game()
    obj = target([0., 0., 6.])
    loaded = False
    def scene():
        nonlocal loaded
        loaded = loaded or game.position[2] > 1.5
        return {'state': 'ready', 'global_special_state': 'ready', 'colliders': [],
                'objects': [obj] if loaded else []}
    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            assert scene()['objects'] and abs(game.position[2] - 6) <= 1.25
    game.on_input = on_input
    assert run(game, scene, [[0., 0., 6.]])
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 2
    assert not game.keys


def test_survey_arrival_never_presses_f_and_reports_only_available_coverage(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_DWELL_SECONDS', .1)
    game, report = Game(), Mock()
    assert run(game, lambda: {'state': 'ready', 'global_special_state': 'ready', 'objects': [], 'colliders': []},
               [[0., 0., 3.]], report)
    assert not any(e.get('key') == 'f' for _, e in game.events)
    assert any(c.kwargs.get('resource_outcome') == 'not_found' for c in report.call_args_list)


def test_empty_hint_is_rejected_then_other_area_is_searched(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_SPECIAL_LOAD_WAIT_SECONDS', .15)
    game, report = Game(), Mock()
    obj = target([0., 0., 8.])
    hint = {'category': 'roaming_oak', 'resource_static_id': 42001, 'position': [0., 0., 0.],
            'resource_global_validated': True, 'resource_global_only': True, 'map_dynamic': True}
    loaded = False
    def scene():
        nonlocal loaded
        loaded = loaded or game.position[2] > 2.
        return {'state': 'ready', 'global_special_state': 'ready', 'colliders': [],
                'global_special_resources': [hint], 'objects': [obj] if loaded else []}
    assert run(game, scene, [[0., 0., 8.]], report)
    assert any(c.kwargs.get('patrol_result') == 'unconfirmed' for c in report.call_args_list)
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 2


def test_loaded_but_unavailable_resource_ends_without_any_input():
    game, report = Game(), Mock()
    obj = target([0., 0., 0.]); obj['resource_available'] = 0
    assert run(game, lambda: {'state': 'ready', 'objects': [obj]}, [[0., 0., 6.]], report)
    assert not game.events
    assert any(c.kwargs.get('resource_outcome') == 'unavailable' for c in report.call_args_list)


def test_stop_before_f6_never_opens_a_reader():
    game = Game(); game.running = False; game.stop_at = .2
    factory = Mock(side_effect=AssertionError('No reader before F6'))
    assert not drive_special_patrol(data([[0., 0., 3.]]), game.size(), factory, game, game,
        game, Mock(), clock=game, navigator=LocalNavigator(lambda: {}, water_areas=()), point_runner=drive_points)
    factory.assert_not_called()
    assert not game.events


def test_unreachable_survey_leg_is_skipped_instead_of_waiting_forever(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_MIN_LEG_SECONDS', .3)
    monkeypatch.setattr(policy, 'COORD_PATROL_MAX_LEG_SECONDS', .3)
    game, report = Game(speed=0), Mock()
    assert run(game, lambda: {'state': 'ready', 'global_special_state': 'ready', 'objects': [], 'colliders': []},
               [[0., 0., 6.]], report)
    assert any(c.kwargs.get('patrol_result') == 'unreachable' for c in report.call_args_list)
    assert not any(e.get('key') == 'f' for _, e in game.events)


def test_world_switch_does_not_reuse_the_old_survey():
    game = Game()
    def scene():
        return {'state': 'ready', 'global_special_state': 'ready', 'objects': [], 'colliders': [],
                'global_special_context': [1, 2, 3 if game.time < .4 else 4]}
    with pytest.raises(ValueError, match='世界'):
        run(game, scene, [[0., 0., 6.]])
    assert not game.keys


def test_unreachable_visible_target_does_not_interrupt_each_search_leg_again(monkeypatch):
    from utils.core.special_patrol import PatrolLegBlocked
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_DWELL_SECONDS', .01)
    game, report = Game(), Mock()
    obj = target([0., 0., 6.])
    nav = LocalNavigator(lambda: {'state': 'ready', 'objects': [obj], 'global_special_state': 'ready'},
                         clock=game.monotonic, water_areas=())
    def runner(leg, *args, **kw):
        if 'special_resource' in leg['points'][0]:
            raise PatrolLegBlocked('blocked')
        return True
    spy = Mock(side_effect=runner)
    assert drive_special_patrol(data([[0., 0., 3.]]), game.size(), lambda: game, game, game, game,
        report, clock=game, navigator=nav, point_runner=spy)
    assert spy.call_count == 2
    assert not game.events


def test_f6_pause_during_survey_never_sends_keys_until_resumed(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_DWELL_SECONDS', .01)
    game = Game(); game.pause_at = .4; game.resume_at = 2.
    assert run(game, lambda: {'state': 'ready', 'global_special_state': 'ready', 'objects': [], 'colliders': []},
               [[0., 0., 6.]])
    assert not [e for t,e in game.events if .45 < t < 2. and e['type'] == 'key_down']


def test_no_path_wait_also_uses_the_search_leg_budget(monkeypatch):
    from utils.core import thresholds as policy
    from utils.core.coordinate_navigation import NavigationWait
    monkeypatch.setattr(policy, 'COORD_PATROL_MIN_LEG_SECONDS', 3.)
    monkeypatch.setattr(policy, 'COORD_PATROL_MAX_LEG_SECONDS', 3.)
    game, report = Game(), Mock()
    nav = LocalNavigator(lambda: {'state': 'ready', 'global_special_state': 'ready',
        'objects': [], 'colliders': []}, clock=game.monotonic, water_areas=())
    nav.next_target = Mock(side_effect=NavigationWait('no path'))
    assert drive_special_patrol(data([[0., 0., 6.]]), game.size(), lambda: game, game, game, game,
        report, clock=game, navigator=nav, point_runner=drive_points,
        recovery_attempts=5, continuous_recovery=True)
    assert nav.next_target.called
    assert game.time < 5.
    assert any(c.kwargs.get('patrol_result') == 'unreachable' for c in report.call_args_list)


def test_f9_interrupts_hint_arrival_confirmation_without_f():
    game = Game(); game.stop_at = .3
    hint = {'category': 'roaming_oak', 'resource_static_id': 42001, 'position': [0., 0., 0.],
            'resource_global_validated': True, 'resource_global_only': True, 'map_dynamic': True}
    scene = lambda: {'state': 'ready', 'global_special_state': 'ready', 'objects': [],
                     'global_special_resources': [hint], 'colliders': []}
    assert not run(game, scene, [[0., 0., 6.]])
    assert not game.events and not game.keys


def test_house_patrol_starts_at_nearest_then_visits_numbered_doors_without_f(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_DWELL_SECONDS', .01)
    game, report = Game(), Mock()
    game.position = [161., 0., 0.]
    plan = data([[float(n*20), 0., 0.] for n in range(1, 13)])
    plan['special_patrol'].update(checkpoint_kind='house-entrances', checkpoint_houses=list(range(1, 13)))
    nav = LocalNavigator(lambda: {'state': 'ready', 'global_special_state': 'ready', 'objects': [], 'colliders': []},
                         clock=game.monotonic, water_areas=())
    def runner(leg, size, factory, *args, **kw):
        game.position = list(leg['points'][0]['position'])
        factory().read_for_playback()
        return True
    assert drive_special_patrol(plan, game.size(), lambda: game, game, game, game, report,
                               clock=game, navigator=nav, point_runner=runner)
    scanned = [c.kwargs['patrol_house'] for c in report.call_args_list if c.kwargs.get('patrol_phase') == 'scan']
    assert scanned == [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
    assert not game.events and not game.keys
