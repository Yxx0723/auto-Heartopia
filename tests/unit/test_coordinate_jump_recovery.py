"""Jump-first obstacle recovery with fake coordinates, keys and clock only."""
import pytest

from tests.unit.test_coordinate_player import Game, WallGame, recovery_run


class JumpWallGame(WallGame):
    def __init__(self):
        super().__init__()
        self.space_started = None
        self.cleared = False

    def sleep(self, dt):
        if 'space' in self.keys:
            if self.space_started is None:
                self.space_started = self.time
            if self.time - self.space_started >= .6:
                self.cleared = True
                self.walls = ()
        super().sleep(dt)


def space_events(game):
    return [(at, event['type']) for at, event in game.events if event['key'] == 'space']


def test_long_jump_clears_low_obstacle_before_any_backward_or_sideways_probe():
    game = JumpWallGame()
    start, phases = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            start.append(len(game.events))
        if extra.get('recovery_phase'):
            phases.append(extra['recovery_phase'])
    assert recovery_run(game, jump=True, report=report)
    assert game.cleared
    assert 'jump' in phases and 'detour' not in phases
    assert not [event for _, event in game.events[start[0]:]
                if event['type'] == 'key_down' and event['key'] in 'sad']
    jumps = space_events(game)
    assert [event for _, event in jumps] == ['key_down', 'key_up']
    assert jumps[1][0] - jumps[0][0] == pytest.approx(1., abs=.01)
    assert not game.keys and game.closed


def test_each_failed_jump_is_released_before_alternating_left_and_right():
    game = WallGame(right_only=True)
    phases = []
    def report(state, message, **extra):
        phase = extra.get('recovery_phase')
        if phase:
            phases.append((phase, extra['recovery_attempt']))
        if phase == 'detour':
            assert not game.keys
    assert recovery_run(game, jump=True, report=report)
    assert phases == [('start', 1), ('jump', 1), ('detour', 1), ('start', 2), ('jump', 2), ('detour', 2)]
    jumps = space_events(game)
    assert [event for _, event in jumps] == ['key_down', 'key_up'] * 2
    assert all(jumps[i + 1][0] - jumps[i][0] == pytest.approx(1., abs=.01) for i in (0, 2))
    assert not game.keys


@pytest.mark.parametrize('interrupt', ['stop', 'pause', 'focus', 'position', 'input'])
@pytest.mark.parametrize('jump_attempt', [1, 2])
def test_interrupt_while_space_is_held_releases_all_keys_without_starting_detour(interrupt, jump_attempt):
    game = WallGame(right_only=True)
    phases = []
    def report(state, message, **extra):
        if extra.get('recovery_phase'):
            phases.append(extra['recovery_phase'])
        if extra.get('recovery_phase') != 'jump' or extra['recovery_attempt'] != jump_attempt:
            return
        if interrupt == 'stop':
            game.stop_at = game.time + .2
        elif interrupt == 'pause':
            game.pause_at = game.time + .2
            game.stop_at = game.time + .6
        elif interrupt == 'position':
            game.invalid_at = game.time + .2
        elif interrupt == 'focus':
            def lose_focus(event):
                if event == {'type': 'key_down', 'key': 'space'}:
                    game.focus = False
            game.on_input = lose_focus
            game.stop_at = game.time + .3
        else:
            def fail(event):
                if event == {'type': 'key_down', 'key': 'space'}:
                    raise OSError('jump input failed')
            game.on_input = fail
    if interrupt in ('position', 'input'):
        with pytest.raises((ValueError, OSError), match='lost position|jump input failed'):
            recovery_run(game, jump=True, report=report)
    else:
        assert not recovery_run(game, jump=True, report=report)
    assert phases[-1] == 'jump' and phases.count('jump') == jump_attempt
    assert not game.keys and game.closed


def test_vertical_jump_without_planar_progress_still_falls_back_to_sideways_probes():
    game = WallGame()
    sleep = game.sleep
    def jumping(dt):
        sleep(dt)
        game.position[1] = min(.8, game.position[1] + 2 * dt) if 'space' in game.keys \
            else max(0., game.position[1] - 2 * dt)
    game.sleep = jumping
    phases = []
    assert recovery_run(game, jump=True,
                        report=lambda state, message, **extra: phases.append(extra.get('recovery_phase')))
    assert phases.index('jump') < phases.index('detour')
    assert game.position[1] == 0 and not game.keys


def test_reaching_point_during_long_jump_stops_forward_input_without_overshooting():
    game = JumpWallGame()
    phases = []
    assert recovery_run(game, positions=((0, 0, 3),), jump=True,
                        report=lambda state, message, **extra: phases.append(extra.get('recovery_phase')))
    assert abs(game.position[2] - 3) <= .3
    assert 'jump' in phases and 'detour' not in phases
    assert not game.keys


@pytest.mark.parametrize('offset', [.15, 1.15])
def test_distance_limit_is_checked_during_jump_and_post_release_observation(monkeypatch, offset):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_RECOVERY_MAX_RADIUS', .3)
    game = WallGame()
    sleep, jump_at, phases = game.sleep, [], []
    def drift(dt):
        sleep(dt)
        if jump_at and game.time >= jump_at[0] + offset:
            game.position[0] = .5
    game.sleep = drift
    def report(state, message, **extra):
        phase = extra.get('recovery_phase')
        if phase:
            phases.append(phase)
        if phase == 'jump':
            jump_at.append(game.time)
        if state == 'blocked':
            assert '限制' in message and not game.keys
            game.stop_at = game.time
    assert not recovery_run(game, jump=True, report=report)
    assert phases == ['start', 'jump']
    assert not game.keys and game.closed


def test_zero_attempts_disables_jump_and_detour():
    game = WallGame()
    phases = []
    def report(state, message, **extra):
        if extra.get('recovery_phase'):
            phases.append(extra['recovery_phase'])
        if state == 'blocked':
            game.stop_at = game.time
    assert not recovery_run(game, jump=True, attempts=0, report=report)
    assert not phases and not space_events(game) and not game.keys


@pytest.mark.parametrize('count', [2, 5])
def test_jump_and_sideways_probes_share_the_selected_attempt_budget(count):
    game = Game()
    sleep = game.sleep
    def frozen(dt):
        if game.time >= 2.1:
            game.speed = 0
        sleep(dt)
    game.sleep = frozen
    attempts = []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if state == 'blocked':
            game.stop_at = game.time
    assert not recovery_run(game, jump=True, attempts=count, report=report)
    assert attempts == list(range(1, count + 1))
    jumps = space_events(game)
    assert [event for _, event in jumps] == ['key_down', 'key_up'] * count
    assert all(jumps[i + 1][0] - jumps[i][0] == pytest.approx(1., abs=.01) for i in range(0, len(jumps), 2))
    assert not game.keys


class RetryJumpGame(Game):
    """Wide barriers each require two separate held jumps to cross."""
    def __init__(self, walls):
        super().__init__()
        self.walls = list(walls)
        self.jump_at, self.wall_jumps = None, 0
        self.cleared = []

    def execute(self, event):
        super().execute(event)
        if event['key'] == 'space':
            if event['type'] == 'key_down':
                self.jump_at, self.wall_jumps = self.time, self.wall_jumps + 1
            else:
                self.jump_at = None

    def sleep(self, dt):
        if self.walls and self.jump_at is not None and self.wall_jumps >= 2 \
                and self.time - self.jump_at >= .6:
            self.cleared.append(self.walls.pop(0))
            self.wall_jumps = 0
        before = self.position.copy()
        super().sleep(dt)
        for wall in self.walls:
            if self.position[2] >= wall and before[2] < wall + 1:
                self.position[2] = before[2]


@pytest.mark.parametrize('walls', [(2.,), (2., 5.)])
def test_failed_first_jump_retries_and_crosses_every_obstacle_before_same_waypoint(walls):
    game = RetryJumpGame(walls)
    phases, blocked = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase'):
            phases.append((extra['recovery_phase'], extra['recovery_attempt']))
        if state == 'blocked':
            blocked.append(message)
            game.stop_at = game.time
    assert recovery_run(game, positions=((0, 0, 8),), attempts=5, jump=True, report=report)
    assert game.cleared == list(walls) and not blocked
    attempts = [number for phase, number in phases if phase == 'start']
    jumps = [number for phase, number in phases if phase == 'jump']
    assert attempts == jumps == [1, 2] * len(walls)
    assert not game.keys and game.closed
