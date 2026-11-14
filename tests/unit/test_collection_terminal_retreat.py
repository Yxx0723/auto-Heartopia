import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core.coordinate_route import make_basis
from utils.flows.coordinate_player import _Follower, _Paused, _Stopped


ORIGIN = (0, 0, 0)
DESTINATION = (0, 0, .6)
EXIT = (0, 0, -.8)
BASIS = make_basis((0, 0, 1), (1, 0, 0))


@pytest.mark.parametrize('mode', ['geometry', 'failed_entry', 'one_safe_direction'])
def test_terminal_candidate_rejections(mode):
    nav = LocalNavigator(lambda: None, water_areas=(), clock=lambda: 0.)
    obstacles = Mock()
    obstacles.risk_cost.return_value = 0
    obstacles.clear.side_effect = lambda a, b, **_: (
        mode == 'failed_entry' or (mode == 'one_safe_direction' and b == (0, -.1))
    )
    if mode == 'failed_entry':
        for direction in range(8):
            angle = direction * math.pi / 4
            endpoint = (math.sin(angle), 0, math.cos(angle))
            for attempt in range(2):
                nav.risks.record(ORIGIN, endpoint, attempt_id=(direction, attempt))
    keys = nav.steering_keys(ORIGIN, DESTINATION, BASIS, .1, obstacles=obstacles)
    if mode == 'one_safe_direction':
        assert keys == {'s'}
        assert len(nav.steering_rejections) == 7
    else:
        assert keys == set()
        assert len({row['candidate_direction'] for row in nav.steering_rejections}) == 8
    assert all(row['candidate_rejection_reason'] == [
        'failed_entry' if mode == 'failed_entry' else 'geometry'
    ] for row in nav.steering_rejections)


def follower():
    f = _Follower.__new__(_Follower)
    f.navigator = Mock()
    f.navigator.scene = {'state': 'ready', 'cache_context': 'a'}
    f.navigator.path_escape_target.return_value = EXIT
    f.navigator.path_escape_keys.return_value = {'s'}
    f.clock = SimpleNamespace(monotonic=lambda: 0.)
    f.read = Mock(side_effect=[ORIGIN, ORIGIN, EXIT])
    f.steering_travel = Mock(return_value=.1)
    f.move_deadline = .2
    f.apply_movement = Mock()
    f.wait_movement = Mock()
    f.release = Mock()
    f.guard = Mock()
    return f


def test_safe_retreat_reaches_alternate_position_for_replanning():
    f = follower()
    assert f.terminal_retreat(DESTINATION, BASIS, ORIGIN) == ('replan', EXIT)
    f.apply_movement.assert_called_once_with({'s'}, ORIGIN, EXIT)
    f.navigator.path_escape_keys.assert_called_once()


@pytest.mark.parametrize('mode', ['no_exit', 'arrived', 'no_pulse', 'scope_changed'])
def test_unsafe_or_unnecessary_retreat_does_not_move(mode):
    f = follower()
    if mode == 'no_exit':
        f.navigator.path_escape_target.return_value = None
    elif mode == 'arrived':
        f.read.side_effect = [DESTINATION]
    elif mode == 'no_pulse':
        f.navigator.path_escape_keys.return_value = set()
    else:
        def change_scope(*args):
            f.navigator.scene['cache_context'] = 'b'
            return EXIT
        f.navigator.path_escape_target.side_effect = change_scope
    if mode == 'scope_changed':
        with pytest.raises(NavigationWait):
            f.terminal_retreat(DESTINATION, BASIS, ORIGIN)
    else:
        assert f.terminal_retreat(DESTINATION, BASIS, ORIGIN)[0] == {
            'no_exit': 'no_safe_exit', 'arrived': 'arrival', 'no_pulse': 'no_safe_exit_pulse'
        }[mode]
    f.apply_movement.assert_not_called()

@pytest.mark.parametrize('interruption', [_Paused, _Stopped, NavigationWait])
def test_terminal_recovery_interruptions_release_inputs(interruption):
    f = follower()
    f.cycle = 1
    f.report = Mock()
    f.navigator.steering_rejections = []
    f.terminal_retreat = Mock(side_effect=interruption('interrupted'))
    attempts = set()
    args = (attempts, 9, 10, {'id': 10, 'position': DESTINATION}, ORIGIN, BASIS)
    if interruption is NavigationWait:
        f.recover_terminal_entry(*args)
        assert f.report.call_args.kwargs['recovery_decision'] == 'snapshot_unavailable'
    else:
        with pytest.raises(interruption):
            f.recover_terminal_entry(*args)
    assert f.release.call_count == 2
    from utils.flows.coordinate_player import _SkipPoint
    with pytest.raises(_SkipPoint, match='attempt_already_used'):
        f.recover_terminal_entry(*args)
    f.terminal_retreat.assert_called_once()


def test_successful_exit_cannot_retreat_again_after_failed_reentry():
    from utils.flows.coordinate_player import _SkipPoint

    f = follower()
    f.cycle = 1
    f.report = Mock()
    f.navigator.steering_rejections = []
    attempts = set()
    args = (attempts, 9, 10, {'id': 10, 'position': DESTINATION}, ORIGIN, BASIS)
    f.recover_terminal_entry(*args)
    assert f.report.call_args.kwargs['recovery_decision'] == 'replan'
    assert f.report.call_args.kwargs['player_position_after'] == list(EXIT)
    with pytest.raises(_SkipPoint, match='attempt_already_used'):
        f.recover_terminal_entry(*args)
    f.apply_movement.assert_called_once()
    assert f.release.call_count == 4
