import pytest

from tests.unit.test_coordinate_auto_repair import RepairGame, FakeBox
from tests.unit.test_coordinate_auto_equip import Selector
from utils.core.coordinate_points import empty_points, append_point
from utils.core.stamina import stamina_status
from utils.flows.coordinate_player import drive_points


class HungryGame(RepairGame):
    def __init__(self, energy=16, restore=10):
        super().__init__(current=890)
        self.energy, self.eat_at, self.before_energy = energy, None, energy
        self.food_effect, self.energy_unavailable = True, False
        self.food_restore = restore

    def stamina_snapshot(self):
        if self.energy_unavailable:
            raise ValueError('energy unavailable')
        if self.eat_at is not None and self.food_effect and self.time >= self.eat_at+.4:
            self.energy = min(100, self.before_energy+self.food_restore)
            self.eat_at = None
        return stamina_status(self.energy, 100, 100)


class Fruit:
    def __init__(self, game):
        self.game, self.uses, self.closes = game, [], 0

    def prepare(self, window, tap, wait, check, device):
        check()
        assert not self.game.keys
        return {'x': 1, 'y': 1, 'food': '苹果'}

    def use(self, target, device, check, wait):
        check()
        self.uses.append(self.game.time)
        self.game.before_energy, self.game.eat_at = self.game.energy, self.game.time
        wait(.1)

    def close_menu(self, window, tap, check, wait, device):
        check()
        self.closes += 1


def test_overflow_stamina_does_not_pause_or_consume_more_fruit():
    game = HungryGame(109)
    result, food, statuses = run(game)
    assert result and game.energy == 109 and not food.uses
    assert not any(state == 'food_paused' for state, _, _ in statuses)


def run(game, food=None, count=1, repair=False):
    food = food or Fruit(game)
    data = empty_points()
    stamp = '2026-10-16T17:00:00+08:00'
    for i in range(count):
        data = append_point(data, {'position': [0., 0., 2.+i*2], 'sample_at': stamp}, stamp)
    statuses = []
    result = drive_points(data, game.size(), lambda: game, game, game, game,
                          lambda state, message, **extra: statuses.append((state, message, extra)),
                          clock=game, collect_wait_seconds=1., food=food,
                          tool_selector=Selector(game), repair_box=FakeBox(game) if repair else None)
    return result, food, statuses


def test_eats_one_at_a_time_until_full_then_continues_same_route_and_collection():
    game = HungryGame(16)
    result, food, statuses = run(game, count=2)
    assert result and game.energy == 100 and len(food.uses) == 9 and food.closes == 1
    assert food.uses[1] > food.uses[0]+.4
    assert any(s == 'eating' for s, _, _ in statuses)
    assert sum(e['type'] == 'key_down' and e['key'] == 'f' for _, e in game.events) == 2
    assert not game.keys and game.closed


@pytest.mark.parametrize('energy', [30, 31, 100])
def test_thirty_and_above_never_eats(energy):
    result, food, _ = run(HungryGame(energy))
    assert result and not food.uses


def test_pause_during_food_use_rechecks_effect_instead_of_eating_twice():
    game = HungryGame(26)
    game.pause_at, game.resume_at = .05, 3.
    result, food, _ = run(game)
    assert result and len(food.uses) == 8 and game.energy == 100
    assert food.uses[1] >= 3.  # The interrupted first use is verified before another.


def test_stop_during_food_use_releases_keys_and_does_not_move():
    game = HungryGame(16)
    game.stop_at = .05
    result, food, _ = run(game)
    assert not result and len(food.uses) == 1 and game.position == [0., 0., 0.]
    assert not game.keys


@pytest.mark.parametrize('fault', ['no_effect', 'unavailable', 'no_fruit', 'close_failed'])
def test_food_failure_retries_without_pausing_or_moving_and_stop_still_works(fault):
    game, food = HungryGame(26), None
    game.stop_at = 12.
    food = Fruit(game)
    if fault == 'no_effect': game.food_effect = False
    if fault == 'unavailable': game.energy_unavailable = True
    def fail(*args): raise ValueError('no suitable fruit')
    if fault == 'no_fruit': food.prepare = fail
    if fault == 'close_failed':
        food.close_menu = fail
        game.food_restore = 100
    result, food, statuses = run(game, food)
    assert not result and not game.keys
    if fault == 'no_effect':
        assert len(food.uses) == 2 and food.uses[1]-food.uses[0] >= 8.
    else:
        assert len(food.uses) <= 1
    assert game.position == [0., 0., 0.]
    assert not any(s == 'food_paused' for s, _, _ in statuses)
    assert any(s == 'eating' and '自动重试' in text for s, text, _ in statuses)


def test_low_durability_and_hunger_are_both_serviced_before_movement():
    game = HungryGame(26)
    game.current, game.before = 110, 110
    game.held = False
    result, food, _ = run(game, repair=True)
    assert result and len(food.uses) == 8 and len(game.use_times) == 1
    assert game.energy == 100 and game.held
    assert food.uses[0] < game.use_times[0]


def test_manual_recovery_while_opening_backpack_is_rechecked_before_consumption():
    game, food = HungryGame(16), None
    food = Fruit(game)
    original = food.prepare
    def prepare(*args):
        target = original(*args)
        game.energy = 100
        return target
    food.prepare = prepare
    result, food, _ = run(game, food)
    assert result and not food.uses and food.closes == 1


def test_one_transient_read_is_retried_without_pausing_or_consuming_food():
    game = HungryGame(40)
    original, calls = game.stamina_snapshot, [0]
    def read():
        calls[0] += 1
        if calls[0] == 1: raise ValueError('property changing')
        return original()
    game.stamina_snapshot = read
    result, food, statuses = run(game)
    assert result and not food.uses and not any(s == 'food_paused' for s, _, _ in statuses)


def test_resume_during_confirmed_eating_retains_settle_time_without_extra_food():
    game = HungryGame(26)
    game.pause_at, game.resume_at = 1., 1.2
    result, food, _ = run(game)
    assert result and len(food.uses) == 8 and game.energy == 100
    assert min(at for at, event in game.events if event['type'] == 'key_down') >= 2.4


def test_collection_uses_fresh_stamina_instead_of_the_one_second_cache():
    game = HungryGame(40)
    read, execute, dropped = game.read, game.execute, [False]
    def position():
        value = read()
        if game.position[2] >= 1.75 and not dropped[0]:
            game.energy, dropped[0] = 16, True
        return value
    def send(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            assert game.energy >= 30
        execute(event)
    game.read, game.execute = position, send
    result, food, _ = run(game)
    assert result and dropped[0] and len(food.uses) == 9 and game.energy == 100


def test_eight_unit_apple_gain_continues_past_thirty_seven_until_full():
    game = HungryGame(29, restore=8)
    result, food, _ = run(game)
    assert result and game.energy == 100 and len(food.uses) == 9
    assert food.closes == 1


@pytest.mark.parametrize('pause', [False, True])
def test_healthy_start_and_resume_close_leftover_backpack_before_movement(pause):
    game = HungryGame(37)
    food = Fruit(game)
    food.opened = True
    snapshot, execute = game.snapshot, game.execute

    def controls():
        running, stopped = snapshot()
        if not running and not stopped:
            food.opened = True
        return running, stopped

    def close(window, tap, check, wait, device):
        check()
        food.closes += 1
        closed, food.opened = food.opened, False
        return closed

    def send(event):
        if event['type'] == 'key_down':
            assert not food.opened
        execute(event)

    food.close_menu = close
    game.snapshot, game.execute = controls, send
    if pause:
        game.pause_at, game.resume_at = .8, 1.2
    result, food, _ = run(game, food)
    assert result and not food.uses and not food.opened
    assert food.closes == (2 if pause else 1)


def test_leftover_backpack_that_cannot_close_retries_before_moving_or_eating_again():
    game, food = HungryGame(37), None
    game.stop_at = 1.
    food = Fruit(game)

    def close(*args):
        raise ValueError('背包未关闭')

    food.close_menu = close
    result, food, statuses = run(game, food)
    assert not result and not food.uses and not game.keys
    assert game.position == [0., 0., 0.]
    assert any(s == 'eating' and '关闭背包未完成' in text for s, text, _ in statuses)
    assert not any(s == 'food_paused' for s, _, _ in statuses)


def test_two_transient_search_failures_recover_without_user_resume():
    game = HungryGame(29, restore=8)
    food = Fruit(game); prepare = food.prepare; calls = []
    def flaky(*args):
        calls.append(game.time)
        if len(calls) <= 2:
            raise ValueError('搜索结果未确认')
        return prepare(*args)
    food.prepare = flaky
    result, food, statuses = run(game, food)
    assert result and game.energy == 100 and len(food.uses) == 9
    assert calls[1]-calls[0] >= 1. and calls[2]-calls[1] >= 2.
    assert not any(s == 'food_paused' for s, _, _ in statuses)


def test_refill_stops_at_one_hundred_even_when_boosted_maximum_is_higher():
    game = HungryGame(29, restore=40)
    def status():
        if game.eat_at is not None and game.time >= game.eat_at+.4:
            game.energy = min(120, game.before_energy+40)
            game.eat_at = None
        return stamina_status(game.energy, 100, 120)
    game.stamina_snapshot = status
    result, food, _ = run(game)
    assert result and game.energy == 109 and len(food.uses) == 2


def test_every_meal_reads_fresh_inventory_before_search_and_stops_at_100():
    game = HungryGame(29, restore=40)
    food = Fruit(game)
    reads, searches = [], []
    def inventory(check):
        check()
        reads.append(game.time)
        return [{'name':'苹果', 'edible':True, 'count':10-len(reads)}]
    game.food_inventory_snapshot = inventory
    game.food_search_query = lambda:'苹果'
    original = food.prepare
    def prepare(*args, **kwargs):
        searches.append(kwargs['inventory'][0]['count'])
        assert reads[-1] <= game.time and kwargs['query_check']() == '苹果'
        return original(*args)
    food.prepare = prepare
    result, food, statuses = run(game, food)
    assert result and game.energy == 100 and searches == [9, 8]
    assert len(food.uses) == 2 and not any(s == 'food_paused' for s, _, _ in statuses)
