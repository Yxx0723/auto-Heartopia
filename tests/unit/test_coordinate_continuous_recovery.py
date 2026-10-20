"""Automatic retry stays on the same point and remains manually interruptible."""
import pytest

from tests.unit.test_coordinate_player import Game, WallGame, manual_points
from utils.flows.coordinate_player import drive_points


def run(game, report, attempts=2, jump=False):
    return drive_points(manual_points([(0, 0, 6), (0, 0, 8)]), game.size(), lambda: game,
                        game, game, game, report, clock=game, recovery_attempts=attempts,
                        jump_on_recovery=jump, collect_wait_seconds=0., continuous_recovery=True)


@pytest.mark.parametrize('jump', [False, True])
def test_failed_round_retries_same_point_then_collects_all_points(jump):
    game = Game()
    state = {'calibrating': False, 'attempts': 0}
    sleep = game.sleep

    def stuck_until_third_attempt(dt):
        game.speed = 3 if state['calibrating'] or state['attempts'] >= 3 else 0
        sleep(dt)

    game.sleep = stuck_until_third_attempt
    reached, retries = [], []

    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        if extra.get('recovery_phase') == 'start':
            state['attempts'] += 1
        if extra.get('recovery_result') == 'auto_retry':
            retries.append(extra['waypoint'])
        assert phase != 'blocked', message
        if phase == 'reached':
            reached.append(extra['waypoint'])

    assert run(game, report, jump=jump)
    assert retries == [1] and state['attempts'] == 3 and reached == [1, 2]
    assert sum(event == {'type': 'key_down', 'key': 'f'} for _, event in game.events) == 2
    assert not game.keys and game.closed


@pytest.mark.parametrize('jump', [False, True])
def test_distance_boundary_replans_locally_instead_of_pausing(monkeypatch, jump):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_RECOVERY_MAX_RADIUS', .4)
    game = Game()
    state = {'calibrating': False, 'clear': False}
    sleep = game.sleep

    def blocked_until_boundary_retry(dt):
        game.speed = 3 if state['calibrating'] or state['clear'] else 0
        sleep(dt)

    game.sleep = blocked_until_boundary_retry
    retries, reached = [], []

    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        assert phase != 'blocked', message
        if jump and extra.get('recovery_phase') == 'jump':
            state['clear'] = True
        if extra.get('recovery_result') == 'distance_retry':
            retries.append((extra['waypoint'], game.read()[2]))
            state['clear'] = True
        if phase == 'reached':
            reached.append(extra['waypoint'])

    assert run(game, report, jump=jump)
    assert len(retries) == 1 and retries[0][0] == 1 and reached == [1, 2]
    if jump:
        assert retries[0][1] > .4  # The live sample crossed the local boundary.
    assert not game.keys and game.closed


@pytest.mark.parametrize('interrupt', ['stop', 'focus', 'invalid'])
def test_automatic_retry_wait_remains_interruptible(interrupt):
    game = Game()
    state = {'calibrating': False}
    sleep = game.sleep

    def frozen(dt):
        game.speed = 3 if state['calibrating'] else 0
        sleep(dt)

    game.sleep = frozen
    retries = []

    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        if extra.get('recovery_result') == 'auto_retry':
            retries.append(extra['waypoint'])
            if interrupt == 'stop':
                game.stop_at = game.time + .1
            elif interrupt == 'focus':
                game.focus = False
                game.stop_at = game.time + .2
            else:
                game.invalid_at = game.time + .1

    if interrupt == 'invalid':
        with pytest.raises(ValueError, match='lost position'):
            run(game, report, attempts=1)
    else:
        assert not run(game, report, attempts=1)
    assert retries == [1]
    assert not any(event['key'] == 'f' for _, event in game.events)
    assert not game.keys and game.closed


class SlopeWallGame(WallGame):
    def read(self):
        x, y, z = super().read()
        return (x, -z, z)


@pytest.mark.parametrize('jump', [False, True])
def test_downhill_obstacle_does_not_pause_on_cumulative_height_change(jump):
    game = SlopeWallGame()
    blocked, reached, recovery_heights = [], [], []

    def report(phase, message, **extra):
        if extra.get('recovery_phase') == 'start':
            recovery_heights.append(game.read()[1])
        if phase == 'blocked':
            blocked.append(message)
            game.stop_at = game.time
        if phase == 'reached':
            reached.append((extra['waypoint'], game.read()))

    data = manual_points([(0, -6, 6), (0, -8, 8)])
    assert drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, recovery_attempts=2, jump_on_recovery=jump,
                        collect_wait_seconds=0., continuous_recovery=True), blocked
    assert recovery_heights and not blocked and [number for number, position in reached] == [1, 2]
    assert reached[0][1][1] < recovery_heights[0] - 1.
    assert sum(event == {'type': 'key_down', 'key': 'f'} for _, event in game.events) == 2
    assert not game.keys and game.closed


@pytest.mark.parametrize('change', [(0, -50, 0), (50, 0, 0), (0, float('nan'), 0)])
def test_continuous_recovery_still_rejects_coordinate_discontinuities(change):
    game = Game()
    read = game.read

    def discontinuous_read():
        position = read()
        return tuple(a + b for a, b in zip(position, change)) if game.time >= 2.5 else position

    game.read = discontinuous_read
    with pytest.raises(ValueError, match='跳变|无效'):
        run(game, lambda *a, **k: None, jump=True)
    assert not any(event['key'] == 'f' for _, event in game.events)
    assert not game.keys and game.closed
