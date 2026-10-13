"""Failure accounting and adaptive detours use simulated movement only."""
import pytest

from tests.unit.test_coordinate_player import Game, WallGame, recovery_run
from tests.unit.test_coordinate_jump_recovery import RetryJumpGame, space_events


class SingleJumpGame(RetryJumpGame):
    def execute(self, event):
        if event == {'type': 'key_down', 'key': 'space'}:
            self.wall_jumps = 1  # One held pulse clears each low barrier.
        super().execute(event)


@pytest.mark.parametrize('jump', [True, False])
def test_easy_obstacles_do_not_consume_budget_before_the_same_waypoint(jump):
    walls = (2., 5., 8., 11.) if jump else (2.,)
    game = SingleJumpGame(walls) if jump else WallGame(walls=walls)
    cleared, failures, blocked = [], [], []
    def report(state, message, **extra):
        if extra.get('recovery_result') == 'cleared':
            cleared.append(extra['recovery_failures'])
        if extra.get('recovery_result') == 'failed':
            failures.append(extra['recovery_failures'])
        if state == 'blocked':
            blocked.append(message)
            game.stop_at = game.time
    assert recovery_run(game, positions=((0, 0, 14),), jump=jump, attempts=1, report=report)
    assert len(cleared) == len(walls) and failures == blocked == []
    assert not game.keys and game.closed


def test_successful_detour_keeps_the_full_budget_for_a_later_impassable_obstacle():
    game = WallGame()
    sleep = game.sleep
    def later_wall(dt):
        before = game.position.copy()
        sleep(dt)
        if game.position[2] >= 5. and before[2] < 5.:
            game.position[2] = before[2]
    game.sleep = later_wall
    attempts, failed, cleared = [], [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if extra.get('recovery_result') == 'failed':
            failed.append(extra['recovery_failures'])
        if extra.get('recovery_result') == 'cleared':
            cleared.append(extra['recovery_failures'])
        if state == 'blocked':
            game.stop_at = game.time
    assert not recovery_run(game, positions=((0, 0, 8),), attempts=1, report=report)
    assert attempts == [1, 1] and cleared == [0] and failed == [1]
    assert not game.keys and game.closed


def test_separate_difficult_obstacles_get_a_fresh_failure_budget_after_real_progress():
    game = RetryJumpGame((2., 5., 8.))
    failures, cleared = [], []
    def report(state, message, **extra):
        if extra.get('recovery_result') == 'failed':
            failures.append(extra['recovery_failures'])
        if extra.get('recovery_result') == 'cleared':
            cleared.append(extra['recovery_failures'])
        if state == 'blocked':
            game.stop_at = game.time
    assert recovery_run(game, positions=((0, 0, 12),), jump=True, attempts=2, report=report)
    assert game.cleared == [2., 5., 8.]
    assert failures == [1, 1, 1] and cleared == [0, 0, 0]
    assert len(space_events(game)) == 12 and not game.keys


def test_wider_obstacle_is_bypassed_by_gradually_widening_left_right_probes():
    game = WallGame()
    game.walls = ()
    sleep = game.sleep
    def wide_wall(dt):
        before = game.position.copy()
        sleep(dt)
        if abs(game.position[0]) < 1.4 and game.position[2] >= 2. and before[2] < 3.:
            game.position[2] = before[2]
    game.sleep = wide_wall
    failures, attempts = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if extra.get('recovery_result') == 'failed':
            failures.append(extra['recovery_failures'])
        if state == 'blocked':
            game.stop_at = game.time
    assert recovery_run(game, positions=((0, 0, 6),), attempts=5, report=report)
    assert attempts == [1, 2, 3] and failures == [1, 2]
    assert not game.keys and game.closed


def test_small_forward_creeps_do_not_reset_failures_or_allow_endless_jumps():
    game = Game()
    sleep = game.sleep
    jump_z = []
    def creep(dt):
        before = game.position.copy()
        sleep(dt)
        if game.time >= 2.1:
            game.position = before
            if jump_z and 'space' in game.keys and 'w' in game.keys:
                game.position[2] = min(jump_z[-1] + .2, before[2] + 3 * dt)
    game.sleep = creep
    failed, attempts = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if extra.get('recovery_phase') == 'jump':
            jump_z.append(game.position[2])
        if extra.get('recovery_result') == 'failed':
            failed.append(extra['recovery_failures'])
        if state == 'blocked':
            assert '连续失败' in message
            assert extra['recovery_failures'] == 3
            game.stop_at = game.time
    assert not recovery_run(game, positions=((0, 0, 8),), jump=True, attempts=3, report=report)
    assert attempts == [1, 2, 3] and failed == [1, 2, 3]
    assert game.time < 30 and not game.keys and game.closed


def test_pause_during_jump_is_counted_once_if_the_obstacle_is_not_cleared():
    game = Game()
    sleep = game.sleep
    state = {'calibrating': True, 'paused': False}
    def frozen(dt):
        game.speed = 3 if state['calibrating'] else 0
        sleep(dt)
    game.sleep = frozen
    failures = []
    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        if extra.get('recovery_phase') == 'jump' and not state['paused']:
            state['paused'] = True
            game.pause_at, game.resume_at = game.time + .2, game.time + .7
        if extra.get('recovery_result') == 'failed':
            failures.append(extra['recovery_failures'])
        if phase == 'blocked':
            assert extra['recovery_failures'] == 2
            game.stop_at = game.time
    assert not recovery_run(game, jump=True, attempts=2, report=report)
    assert failures == [1, 2] and len(space_events(game)) == 4
    assert not game.keys and game.closed
