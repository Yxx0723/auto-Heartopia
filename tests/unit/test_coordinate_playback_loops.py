"""Finite replay loops preserve point order, dwell and interrupt handling."""
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_point_playback import FakeWindow, WaitingControls, points
from utils.flows.coordinate_player import drive_points
from utils.ui.point_playback import PointPlayback


def loop_run(game, count=2, positions=((0, 0, 0), (0, 0, 3)), report=lambda *a, **k: None, **options):
    return drive_points(manual_points(positions), game.size(), lambda: game, game, game, game,
                        report, clock=game, loop_count=count, **options)


def test_every_loop_visits_all_points_and_finishes_collection_wait_at_the_last_point():
    game = Game()
    reached, completed, releases = [], [], []
    def report(state, message, **extra):
        assert extra['loop_count'] == 3 and f"{extra['cycle']}/3" in message
        if state == 'reached':
            reached.append((extra['cycle'], extra['waypoint'], game.time))
        if state == 'cycle_finished':
            completed.append(extra['cycle'])
            assert not game.keys
    on_input = game.execute
    def execute(event):
        on_input(event)
        if event == {'type': 'key_up', 'key': 'f'}:
            releases.append(game.time)
    game.execute = execute
    assert loop_run(game, count=3, report=report, collect_wait_seconds=.4)
    assert [(cycle, point) for cycle, point, at in reached] == [(c, p) for c in (1, 2, 3) for p in (1, 2)]
    assert completed == [1, 2, 3] and len(releases) == 6
    assert all(at - up >= .4 - 1e-6 for (_, _, at), up in zip(reached, releases))
    assert not game.keys and game.closed


def test_nearest_selection_only_applies_to_first_loop_then_full_saved_order():
    game = Game()
    game.position = [6., 0., 0.]
    reached, calibrated = [], []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append((extra['cycle'], extra['waypoint']))
        if state == 'calibrating':
            calibrated.append(extra['cycle'])
    assert loop_run(game, positions=((0, 0, 0), (4, 0, 0), (8, 0, 0)),
                    start_mode='nearest', report=report, collect_wait_seconds=0)
    assert reached == [(1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    assert calibrated == [1]  # Reuse feedback direction; no extra walking calibration at resource nodes.
    assert not game.keys and game.closed


@pytest.mark.parametrize('interrupt', ['stop', 'pause', 'focus'])
def test_interrupt_at_loop_boundary_does_not_start_input_for_next_loop(interrupt):
    game = Game()
    boundary, reached = [], []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append((extra['cycle'], extra['waypoint']))
        if state == 'cycle_finished':
            boundary.append(len(game.events))
            if interrupt == 'stop':
                game.stop_at = game.time
            elif interrupt == 'pause':
                game.pause_at, game.stop_at = game.time, game.time + .3
            else:
                game.focus, game.stop_at = False, game.time + .3
    assert not loop_run(game, report=report)
    assert reached == [(1, 1), (1, 2)]
    assert not [e for _, e in game.events[boundary[0]:] if e['type'] == 'key_down']
    assert not game.keys and game.closed


def test_pause_in_second_loop_keeps_cycle_target_and_dwell_without_duplicate_f():
    game = Game()
    reached, paused = [], []
    def report(state, message, **extra):
        if state == 'collection_wait' and extra['cycle'] == 2 and extra['waypoint'] == 1 and not paused:
            paused.append(game.time)
            game.pause_at, game.resume_at = game.time + .1, game.time + .6
        if state == 'reached':
            reached.append((extra['cycle'], extra['waypoint']))
    assert loop_run(game, report=report, collect_wait_seconds=.5)
    assert reached == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 4
    assert not [e for at, e in game.events if paused[0] + .11 < at < paused[0] + .6 and e['type'] == 'key_down']
    assert not game.keys and game.closed


def test_single_point_and_duplicate_points_collect_on_each_selected_cycle():
    for positions in (((0, 0, 0),), ((0, 0, 0), (0, 0, 0))):
        game = Game()
        assert loop_run(game, count=3, positions=positions, collect_wait_seconds=0)
        assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 3 * len(positions)
        assert not game.keys and game.closed


def test_per_point_wait_overrides_are_preserved_across_loops_without_mutating_route():
    from copy import deepcopy
    game = Game()
    data = manual_points([(0, 0, 0), (0, 0, 3)])
    data['points'][0]['collect_wait'], data['points'][1]['collect_wait'] = 0., .3
    original, reached = deepcopy(data), []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(game.time)
    assert drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, loop_count=2, collect_wait_seconds=7.)
    ups = [at for at, e in game.events if e == {'type': 'key_up', 'key': 'f'}]
    assert [at - up for at, up in zip(reached, ups)] == pytest.approx([0, .3, 0, .3])
    assert data == original and not game.keys


def test_coordinate_loss_in_second_loop_never_advances_to_a_third_loop():
    game = Game()
    cycles = []
    def report(state, message, **extra):
        cycles.append(extra['cycle'])
        if state == 'reached' and extra['cycle'] == 2 and extra['waypoint'] == 1:
            game.invalid_at = game.time + .1
    with pytest.raises(ValueError, match='lost position'):
        loop_run(game, count=3, report=report)
    assert max(cycles) == 2 and not game.keys and game.closed


def test_active_movement_budget_applies_to_each_cycle_instead_of_all_loops(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_POINT_PLAYBACK_SECONDS', 3.5)
    game = Game()
    assert loop_run(game, count=3, positions=((0, 0, 0), (0, 0, 3)))
    assert game.time > 3.5 and not game.keys


@pytest.mark.parametrize('count', [0, -1, 1000, True, 2.5, '2', math.inf])
def test_invalid_loop_count_rejected_before_process_access_or_input(count):
    game = Game()
    factory = Mock()
    with pytest.raises(ValueError, match='循环'):
        drive_points(manual_points([(0, 0, 0)]), game.size(), factory, game, game, game,
                     lambda *a, **k: None, clock=game, loop_count=count)
    factory.assert_not_called()
    assert not game.events


def test_worker_passes_selected_count_and_reports_completed_loops():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda w: Mock())
    worker.start(points(), 77, loop_count=3)
    worker.thread.join(timeout=2)
    assert runner.call_args.kwargs['loop_count'] == 3
    result = worker.take_latest()
    assert result['state'] == 'finished' and '3' in result['message']
    assert result['cycle'] == result['loop_count'] == 3
    worker.close()


@pytest.mark.parametrize('count', [0, 1000, True, 1.5])
def test_worker_rejects_invalid_loop_count_before_starting_thread(count):
    worker = PointPlayback('profile.json', runner=Mock(), window_factory=Mock())
    with pytest.raises(ValueError, match='循环'):
        worker.start(points(), 77, loop_count=count)
    assert worker.thread is None


@pytest.mark.parametrize('count', [1, 999])
def test_loop_count_boundaries_are_accepted(count):
    from utils.core.coordinate_points import validate_loop_count
    assert validate_loop_count(count) == count
