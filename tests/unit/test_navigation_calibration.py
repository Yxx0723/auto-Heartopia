from pathlib import Path
from unittest.mock import Mock
from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_coordinate_navigation import scene
from tests.unit.test_point_playback import FakeWindow, WaitingControls, points
from utils.core.coordinate_navigation import LocalNavigator
from utils.flows.coordinate_player import drive_points, _Follower, _CalibrationRetry
from utils.ui.point_playback import PointPlayback


def test_repeated_local_calibration_failure_skips_point_and_continues_without_false_completion(monkeypatch):
    game, calls, reports = Game(), [], []
    game.stop_at = 15.
    def calibrate(self, reference, target_index=0):
        if target_index == 0:
            calls.append(game.time)
            raise _CalibrationRetry('synthetic wall slide')
        return {'forward': [0, 1], 'right': [1, 0]}
    monkeypatch.setattr(_Follower, 'calibrate', calibrate)
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=())
    completed = drive_points(manual_points([(0, 0, 5), (0, 0, 1)]), game.size(), lambda: game,
                             game, game, game, lambda state, message, **extra: reports.append((state, extra)),
                             clock=game, navigator=nav, collect_wait_seconds=0.)
    assert not completed and len(calls) == 3
    assert any(state == 'skipped' and extra['waypoint'] == 1 for state, extra in reports)
    assert any(state == 'reached' and extra['waypoint'] == 2 for state, extra in reports)
    assert not any(state == 'cycle_finished' for state, _ in reports)
    assert reports[-1][0] == 'partial' and not game.keys and game.closed


def test_normal_aligned_motion_does_not_relearn_basis_from_each_displacement(monkeypatch):
    import utils.flows.coordinate_player as player
    def forbidden(*args):
        raise AssertionError('aligned displacement must not rotate the trusted basis')
    monkeypatch.setattr(player, 'rotate_basis', forbidden)
    game = Game()
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=())
    assert drive_points(manual_points([(0, 0, 6)]), game.size(), lambda: game, game, game, game,
                        lambda *a, **kw: None, clock=game, navigator=nav)


def test_worker_preserves_partial_outcome_in_history_instead_of_reporting_stopped(tmp_path):
    def runner(data, size, factory, device, window, controls, report, **kwargs):
        report('partial', 'one point not reached', skipped_count=1)
        return False
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           diagnostics_path=tmp_path/'logs'/'playback.jsonl')
    worker.start(points(), 77)
    worker.thread.join(timeout=2)
    status = worker.take_latest()
    assert status['state'] == 'partial' and '完成时间' not in status['message']
    import json
    summary = json.loads((Path(status['history_path'])/'summary.json').read_text(encoding='utf-8'))
    assert summary['state'] == 'partial' and summary['skipped_count'] == 1
    worker.close()


def test_no_path_recovery_budget_ends_point_without_infinite_retries_or_false_arrival():
    from tests.unit.test_coordinate_navigation import NavigationGame, box
    wall = box(-30,30,2,7)
    game = NavigationGame(physical=[wall['polygon']])
    game.stop_at = 100.
    nav = LocalNavigator(lambda: scene(wall), clock=game.monotonic, water_areas=())
    reports = []
    assert not drive_points(manual_points([(0,0,5)]),game.size(),lambda:game,game,game,game,
                            lambda state,message,**extra:reports.append((state,extra)),clock=game,
                            navigator=nav,continuous_recovery=True,recovery_attempts=5)
    assert game.time < game.stop_at
    assert any(state == 'skipped' for state,_ in reports)
    assert not any(state in ('reached','cycle_finished') for state,_ in reports)
    assert reports[-1][0] == 'partial' and not game.keys
