"""Use the stove's default selection; never require a coffee/manual selection."""
import pytest

from utils.core.cooking import CookingSession, Observation


def inventory():
    return {'scope': 'bag', 'counts': {10: 40, 20: 4},
            'selectable_counts': {10: 40}, 'usable_counts': {10: 40},
            'items': [{'net_id': 7, 'item_id': 10, 'count': 40, 'star': 0,
                       'locked': False, 'disabled': False, 'prepared': False},
                      {'net_id': 8, 'item_id': 20, 'count': 4, 'star': 1,
                       'locked': False, 'disabled': False, 'prepared': True}]}


def view(identity=55):
    return {'scope': 'cooker', 'signature': (identity, 7, 7),
            'selected': {'recipe_id': identity, 'name': '默认料理',
                         'slots': [{'filled': True, 'net_id': 7, 'item_id': 10}]*2}}


def test_default_job_opens_stove_once_without_a_recipe_picker():
    s = CookingSession(None, 1, default_mode=True)
    assert s.update(Observation('interact'), inventory(), 0) == 'interact'
    assert s.update(Observation('interact'), inventory(), 1) is None
    assert s.started == 0
    assert s.update(Observation('menu', recipe_view=view()), inventory(), 2) == 'start'
    assert s.recipe.name == '默认料理' and s.recipe.ingredients == {10: 2}


def test_default_receipt_accepts_actual_prepared_variant_not_assumed_coffee_id():
    s = CookingSession(None, 1, default_mode=True)
    bag = inventory()
    s.update(Observation('menu', recipe_view=view()), bag, 0)
    s.update(Observation('cooking'), bag, 1)
    assert s.update(Observation('done'), bag, 2) == 'collect'
    bag = inventory(); bag['counts'][99] = 1
    bag['items'].append({'item_id': 99, 'prepared': True, 'count': 1})
    s.update(Observation('interact'), bag, 3)
    assert s.state == 'finished' and s.confirmed == 1
    assert s.pot['inventory_delta'] == {99: 1}


def test_raw_material_or_unrelated_type_increase_is_not_cooking_success():
    s = CookingSession(None, 1, default_mode=True)
    bag = inventory(); s.update(Observation('menu', recipe_view=view()), bag, 0)
    s.update(Observation('cooking'), bag, 1); s.update(Observation('done'), bag, 2)
    bag = inventory(); bag['counts'][10] += 5
    s.update(Observation('interact'), bag, 3)
    assert s.confirmed == 0


@pytest.mark.parametrize('fault', ['locked', 'star', 'foreign_net', 'empty', 'insufficient'])
def test_automatic_default_filling_must_be_owned_usable_and_sufficient(fault):
    s = CookingSession(None, 1, default_mode=True)
    bag, selection = inventory(), view()
    if fault == 'locked': bag['items'][0]['locked'] = True
    if fault == 'star': bag['items'][0]['star'] = 5
    if fault == 'foreign_net': selection['selected']['slots'][0] = dict(filled=True, net_id=999, item_id=10)
    if fault == 'empty': selection['selected']['slots'][0] = dict(filled=False, net_id=0, item_id=0)
    if fault == 'insufficient': bag['items'][0]['count'] = 1
    assert s.update(Observation('menu', recipe_view=selection), bag, 0) is None
    assert s.started == 0


def test_changing_recipe_between_pots_is_not_silently_accepted():
    s = CookingSession(None, 2, default_mode=True)
    bag = inventory(); s.update(Observation('menu', recipe_view=view()), bag, 0)
    s.update(Observation('cooking'), bag, 1); s.update(Observation('done'), bag, 2)
    bag = inventory(); bag['counts'][20] += 1
    s.update(Observation('interact'), bag, 3)
    s.update(Observation('menu', recipe_view=view(56)), bag, 4)
    assert s.state == 'invalid' and s.started == 1
