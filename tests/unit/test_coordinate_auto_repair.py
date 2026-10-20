"""Repair is exercised with a simulated game; tests never send desktop input."""
from unittest.mock import Mock

import pytest

from utils.core.coordinate_points import append_point, empty_points
from utils.core.tool_durability import axe_pick_status
from utils.flows.coordinate_player import drive_points
from tests.unit.test_coordinate_player import Game


class RepairGame(Game):
    def __init__(self, current=110, effect=True):
        super().__init__()
        self.current, self.effect = current, effect
        self.repair_at, self.before = None, current
        self.tool_reads, self.use_times = 0, []
        self.unavailable, self.held = False, True
        self.drop_on_collect = False

    def tools_snapshot(self):
        self.tool_reads += 1
        if self.unavailable:
            raise ValueError('unavailable')
        if self.repair_at is not None and self.effect:
            self.current = min(1100, self.before+int(min(17., self.time-self.repair_at))*33)
        return axe_pick_status(self.current, 1100, True, self.held)

    def execute(self, event):
        super().execute(event)
        if event == {'type': 'key_down', 'key': 'f'} and self.drop_on_collect:
            self.current, self.repair_at = 110, None


class FakeBox:
    def __init__(self, game):
        self.game = game
        self.prepare_calls = self.close_calls = 0

    def prepare(self, window, tap, wait, check):
        self.prepare_calls += 1
        check()
        return {'x': 1, 'y': 1}

    def use(self, target, device, check, wait):
        check()
        self.game.before = self.game.current
        self.game.repair_at = self.game.time
        self.game.use_times.append(self.game.time)

    def close_menu(self, window, tap, check, wait):
        check()
        self.close_calls += 1


def run(game, count=1, wait=0., box=None, loops=1):
    stamp = '2026-10-15T22:00:00+08:00'
    points = empty_points()
    for n in range(count):
        points = append_point(points, {'position': [0., 0., float(2+n*2)], 'sample_at': stamp}, stamp)
    statuses = []
    result = drive_points(points, game.size(), lambda: game, game, game, game,
                          lambda state, message, **extra: statuses.append((game.time, state, message, extra)),
                          clock=game, collect_wait_seconds=wait, loop_count=loops,
                          repair_box=box or FakeBox(game))
    return result, statuses


def test_waits_seventeen_seconds_without_walking_or_collecting_then_resumes_same_point():
    game = RepairGame()
    result, statuses = run(game)
    assert result and len(game.use_times) == 1
    assert not [(t, e) for t, e in game.events if t < 17. and e['type'] == 'key_down']
    assert game.current == 671 and game.closed and not game.keys
    assert any(s == 'repairing' and extra.get('repair_phase') == 'waiting' for _, s, _, extra in statuses)
    assert any('维修完成' in text for _, _, text, _ in statuses)
    assert [e['key'] for _, e in game.events if e['type'] == 'key_down'].count('f') == 1


def test_exactly_twenty_percent_does_not_consume_a_box():
    game = RepairGame(current=220)
    assert run(game)[0]
    assert not game.use_times


def test_a_later_low_episode_can_repair_again_without_skipping_or_repeating_collection():
    game = RepairGame(current=890)
    game.drop_on_collect = True
    assert run(game, count=2, wait=2.)[0]
    assert len(game.use_times) == 2
    assert all(b-a >= 17. for a, b in zip(game.use_times, game.use_times[1:]))
    assert [e['key'] for _, e in game.events if e['type'] == 'key_down'].count('f') == 2


def test_f6_pause_during_effect_keeps_wall_clock_deadline_and_sends_no_second_use():
    game = RepairGame()
    game.pause_at, game.resume_at = 5., 25.
    assert run(game)[0]
    assert len(game.use_times) == 1
    assert min(t for t, e in game.events if e['type'] == 'key_down') >= 25.


def test_f9_stops_during_repair_and_releases_every_key():
    game = RepairGame()
    game.stop_at = 2.
    assert not run(game)[0]
    assert game.time <= 2.05 and len(game.use_times) == 1
    assert not game.keys and game.closed


@pytest.mark.parametrize('fault', ['no_effect', 'unavailable', 'not_held'])
def test_missing_box_data_or_wrong_held_tool_pauses_instead_of_repeated_consumption(fault):
    game = RepairGame(effect=fault != 'no_effect')
    game.unavailable, game.held = fault == 'unavailable', fault != 'not_held'
    game.stop_at = 20.
    result, statuses = run(game)
    assert not result and not game.keys
    assert len(game.use_times) == (1 if fault == 'no_effect' else 0)
    assert any(s == 'repair_paused' for _, s, _, _ in statuses)
    assert not [e for _, e in game.events if e['type'] == 'key_down']


def test_high_durability_unheld_tool_does_not_block_normal_playback():
    game = RepairGame(current=890)
    game.held = False
    assert run(game)[0]
    assert not game.use_times


def test_unknown_icon_is_reported_and_never_consumes_a_box():
    game = RepairGame()
    game.stop_at = 1.
    box = FakeBox(game)
    box.prepare = Mock(side_effect=ValueError('icon missing'))
    result, statuses = run(game, box=box)
    assert not result and not game.use_times
    assert any('icon missing' in text for _, _, text, _ in statuses)


def test_pause_after_recovery_before_menu_close_retries_close_before_any_movement():
    game = RepairGame()
    box = FakeBox(game)
    original = box.close_menu

    def close_once_paused(window, tap, check, wait):
        if box.close_calls == 0:
            box.close_calls += 1
            game.pause_at, game.resume_at = game.time, game.time+1.
        original(window, tap, check, wait)

    box.close_menu = close_once_paused
    assert run(game, box=box)[0]
    assert len(game.use_times) == 1 and box.close_calls >= 2
    assert min(t for t, e in game.events if e['type'] == 'key_down') >= 18.
