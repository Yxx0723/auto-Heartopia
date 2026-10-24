"""Single-stove decisions must survive delayed UI, pause and inventory updates."""
import pytest

from utils.core.cooking import CookingSession, Recipe, Observation


def stock(dish=0, raw=10, scope='player-bag'):
    return {'scope': scope, 'counts': {100: dish, 200: raw}}


def session(target=2):
    return CookingSession(Recipe('test dish', (100,), {200: 2}, {200: 2}), target)


def test_start_is_not_repeated_while_menu_feedback_is_delayed():
    s = session()
    assert s.update(Observation('menu'), stock(), 0) == 'start'
    assert s.update(Observation('menu'), stock(), .5) is None
    assert s.update(Observation('cooking'), stock(), 1) is None
    assert s.started == 1 and s.confirmed == 0


def test_stir_is_once_per_visible_prompt_and_rearmed_only_after_clear():
    s = session(); s.update(Observation('menu'), stock(), 0)
    assert s.update(Observation('stir'), stock(), 1) == 'stir'
    assert s.update(Observation('stir'), stock(), 1.1) is None
    assert s.update(Observation('unknown'), stock(), 1.2) is None
    assert s.update(Observation('stir'), stock(), 1.3) is None
    s.update(Observation('cooking'), stock(), 2)
    assert s.update(Observation('stir'), stock(), 3) == 'stir'


def test_collect_once_then_delay_until_actual_inventory_gain():
    s = session(); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('cooking'), stock(), 1)
    assert s.update(Observation('done'), stock(), 2) == 'collect'
    assert s.update(Observation('done'), stock(), 3) is None
    assert s.update(Observation('menu'), None, 4) is None
    assert s.confirmed == 0
    assert s.update(Observation('menu'), stock(1), 5) is None
    assert s.confirmed == 1
    assert s.update(Observation('menu'), stock(1), 6) == 'start'
    assert s.started == 2


def test_foreign_scope_never_confirms_and_stops_new_input():
    s = session(); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('done'), stock(), 1)
    assert s.update(Observation('menu'), stock(1, scope='other'), 2) is None
    assert s.state == 'invalid' and s.confirmed == 0


def test_missing_initial_inventory_never_starts_or_creates_post_input_baseline():
    s = session()
    assert s.update(Observation('menu'), None, 0) is None
    assert s.started == 0
    assert s.update(Observation('menu'), stock(), 1) == 'start'


def test_reserves_are_checked_before_each_pot():
    s = session()
    assert s.update(Observation('menu'), stock(raw=3), 0) is None
    assert s.state == 'insufficient' and s.started == 0


def test_timeout_is_unknown_and_never_retries_material_consuming_action():
    s = session(); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('menu'), stock(), 1000)
    assert s.state == 'unconfirmed' and s.started == 1
    assert s.update(Observation('menu'), stock(1), 1001) is None


def test_already_cooking_cannot_be_adopted_without_a_pre_start_baseline():
    s = session()
    assert s.update(Observation('cooking'), stock(), 0) is None
    assert s.started == 0


@pytest.mark.parametrize('count', [0, -1, True, 1.5, 10001])
def test_invalid_goal_is_rejected(count):
    with pytest.raises(ValueError):
        CookingSession(Recipe('dish', (100,), {200: 1}, {}), count)


def test_target_count_is_successful_pots_not_clicks_or_units_gained():
    s = session(1); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('cooking'), stock(), 1)
    s.update(Observation('done'), stock(), 2)
    s.update(Observation('menu'), stock(3), 3)
    assert s.confirmed == 1 and s.state == 'finished'
    assert s.update(Observation('menu'), stock(3), 4) is None


def test_next_pot_reopens_menu_once_only_after_receipt_confirmation():
    s = session(2); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('cooking'), stock(), 1)
    s.update(Observation('done'), stock(), 2)
    assert s.update(Observation('interact'), stock(1), 3) is None
    assert s.confirmed == 1
    assert s.update(Observation('interact'), stock(1), 4) == 'interact'
    assert s.update(Observation('interact'), stock(1), 5) is None
    assert s.update(Observation('menu'), stock(1), 6) == 'start'


def test_unresponsive_reopen_is_bounded_and_does_not_repeat_q():
    s = session(2); s.update(Observation('menu'), stock(), 0)
    s.update(Observation('cooking'), stock(), 1)
    s.update(Observation('done'), stock(), 2)
    s.update(Observation('interact'), stock(1), 3)
    s.update(Observation('interact'), stock(1), 4)
    assert s.update(Observation('interact'), stock(1), 30) is None
    assert s.state == 'unconfirmed' and s.started == 1
