"""Closed-loop skipped visits, retries and recovery without game input."""
from unittest.mock import Mock
import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_coordinate_navigation import scene
from tests.unit.test_point_playback import FakeWindow, WaitingControls, points
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core import thresholds as policy
from utils.flows.coordinate_player import drive_points, _Follower
from utils.ui.point_playback import PointPlayback
from utils.drivers.playback_history import PlaybackHistory


class IntermittentNavigator(LocalNavigator):
    def __init__(self, game, recovered=lambda: False):
        super().__init__(lambda: scene(), clock=game.monotonic, water_areas=())
        self.recovered = recovered

    def next_target(self, position, target, **kwargs):
        if target[2] == 6 and not self.recovered():
            raise NavigationWait('模拟点位不通', reason='no_path')
        return super().next_target(position, target, **kwargs)


def run(game, nav, report, **kwargs):
    return drive_points(manual_points([(0, 0, 6), (4, 0, 0)]), game.size(), lambda: game,
                        game, game, game, report, clock=game, navigator=nav,
                        collect_wait_seconds=0., stable_navigation=True, skip_unreachable=True, **kwargs)


def test_unreachable_point_is_skipped_other_points_collect_and_retries_are_finite(monkeypatch):
    monkeypatch.setattr(policy, 'COORD_VISIT_STALL_SECONDS', 2.)
    game, events = Game(), []
    nav = IntermittentNavigator(game)
    assert run(game, nav, lambda state, message, **kw: events.append((state, kw)))
    assert [state for state, _ in events].count('skipped') == 3
    assert [state for state, _ in events].count('retrying') == 2
    assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 1
    assert game.position[0] > 3.5
    unresolved = [extra for state, extra in events if state == 'cycle_finished'][-1]['unresolved_points']
    assert len(unresolved) == 1 and unresolved[0]['point_id'] == 1
    assert not game.keys and game.closed


def test_point_that_recovers_is_collected_on_deferred_pass_without_double_collecting_others(monkeypatch):
    monkeypatch.setattr(policy, 'COORD_VISIT_STALL_SECONDS', 2.)
    game, phase = Game(), [0]
    nav = IntermittentNavigator(game, lambda: phase[0] > 0)
    end = []
    def report(state, message, **kw):
        if state == 'retrying':
            phase[0] += 1
        if state == 'cycle_finished':
            end.append(kw)
    assert run(game, nav, report)
    assert phase == [1] and end[-1]['unresolved_points'] == []
    assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 2
    assert game.position[2] > 5.5 and not game.keys


def test_f9_at_skip_boundary_prevents_more_input_or_silent_completion(monkeypatch):
    monkeypatch.setattr(policy, 'COORD_VISIT_STALL_SECONDS', 2.)
    game, boundary = Game(), []
    def report(state, message, **kw):
        if state == 'skipped':
            game.stop_at = game.time
            boundary.append(len(game.events))
    assert not run(game, IntermittentNavigator(game), report)
    assert not [e for _, e in game.events[boundary[0]:] if e['type'] == 'key_down']
    assert not game.keys


def test_loop_retries_restore_full_route_for_the_next_cycle(monkeypatch):
    monkeypatch.setattr(policy, 'COORD_VISIT_STALL_SECONDS', 2.)
    game, end = Game(), []
    assert run(game, IntermittentNavigator(game),
               lambda state, msg, **kw: end.append(kw) if state == 'cycle_finished' else None, loop_count=2)
    assert len(end) == 2
    assert [p['cycle'] for p in end[-1]['unresolved_points']] == [1, 2]
    assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 2


def test_position_loss_is_still_an_error_and_is_not_skipped(monkeypatch):
    game = Game()
    game.invalid_at = .1
    with pytest.raises(ValueError, match='lost position'):
        run(game, IntermittentNavigator(game), lambda *a, **kw: None)
    assert not game.keys


def test_pause_time_does_not_charge_deferred_visit_budget(monkeypatch):
    monkeypatch.setattr(policy, 'COORD_VISIT_STALL_SECONDS', 2.)
    game, skipped = Game(), []
    game.pause_at, game.resume_at, game.stop_at = 1.4, 5.4, 6.
    assert not run(game, IntermittentNavigator(game),
                   lambda state, msg, **kw: skipped.append(game.time) if state == 'skipped' else None)
    assert not skipped and not game.keys


def test_stable_recovery_attempts_detour_before_jump():
    game = Game()
    follower = _Follower({'duration': 0, 'client_size': game.size()}, game, game, game, game,
                         lambda *a, **kw: None, game, waypoints=[], stable_navigation=True,
                         jump_on_recovery=True, recovery_attempts=5)
    called = []
    follower.recovery_leg = lambda *a: called.append('detour') or False
    follower.recovery_jump = lambda *a: called.append('jump') or True
    assert follower.recover((0, 0, 10), ((0, 1), (1, 0)), (0, 0, 0), 0, 1)
    assert called == ['detour', 'jump'] and not game.keys


def test_worker_does_not_claim_all_points_complete_when_deferred_points_failed():
    def runner(*a, **kwargs):
        a[6]('cycle_finished', '结束', unresolved_points=[{'name': '山后果树', 'point_id': 1, 'reason': '不通'}])
        return True
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda w: Mock())
    worker.start(points(), 77, stable_navigation=True, skip_unreachable=True)
    worker.thread.join(2)
    status = worker.take_latest()
    assert status['state'] == 'finished' and '未完成' in status['message']
    assert '回放完成' not in status['message']
    assert status['unresolved_points'][0]['name'] == '山后果树'
    worker.close()


def test_history_retains_skip_reasons_and_unresolved_points(tmp_path):
    import json
    history = PlaybackHistory(tmp_path, points(), '果树', {})
    history.event({'state': 'skipped', 'message': '跳过', 'point_id': 1, 'skip_reason': '山壁'})
    history.event({'state': 'finished', 'message': '结束', 'unresolved_points': [{'point_id': 1, 'reason': '山壁'}]})
    history.close()
    summary = json.loads((history.path/'summary.json').read_text(encoding='utf-8'))
    assert summary['skipped_count'] == 1
    assert summary['unresolved_points'] == [{'point_id': 1, 'reason': '山壁'}]
