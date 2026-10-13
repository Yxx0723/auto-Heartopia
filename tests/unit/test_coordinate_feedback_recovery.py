"""Frame timing and collision feedback regressions, with no real game input."""
import math
from unittest.mock import patch

import pytest

from tests.unit.test_coordinate_player import Game, recovery_run
from utils.core.coordinate_route import rotate_basis
from utils.flows.coordinate_player import _Follower


def follower(game):
    return _Follower({'duration': 10., 'client_size': game.size()}, game, game, game, game,
                     lambda *a, **k: None, game)


def test_forced_read_between_game_frames_accepts_normal_frame_displacement():
    game = Game()
    player = follower(game)
    player.read(force=True)
    game.time += .001
    game.position[2] += .2  # A game frame publishes movement all at once.
    assert player.read(force=True) == (0., 0., .2)


def test_read_latency_counts_toward_the_motion_measurement_interval():
    game = Game()
    player = follower(game)
    player.read(force=True)
    game.time += .001
    game.keys.add('w')
    read = game.read
    def delayed_read():
        game.sleep(.2)
        return read()
    game.read = delayed_read
    assert player.read(force=True)[2] == pytest.approx(.6)
    assert player.position_at == pytest.approx(game.time)


def test_rapid_forced_reads_cannot_reset_the_window_to_hide_excessive_motion():
    game = Game()
    player = follower(game)
    player.read(force=True)
    for _ in range(2):
        game.time += .001
        game.position[2] += .4
        player.read(force=True)
    game.time += .001
    game.position[2] += .4
    with pytest.raises(ValueError, match='跳变'):
        player.read(force=True)


@pytest.mark.parametrize('position', [(50., 0., 0.), (0., 50., 0.), (math.nan, 0., 0.)])
def test_teleports_and_invalid_values_still_fail(position):
    game = Game()
    player = follower(game)
    player.read(force=True)
    game.position = list(position)
    with pytest.raises(ValueError, match='跳变|无效'):
        player.read(force=True)


def test_sampling_window_is_reset_when_resuming_after_manual_movement():
    game = Game()
    player = follower(game)
    player.read(force=True)
    game.position[2] = 50.
    player.position_at = None
    assert player.read(force=True)[2] == 50.
    game.time += .001
    game.position[2] += .2
    assert player.read(force=True)[2] == 50.2


class SlidingWallGame(Game):
    """Collision projects W sideways, then sticks, until a jump clears it."""
    def __init__(self):
        super().__init__()
        self.sliding_since, self.cleared = None, False

    def sleep(self, dt):
        before = self.position.copy()
        super().sleep(dt)
        if self.cleared or self.time < 2.1 or before[2] < 1.5:
            return
        if 'space' in self.keys:
            self.cleared = True
        elif 'w' in self.keys:
            if self.sliding_since is None:
                self.sliding_since = self.time
            self.position = before
            if self.time - self.sliding_since < .8:
                self.position[0] += 3 * dt


def test_wall_slide_does_not_abort_before_jump_recovery_or_change_the_basis():
    game = SlidingWallGame()
    phases = []
    def report(state, message, **extra):
        if extra.get('recovery_phase'):
            phases.append(extra['recovery_phase'])
        if state == 'blocked':
            game.stop_at = game.time
    with patch('utils.flows.coordinate_player.rotate_basis', wraps=rotate_basis) as rotate:
        assert recovery_run(game, positions=((0, 0, 6),), attempts=2, jump=True, report=report)
        rotate.assert_not_called()
    assert 'jump' in phases and game.cleared
    assert math.hypot(game.position[0], game.position[2] - 6) <= .3
    assert not game.keys and game.closed


def test_real_jump_in_coordinates_during_playback_releases_every_key():
    game = Game()
    read = game.read
    def teleport():
        if game.time >= 2.5:
            game.position[2] += 50
        return read()
    game.read = teleport
    with pytest.raises(ValueError, match='跳变'):
        recovery_run(game)
    assert not game.keys and game.closed


def test_frame_quantized_coordinates_complete_calibration_and_point_playback():
    game = Game()
    game.read = lambda: tuple(round(v / .15) * .15 for v in game.position)
    assert recovery_run(game, positions=((0, 0, 6),), attempts=2, jump=True)
    assert math.hypot(game.position[0], game.position[2] - 6) <= .4
    assert not game.keys and game.closed


def test_camera_reversal_during_manual_playback_pauses_bounded_recovery_without_collecting():
    game = Game()
    game.rotate_at = 2.5
    blocked = []
    def report(state, message, **extra):
        if state == 'blocked':
            blocked.append(message)
            game.stop_at = game.time
    assert not recovery_run(game, positions=((0, 0, 6),), attempts=2, jump=True, report=report)
    assert len(blocked) == 1 and '暂停' in blocked[0]
    assert game.time < 30 and not any(e['key'] == 'f' for _, e in game.events)
    assert not game.keys and game.closed


@pytest.mark.parametrize('interrupt', ['stop', 'focus', 'invalid'])
def test_sliding_wall_jump_remains_interruptible(interrupt):
    game = SlidingWallGame()
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'jump':
            if interrupt == 'stop':
                game.stop_at = game.time + .2
            elif interrupt == 'focus':
                game.focus = False
                game.stop_at = game.time + .2
            else:
                game.invalid_at = game.time + .2
    if interrupt == 'invalid':
        with pytest.raises(ValueError, match='lost position'):
            recovery_run(game, positions=((0, 0, 6),), attempts=2, jump=True, report=report)
    else:
        assert not recovery_run(game, positions=((0, 0, 6),), attempts=2, jump=True, report=report)
    assert not game.keys and game.closed
