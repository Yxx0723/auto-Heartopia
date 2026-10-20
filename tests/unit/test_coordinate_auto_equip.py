from unittest.mock import Mock

import pytest

from utils.core.coordinate_points import append_point, empty_points
from utils.flows.coordinate_player import drive_points
from tests.unit.test_coordinate_auto_repair import RepairGame, FakeBox


class Selector:
    def __init__(self, game, success=True):
        self.game, self.success = game, success
        self.clicks, self.closes, self.prepares = 0, 0, 0

    def prepare(self, window, tap, wait, check):
        self.prepares += 1
        check()
        return {'x': 1, 'y': 1}

    def use(self, target, device, check, wait):
        check()
        self.clicks += 1
        if self.success:
            self.game.held = not self.game.held  # Repeated clicks would put it away.
        wait(.1)

    def close_menu(self, window, tap, check, wait):
        check()
        self.closes += 1


def run(game, selector=None, repair=False, count=1, collect_wait=0.):
    selector = selector or Selector(game)
    stamp = '2026-10-16T01:00:00+08:00'
    points = empty_points()
    for n in range(count):
        points = append_point(points, {'position': [0., 0., float(2+n*2)], 'sample_at': stamp}, stamp)
    statuses = []
    result = drive_points(points, game.size(), lambda: game, game, game, game,
                          lambda state, message, **extra: statuses.append((game.time, state, message, extra)),
                          clock=game, collect_wait_seconds=collect_wait, tool_selector=selector,
                          repair_box=FakeBox(game) if repair else None)
    return result, selector, statuses


def test_unheld_tool_is_taken_out_before_calibration_and_collection_even_without_auto_repair():
    game = RepairGame(current=890)
    game.held = False
    result, selector, statuses = run(game)
    assert result and game.held and selector.clicks == 1 and selector.closes == 1
    assert [e['key'] for _, e in game.events if e['type'] == 'key_down'].count('f') == 1
    assert any(state == 'equipping' for _, state, _, _ in statuses)
    assert not game.keys and game.closed


def test_already_held_tool_is_never_clicked_or_put_away():
    game = RepairGame(current=890)
    result, selector, _ = run(game)
    assert result and game.held and selector.clicks == selector.prepares == 0


def test_collection_rechecks_fresh_hold_state_instead_of_one_second_old_cache():
    game = RepairGame(current=890)
    read, execute = game.read, game.execute
    dropped = [False]
    def position():
        value = read()
        if game.position[2] >= 1.75 and not dropped[0]:
            game.held, dropped[0] = False, True
        return value
    def guarded_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            assert game.held
        execute(event)
    game.read, game.execute = position, guarded_input
    result, selector, _ = run(game)
    assert result and dropped[0] and selector.clicks == 1


def test_manual_equip_during_menu_open_is_rechecked_before_click():
    game = RepairGame(current=890)
    game.held = False
    selector = Selector(game)
    original = selector.prepare
    def prepare(*args):
        target = original(*args)
        game.held = True
        return target
    selector.prepare = prepare
    assert run(game, selector)[0]
    assert selector.clicks == 0 and game.held


def test_pause_during_selection_verifies_existing_click_and_resumes_without_toggling_again():
    game = RepairGame(current=890)
    game.held = False
    game.pause_at, game.resume_at = .17, 3.
    result, selector, _ = run(game)
    assert result and selector.clicks == 1 and game.held
    assert min(t for t, e in game.events if e['type'] == 'key_down') >= 3.


def test_f9_during_selection_stops_and_releases_all_keys():
    game = RepairGame(current=890)
    game.held = False
    game.stop_at = .17
    result, selector, _ = run(game)
    assert not result and selector.clicks == 1 and not game.keys and game.closed


@pytest.mark.parametrize('fault', ['unknown_icon', 'no_effect', 'unknown_state', 'locked'])
def test_selection_failure_never_moves_or_spams_clicks(fault):
    game = RepairGame(current=890)
    game.held, game.stop_at = False, 5.
    selector = Selector(game, success=fault != 'no_effect')
    if fault == 'unknown_icon':
        selector.prepare = Mock(side_effect=ValueError('icon missing'))
    elif fault == 'unknown_state':
        game.unavailable = True
    elif fault == 'locked':
        game.tools_snapshot = lambda: {'state': 'locked', 'name': '斧镐'}
    result, selector, statuses = run(game, selector)
    assert not result and selector.clicks == (1 if fault == 'no_effect' else 0)
    assert not [e for _, e in game.events if e['type'] == 'key_down']
    assert any(state == 'equip_paused' for _, state, _, _ in statuses)


def test_low_tool_is_equipped_before_repair_box_is_used():
    game = RepairGame()
    game.held = False
    result, selector, _ = run(game, repair=True)
    assert result and selector.clicks == 1 and len(game.use_times) == 1 and game.held


def test_tool_put_away_after_collection_is_reequipped_without_recollecting_or_skipping_points():
    game = RepairGame(current=890)
    original = game.execute
    def execute(event):
        original(event)
        if event == {'type': 'key_down', 'key': 'f'}:
            game.held = False
    game.execute = execute
    result, selector, _ = run(game, count=2, collect_wait=2.)
    assert result and selector.clicks == 2 and game.held
    assert [e['key'] for _, e in game.events if e['type'] == 'key_down'].count('f') == 2
