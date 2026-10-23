from unittest.mock import Mock
import pytest
from tests.unit.test_food_search import search_game


def test_raw_mushrooms_are_not_fruit_fallback():
    menu, window, device, state = search_game(available=('平菇',))
    with pytest.raises(ValueError):
        menu.prepare(window, Mock(), Mock(), Mock(), device,
                     inventory=[{'name': '平菇', 'edible': True, 'count': 5}])
    assert not state['queries'] and not state['eaten']
