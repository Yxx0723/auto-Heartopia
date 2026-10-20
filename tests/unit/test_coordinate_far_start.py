"""Long approaches must not treat their original straight line as a local fence."""
import math

import pytest

from tests.unit.test_coordinate_player import Game, recovery_run


class FarApproachWallGame(Game):
    def __init__(self, length):
        super().__init__()
        self.position = [-108., 25., 76.]
        self.origin = self.position.copy()
        self.wall_x = self.origin[0] + length / 5

    def sleep(self, dt):
        before = self.position.copy()
        super().sleep(dt)
        # A small obstacle on the actual keyboard-driven approach. It can be
        # bypassed on either side, but jumping alone does not clear it.
        if before[0] < self.wall_x <= self.position[0] and abs(self.position[2] - self.origin[2]) < .8:
            self.position = before


@pytest.mark.parametrize('length', [10., 100.])
@pytest.mark.parametrize('jump', [False, True])
def test_far_start_bypasses_local_obstacle_and_keeps_saved_point_order(length, jump):
    game = FarApproachWallGame(length)
    positions = [(game.origin[0] + length, 25., game.origin[2] + length * .3),
                 (game.origin[0] + length + 3., 25., game.origin[2] + length * .3)]
    blocked, reached, recovery_starts = [], [], []

    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            recovery_starts.append(game.read())
        if state == 'reached':
            reached.append((extra['waypoint'], game.read()))
        if state == 'blocked':
            blocked.append(message)
            game.stop_at = game.time

    assert recovery_run(game, positions=positions, attempts=5, jump=jump, report=report), blocked
    assert recovery_starts and not blocked
    assert [number for number, position in reached] == [1, 2]
    assert all(math.dist(actual, expected) <= .3 for (_, actual), expected in zip(reached, positions))
    if length == 100.:
        # The quantized WASD approach is more than four units away from the
        # original origin-to-target line. Its local obstacle remains reachable.
        position = recovery_starts[0]
        dx, dz = positions[0][0] - game.origin[0], positions[0][2] - game.origin[2]
        deviation = abs(dx * (position[2] - game.origin[2]) - dz * (position[0] - game.origin[0])) / math.hypot(dx, dz)
        assert deviation > 4.
    assert not game.keys and game.closed
