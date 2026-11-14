"""Sub-reasons for navigation waits.

``reason`` keeps driving recovery; ``code`` only separates a throttled retry
from a fresh search failure so repeated waits are not counted as new attempts.
"""
import pytest

from utils.core.coordinate_navigation import LocalNavigator, NavigationWait


def box(left=-2., right=2., low=2., high=3., height=(-.5, 2.)):
    return {'polygon': [[left, low], [right, low], [right, high], [left, high]],
            'height': list(height)}


def scene(*boxes):
    return {'state': 'ready', 'objects': [], 'colliders': list(boxes)}


def test_wait_defaults_to_no_sub_reason():
    assert NavigationWait('x').code is None
    assert NavigationWait('x').reason == 'no_path'
    assert NavigationWait('x', code='retry_wait').code == 'retry_wait'


def test_throttled_retry_reports_retry_wait_not_a_new_search():
    nav = LocalNavigator(lambda: scene(), water_areas=())
    target = (0., 0., 8.)
    nav.next_target((0., 0., 0.), target)
    nav.path = []
    nav.retry_at = nav.clock() + 30.
    with pytest.raises(NavigationWait) as caught:
        nav.next_target((0., 0., 0.), target)
    assert caught.value.reason == 'no_path'
    assert caught.value.code == 'retry_wait'


def test_unreachable_target_reports_search_empty():
    walls = scene(box(-3., 3., -.5, .5), box(-3., 3., 7.5, 8.5),
                  box(-3.5, -2.5, -.5, 8.5), box(2.5, 3.5, -.5, 8.5))
    nav = LocalNavigator(lambda: walls, water_areas=())
    with pytest.raises(NavigationWait) as caught:
        nav.next_target((0., 0., 0.), (0., 0., 8.))
    assert caught.value.reason == 'no_path'
    assert caught.value.code == 'search_empty'


def test_repeated_failed_direction_reports_repeat_entry():
    nav = LocalNavigator(lambda: scene(), water_areas=())
    target = (0., 0., 8.)
    nav.next_target((0., 0., 0.), target)
    nav.risks.repeats_failed_entry = lambda *args: True
    with pytest.raises(NavigationWait) as caught:
        nav.next_target((0., 0., 0.), target)
    assert caught.value.code == 'repeat_entry'
    assert nav.retry_at > nav.clock()


def test_every_search_records_a_request_id_and_attempts():
    walls = scene(box(-3., 3., -.5, .5), box(-3., 3., 7.5, 8.5),
                  box(-3.5, -2.5, -.5, 8.5), box(2.5, 3.5, -.5, 8.5))
    nav = LocalNavigator(lambda: walls, water_areas=())
    with pytest.raises(NavigationWait):
        nav.next_target((0., 0., 0.), (0., 0., 8.))
    diagnostics = nav.search_diagnostics
    assert diagnostics['request_id'] == nav.search_request_id >= 1
    assert diagnostics['attempts'] >= 1
    assert 'partial_results' in diagnostics and 'escalated' in diagnostics
