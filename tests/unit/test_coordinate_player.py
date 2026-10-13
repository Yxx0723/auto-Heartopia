"""A moving simulated character verifies the feedback loop without live input."""
import math

import pytest

from utils.flows.coordinate_player import drive_points, drive_route
from utils.core.coordinate_points import append_point, empty_points


class Game:
    def __init__(self, angle=0, speed=3):
        self.time, self.position = 0., [0., 0., 0.]
        self.keys, self.events = set(), []
        self.angle, self.speed = angle, speed
        self.closed, self.focus, self.running = False, True, True
        self.stop_at, self.pause_at, self.resume_at = math.inf, math.inf, math.inf
        self.invalid_at, self.rotate_at = math.inf, math.inf
        self.on_input = None

    def monotonic(self):
        return self.time

    def sleep(self, dt):
        dx = int('d' in self.keys) - int('a' in self.keys)
        dz = int('w' in self.keys) - int('s' in self.keys)
        length = math.hypot(dx, dz)
        if length:
            angle = self.angle + (math.pi if self.time >= self.rotate_at else 0)
            self.position[0] += (dx * math.cos(angle) - dz * math.sin(angle)) / length * self.speed * dt
            self.position[2] += (dx * math.sin(angle) + dz * math.cos(angle)) / length * self.speed * dt
        self.time += dt
        assert self.time < 200, 'loop did not terminate'

    def read(self):
        if self.time >= self.invalid_at:
            raise ValueError('lost position')
        return tuple(self.position)

    def foreground(self):
        return self.focus

    def size(self):
        return [1280, 720]

    def snapshot(self):
        return self.running and not self.pause_at <= self.time < self.resume_at, self.time >= self.stop_at

    def pause(self):
        self.running = False

    def execute(self, event):
        if self.on_input:
            self.on_input(event)
        self.events.append((self.time, event.copy()))
        (self.keys.add if event['type'] == 'key_down' else self.keys.discard)(event['key'])

    def release_all(self):
        for key in tuple(self.keys):
            self.execute({'type': 'key_up', 'key': key})

    def close(self):
        self.closed = True


def route():
    positions = [(0, 0, 0), (0, 0, 4), (4, 0, 4), (4, 0, 0)]
    return {'engine': 'coordinate_route', 'coordinate_version': 1,
            'name': 'test', 'author': 'test', 'target_title': '心动小镇',
            'manual_start': True, 'client_size': [1280, 720], 'duration': 6.,
            'samples': [{'at': float(i * 2), 'position': list(p)} for i, p in enumerate(positions)]}


def run(game, data=None):
    return drive_route(data or route(), lambda: game, game, game, game,
                       lambda *args, **kwargs: None, clock=game)


@pytest.mark.parametrize('angle', [0, math.pi / 3, -math.pi / 2])
def test_closed_loop_reaches_corners_with_rotated_camera(angle):
    game = Game(angle)
    assert run(game)
    assert math.hypot(game.position[0] - 4, game.position[2]) <= .3
    assert not game.keys and game.closed


def test_start_far_away_never_sends_input():
    game = Game()
    game.position[0] = 10
    with pytest.raises(ValueError, match='起点'):
        run(game)
    assert not game.events and game.closed


@pytest.mark.parametrize('stop_at', [.1, 2.5])
def test_stop_during_calibration_or_following_releases_keys(stop_at):
    game = Game()
    game.stop_at = stop_at
    assert not run(game)
    assert game.time <= stop_at + .051
    assert not game.keys and game.closed


def test_paused_time_is_excluded_and_resume_recalibrates():
    game = Game()
    game.pause_at, game.resume_at = 2.4, 6.
    assert run(game)
    assert not [e for t, e in game.events if 2.46 < t < 6 and e['type'] == 'key_down']
    assert not game.keys and game.closed


def test_missing_position_releases_keys():
    game = Game()
    game.invalid_at = .15
    with pytest.raises(ValueError, match='lost position'):
        run(game)
    assert not game.keys and game.closed


def test_calibration_rejects_stationary_coordinates():
    game = Game(speed=0)
    with pytest.raises(ValueError, match='校准'):
        run(game)
    assert not game.keys and game.closed


def test_180_degree_camera_change_stops_instead_of_running_away():
    game = Game()
    game.rotate_at = 2.5
    with pytest.raises(ValueError, match='方向|进展'):
        run(game)
    assert not game.keys and game.closed


def test_focus_loss_between_key_transitions_prevents_next_key_down():
    game = Game()
    def lose_focus(event):
        if event['type'] == 'key_down':
            game.focus = False
    game.on_input, game.stop_at = lose_focus, .4
    assert not run(game)
    assert len([e for _, e in game.events if e['type'] == 'key_down']) == 1
    assert not game.keys and game.closed


def test_input_exception_always_attempts_release():
    game = Game()
    def fail(event):
        if event['type'] == 'key_down' and event['key'] == 's':
            raise OSError('input failed')
    game.on_input = fail
    with pytest.raises(OSError, match='input failed'):
        run(game)
    assert not game.keys and game.closed


def test_no_progress_after_calibration_stops_with_keys_released():
    game = Game()
    original_sleep = game.sleep
    def sleep(dt):
        if game.time >= 2.1:
            game.speed = 0
        original_sleep(dt)
    game.sleep = sleep
    with pytest.raises(ValueError, match='前进'):
        run(game)
    assert game.time < 5 and not game.keys and game.closed


def test_recorded_idle_time_is_preserved():
    game = Game()
    data = route()
    data['samples'].insert(2, {'at': 3., 'position': [0, 0, 4]})
    data['samples'][3]['at'], data['samples'][4]['at'], data['duration'] = 5., 7., 7.
    assert run(game, data)
    ups = [t for t, e in game.events if t > 2 and e == {'type': 'key_up', 'key': 'w'}]
    downs = [t for t, e in game.events if t > 2 and e == {'type': 'key_down', 'key': 'd'}]
    assert downs[0] - ups[0] >= 1 - 1e-6


def test_stop_before_start_does_not_open_process_or_send_input():
    game = Game()
    game.running, game.stop_at = False, .1
    def reader():
        pytest.fail('opened game before F6')
    assert not drive_route(route(), reader, game, game, game, lambda *a, **k: None, clock=game)
    assert not game.events


def test_pause_during_calibration_requires_a_new_calibration():
    game = Game()
    game.pause_at, game.resume_at = .15, 1.
    assert run(game)
    assert not game.keys and game.closed


def test_invalid_route_never_sends_input():
    game = Game()
    data = route()
    data['samples'][1]['position'][1] = 1000
    with pytest.raises(ValueError):
        run(game, data)
    assert not game.events


def manual_points(positions):
    data = empty_points()
    stamp = '2026-10-11T23:30:00+08:00'
    for position in positions:
        data = append_point(data, {'position': list(position), 'sample_at': stamp}, stamp)
    return data


def play_points(game, data, report=lambda *args, **kwargs: None):
    return drive_points(data, game.size(), lambda: game, game, game, game, report, clock=game)


@pytest.mark.parametrize('angle', [0, math.pi / 3, -math.pi / 2])
def test_saved_points_are_reached_in_order_from_current_position(angle):
    game = Game(angle)
    positions = [(0, 0, 6), (4, 0, 6), (4, 0, 0), (0, 0, 6)]
    reached = []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append((extra['waypoint'], game.read()))
    assert play_points(game, manual_points(positions), report)
    assert [number for number, position in reached] == [1, 2, 3, 4]
    for (_, actual), expected in zip(reached, positions):
        assert math.dist(actual, expected) <= .3
    assert not game.keys and game.closed


def test_single_saved_point_is_supported_and_duplicates_are_not_removed():
    game = Game()
    reached = []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert play_points(game, manual_points([(0, 0, 0), (0, 0, 0)]), report)
    assert reached == [1, 2]
    assert play_points(Game(), manual_points([(0, 0, 2)]))


def test_sloping_ground_can_reach_saved_points_at_different_heights():
    game = Game()
    read = game.read
    game.read = lambda: (read()[0], read()[2] * .5, read()[2])
    assert play_points(game, manual_points([(0, 3, 6), (0, 1, 2)]))
    assert not game.keys


def test_same_planar_point_on_wrong_floor_is_rejected():
    game = Game()
    with pytest.raises(ValueError, match='高度|楼层'):
        play_points(game, manual_points([(0, 4, 0)]))
    assert not game.keys and game.closed


@pytest.mark.parametrize('stop_at', [.1, 2.5])
def test_saved_points_stop_releases_keys(stop_at):
    game = Game()
    game.stop_at = stop_at
    assert not play_points(game, manual_points([(0, 0, 8)]))
    assert game.time <= stop_at + .051
    assert not game.keys and game.closed


def test_saved_points_pause_during_calibration_and_resume_recalibrates():
    game = Game()
    game.pause_at, game.resume_at = .15, 1.
    assert play_points(game, manual_points([(0, 0, 5)]))
    assert not game.keys and game.closed


def test_saved_points_loss_of_coordinates_and_stuck_character_release_keys():
    game = Game()
    game.invalid_at = 2.5
    with pytest.raises(ValueError, match='lost position'):
        play_points(game, manual_points([(0, 0, 8)]))
    assert not game.keys and game.closed
    game = Game()
    sleep = game.sleep
    def stuck(dt):
        if game.time >= 2.1:
            game.speed = 0
        sleep(dt)
    game.sleep = stuck
    game.stop_at = 5.
    reports = []
    assert not play_points(game, manual_points([(0, 0, 8)]),
                           lambda state, message, **extra: reports.append(state))
    assert 'blocked' in reports and not game.running
    assert game.time <= 5.01 and not game.keys and game.closed


def test_manual_point_playback_has_a_bounded_total_duration(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_POINT_PLAYBACK_SECONDS', 3.)
    game = Game()
    with pytest.raises(ValueError, match='时限'):
        play_points(game, manual_points([(0, 0, 100)]))
    assert game.time < 3.1 and not game.keys and game.closed


def test_invalid_or_empty_points_do_not_open_reader_or_send_keys():
    game = Game()
    def reader():
        pytest.fail('opened reader for invalid points')
    with pytest.raises(ValueError):
        drive_points(empty_points(), game.size(), reader, game, game, game, lambda *a, **k: None, clock=game)
    assert not game.events


@pytest.mark.parametrize('angle', [0, math.pi / 3, -math.pi / 2])
def test_nearest_start_joins_route_then_follows_remaining_saved_order(angle):
    game = Game(angle)
    game.position = [7., 0., 2.]
    reached = []
    report = lambda state, message, **extra: reached.append(extra['waypoint']) if state == 'reached' else None
    data = manual_points([(0, 0, 0), (4, 0, 0), (8, 0, 0), (12, 0, 0)])
    assert drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, start_mode='nearest')
    assert reached == [3, 4]
    assert not game.keys and game.closed


def test_nearest_start_uses_position_after_f6_instead_of_when_button_was_clicked():
    game = Game()
    game.snapshot = lambda: (game.time >= .5, False)
    reached = []
    def reader():
        game.position = [7., 0., 0.]
        return game
    report = lambda state, message, **extra: reached.append(extra['waypoint']) if state == 'reached' else None
    data = manual_points([(0, 0, 0), (4, 0, 0), (8, 0, 0), (12, 0, 0)])
    assert drive_points(data, game.size(), reader, game, game, game, report,
                        clock=game, start_mode='nearest')
    assert reached == [3, 4]


def test_nearest_start_is_not_reselected_after_pause():
    game = Game()
    game.position = [7., 0., 2.]
    game.pause_at, game.resume_at = .15, 1.
    selected, reached = [], []
    def report(state, message, **extra):
        if state == 'calibrating':
            selected.append(extra['start_waypoint'])
        if state == 'reached':
            reached.append(extra['waypoint'])
    data = manual_points([(0, 0, 0), (4, 0, 0), (8, 0, 0), (12, 0, 0)])
    assert drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, start_mode='nearest')
    assert selected == [3, 3]
    assert reached == [3, 4]


def test_resume_progress_reports_current_target_and_keeps_original_join_point():
    game = Game()
    game.position = [7., 0., 2.]
    calibrations = []
    def report(state, message, **extra):
        if state == 'calibrating':
            calibrations.append((extra['start_waypoint'], extra['waypoint']))
        if state == 'reached' and extra['waypoint'] == 3:
            game.pause_at, game.resume_at = game.time + .05, game.time + .5
    data = manual_points([(0, 0, 0), (4, 0, 0), (8, 0, 0), (12, 0, 0)])
    assert drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, start_mode='nearest')
    assert calibrations == [(3, 3), (3, 4)]


class WallGame(Game):
    def __init__(self, right_only=False, walls=(2.,)):
        super().__init__()
        self.right_only, self.walls = right_only, walls

    def sleep(self, dt):
        before = self.position.copy()
        super().sleep(dt)
        for z in self.walls:
            blocked = self.position[0] < .8 if self.right_only else abs(self.position[0]) < .8
            if blocked and self.position[2] >= z and before[2] < z + 1:
                self.position[2] = before[2]


def recovery_run(game, positions=((0, 0, 5),), attempts=3, jump=False, report=None):
    return drive_points(manual_points(positions), game.size(), lambda: game, game, game, game,
                        report or (lambda *a, **k: None), clock=game,
                        recovery_attempts=attempts, jump_on_recovery=jump)


@pytest.mark.parametrize('right_only,expected', [(False, [1]), (True, [1, 2])])
def test_backs_up_and_alternates_sides_without_skipping_saved_points(right_only, expected):
    game = WallGame(right_only)
    attempts, reached = [], []
    def report(state, message, **extra):
        if state == 'recovering' and extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
            assert not game.keys
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert recovery_run(game, positions=((0, 0, 5), (0, 0, 6)), report=report)
    assert attempts == expected and reached == [1, 2]
    assert not any(e['key'] == 'space' for _, e in game.events)
    assert not game.keys and game.closed


@pytest.mark.parametrize('count', [0, 1, 4])
def test_exact_attempt_budget_then_pause_with_no_keys_held(count):
    game = Game()
    sleep = game.sleep
    def frozen(dt):
        if game.time >= 2.1:
            game.speed = 0
        sleep(dt)
    game.sleep, game.stop_at = frozen, 30.
    attempts, blocked = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if state == 'blocked':
            blocked.append(extra)
            assert not game.keys and not game.running
            game.stop_at = game.time + .2
    assert not recovery_run(game, attempts=count, report=report)
    assert attempts == list(range(1, count + 1))
    assert len(blocked) == 1 and blocked[0]['waypoint'] == 1
    assert not game.keys and game.closed


def test_next_point_starts_with_a_fresh_recovery_episode():
    game = WallGame(walls=(2., 7.))
    attempts = []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append((extra['waypoint'], extra['recovery_attempt']))
    assert recovery_run(game, positions=((0, 0, 5), (0, 0, 10)), attempts=1, report=report)
    assert attempts == [(1, 1), (2, 1)]


def test_manual_detour_after_exhaustion_resumes_the_same_target_from_new_position():
    game = WallGame(right_only=True)
    reached, blocked = [], []
    def report(state, message, **extra):
        if state == 'blocked':
            blocked.append(extra['waypoint'])
            assert not game.keys
            # User manually detours and presses F6, far from the paused position.
            game.position, game.running = [2., 0., 4.], True
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert recovery_run(game, attempts=1, positions=((0, 0, 5), (0, 0, 6)), report=report)
    assert blocked == [1] and reached == [1, 2]
    assert not game.keys and game.closed


@pytest.mark.parametrize('interrupt', ['stop', 'focus', 'position', 'input'])
def test_interrupt_during_recovery_always_releases_keys(interrupt):
    game = WallGame()
    def report(state, message, **extra):
        if extra.get('recovery_phase') != 'start':
            return
        if interrupt == 'stop':
            game.stop_at = game.time + .1
        elif interrupt == 'focus':
            game.on_input = lambda event: setattr(game, 'focus', False)
            game.stop_at = game.time + .3
        elif interrupt == 'position':
            game.invalid_at = game.time + .1
        else:
            def fail(event):
                if event['type'] == 'key_down':
                    raise OSError('recovery input failed')
            game.on_input = fail
    if interrupt in ('input', 'position'):
        with pytest.raises((ValueError, OSError), match='lost position|recovery input failed'):
            recovery_run(game, report=report)
    else:
        assert not recovery_run(game, report=report)
    assert not game.keys and game.closed


def test_jump_is_only_sent_when_explicitly_enabled():
    game = WallGame()
    assert recovery_run(game, jump=True)
    jumps = [(t, e) for t, e in game.events if e['key'] == 'space']
    assert [e['type'] for _, e in jumps] == ['key_down', 'key_up']
    assert not game.keys


@pytest.mark.parametrize('attempts,jump', [(-1, False), (11, False), (True, False), (2, 1)])
def test_invalid_recovery_settings_never_open_reader_or_send_keys(attempts, jump):
    game = Game()
    def reader():
        pytest.fail('reader opened for invalid recovery options')
    with pytest.raises(ValueError):
        drive_points(manual_points([(0, 0, 8)]), game.size(), reader, game, game, game,
                     lambda *a, **k: None, clock=game,
                     recovery_attempts=attempts, jump_on_recovery=jump)
    assert not game.events


def test_pressing_f6_after_exhaustion_does_not_reset_the_attempt_count():
    game = Game()
    state = {'phase': '', 'blocked': 0}
    attempts = []
    sleep = game.sleep
    def controlled_sleep(dt):
        game.speed = 3 if state['phase'] == 'calibrating' else 0
        sleep(dt)
    game.sleep = controlled_sleep
    def report(phase, message, **extra):
        state['phase'] = phase
        if extra.get('recovery_phase') == 'start':
            attempts.append(extra['recovery_attempt'])
        if phase == 'blocked':
            state['blocked'] += 1
            if state['blocked'] == 1:
                game.running = True  # F6, without manually clearing the obstacle.
            else:
                game.stop_at = game.time
    assert not recovery_run(game, attempts=1, report=report)
    assert state['blocked'] == 2 and attempts == [1]
    assert not game.keys and game.closed


def test_recovery_radius_limit_pauses_before_sending_a_detour_key(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_RECOVERY_MAX_RADIUS', .2)
    game = WallGame()
    attempts, blocked = [], []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'start':
            attempts.append(len(game.events))
        if state == 'blocked':
            blocked.append(message)
            game.stop_at = game.time
    assert not recovery_run(game, report=report)
    assert len(attempts) == 1 and len(blocked) == 1 and '限制' in blocked[0]
    assert not [e for _, e in game.events[attempts[0]:] if e['type'] == 'key_down']
    assert not game.keys and game.closed


def collection_run(game, positions=((0, 0, 0), (0, 0, 3)), default_wait=3., overrides=None,
                   report=lambda *a, **k: None, **options):
    data = manual_points(positions)
    for index, seconds in (overrides or {}).items():
        data['points'][index]['collect_wait'] = seconds
    return drive_points(data, game.size(), lambda: game, game, game, game, report, clock=game,
                        collect_wait_seconds=default_wait, **options)


def test_every_point_stops_then_presses_f_once_and_waits_including_the_final_point():
    game = Game()
    captured, finished = [], []
    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            assert not game.keys
            captured.append(game.read())
    game.on_input = on_input
    def report(state, message, **extra):
        if state == 'reached':
            finished.append((extra['waypoint'], game.time))
    assert collection_run(game, report=report)
    downs = [t for t, e in game.events if e == {'type': 'key_down', 'key': 'f'}]
    ups = [t for t, e in game.events if e == {'type': 'key_up', 'key': 'f'}]
    assert len(downs) == len(ups) == len(captured) == 2
    assert [i for i, t in finished] == [1, 2]
    assert all(end - up >= 3 - 1e-6 for (_, end), up in zip(finished, ups))
    for position, target in zip(captured, [(0, 0, 0), (0, 0, 3)]):
        assert math.dist(position, target) <= .3
    assert not [e for t, e in game.events if downs[0] < t < finished[0][1]
                and e['type'] == 'key_down' and e['key'] in 'wasd']
    assert not game.keys and game.closed


def test_per_point_wait_overrides_default_and_zero_wait_still_collects_duplicates():
    game = Game()
    finished = []
    def report(state, message, **extra):
        if state == 'reached':
            finished.append(game.time)
    assert collection_run(game, positions=((0, 0, 0),) * 3, default_wait=1.5,
                          overrides={0: 0., 2: .5}, report=report)
    ups = [t for t, e in game.events if e == {'type': 'key_up', 'key': 'f'}]
    assert len(ups) == 3
    assert [end - up for end, up in zip(finished, ups)] == pytest.approx([0, 1.5, .5], abs=1e-6)


@pytest.mark.parametrize('pause_offset', [.04, .3])
def test_pause_during_f_or_wait_keeps_progress_without_repressing_f_or_recalibrating(pause_offset):
    game = Game()
    f_time = []
    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            f_time.append(game.time)
            game.pause_at, game.resume_at = game.time + pause_offset, game.time + pause_offset + 2
    game.on_input = on_input
    assert collection_run(game, positions=((0, 0, 0),), default_wait=1.)
    assert len(f_time) == 1
    assert game.time >= f_time[0] + 3 - 1e-6
    assert not [e for t, e in game.events if t > f_time[0] and e['type'] == 'key_down' and e['key'] in 'wasd']
    assert not game.keys and game.closed


@pytest.mark.parametrize('stop_offset', [.04, .3])
def test_f9_during_collection_or_wait_releases_f_and_stops_before_next_point(stop_offset):
    game = Game()
    reached = []
    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            game.stop_at = game.time + stop_offset
    game.on_input = on_input
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert not collection_run(game, report=report)
    assert game.time <= game.stop_at + .011 and reached == []
    assert not game.keys and game.closed


@pytest.mark.parametrize('fault', ['focus', 'position', 'input'])
def test_collection_interruptions_always_release_all_keys(fault):
    game = Game()
    def on_input(event):
        if event != {'type': 'key_down', 'key': 'f'}:
            return
        if fault == 'focus':
            game.focus, game.stop_at = False, game.time + .3
        elif fault == 'position':
            game.invalid_at = game.time + .2
        else:
            raise OSError('F input failed')
    game.on_input = on_input
    if fault == 'focus':
        assert not collection_run(game)
    else:
        with pytest.raises((ValueError, OSError), match='lost position|F input failed'):
            collection_run(game)
    assert not game.keys and game.closed


def test_nearest_mode_collects_only_the_points_visited_in_saved_order():
    game = Game()
    game.position = [7., 0., 0.]
    reached = []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert collection_run(game, positions=((0, 0, 0), (4, 0, 0), (8, 0, 0), (12, 0, 0)),
                          default_wait=0, report=report, start_mode='nearest')
    assert reached == [3, 4]
    assert len([e for _, e in game.events if e == {'type': 'key_down', 'key': 'f'}]) == 2


@pytest.mark.parametrize('seconds', [-1, 301, True, float('nan'), float('inf'), '3'])
def test_invalid_collection_default_never_opens_reader_or_sends_input(seconds):
    game = Game()
    def reader():
        pytest.fail('opened reader for invalid collection settings')
    with pytest.raises(ValueError):
        drive_points(manual_points([(0, 0, 0)]), game.size(), reader, game, game, game,
                     lambda *a, **k: None, clock=game, collect_wait_seconds=seconds)
    assert not game.events


def test_configured_collection_wait_is_added_to_the_bounded_manual_run_budget(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_POINT_PLAYBACK_SECONDS', 3.)
    assert collection_run(Game(), positions=((0, 0, 0),), default_wait=8.)


def test_drift_during_the_final_wait_slice_cannot_complete_a_point():
    game = Game()
    drift_at, reached = [math.inf], []
    sleep = game.sleep
    def drift(dt):
        if game.time >= drift_at[0]:
            game.position[0] += 18 * dt
        sleep(dt)
    game.sleep = drift
    def on_input(event):
        if event == {'type': 'key_up', 'key': 'f'}:
            drift_at[0] = game.time + .095
    game.on_input = on_input
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
    with pytest.raises(ValueError, match='离开|高度'):
        collection_run(game, positions=((0, 0, 0),), default_wait=.125, report=report)
    assert reached == [] and not game.keys and game.closed
