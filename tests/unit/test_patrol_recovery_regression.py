"""2026-10-21 fluorite interruption: a 0.055-unit ring is no detour bearing."""
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_special_collection import follower
from tests.unit.test_special_patrol_flow import data
from tests.unit.test_coordinate_player import Game
from utils.core.coordinate_navigation import LocalNavigator
from utils.core.special_approach import interaction_standpoint, in_interaction_range
from utils.core.coordinate_navigation import ObstacleMap
from utils.flows.coordinate_player import _Follower, drive_points
from utils.flows.special_patrol import drive_special_patrol

POSITION = [-131.1566619873047, 21.695714950561523, 59.250457763671875]
RESOURCE = [-130.6199951171875, 21.784000396728516, 60.3849983215332]
LEG = [-131.13311525798048, 21.695714950561523, 59.300236725092496]


def configure():
    f, game, point, target, _ = follower()
    game.position = list(POSITION)
    target['position'] = list(RESOURCE)
    point['position'] = list(LEG)
    f.navigator = LocalNavigator(lambda: {'state': 'ready', 'objects': [target], 'colliders': []},
                                 clock=game.monotonic, water_areas=())
    f.stable_navigation = True
    return f, game, point, target


def test_actual_fluorite_approach_reaches_interaction_envelope_then_collects():
    f, game, _, _ = configure()
    assert not in_interaction_range(POSITION, RESOURCE)
    f.calibrate = Mock(return_value={'forward': (0., 1.), 'right': (1., 0.)})
    assert f.follow()
    assert in_interaction_range(game.position, RESOURCE)
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 2
    assert not game.keys


def test_actual_short_leg_uses_resource_bearing_instead_of_raising():
    f, game, _, _ = configure()
    f.recovery_leg = Mock(return_value=False)
    assert f.recover(LEG, {'forward': (0., 1.), 'right': (1., 0.)}, POSITION, 0, 1) is False
    f.recovery_leg.assert_called_once()
    assert f.recovery_leg.call_args.args[-1] == RESOURCE
    assert not game.keys


def test_already_reached_ordinary_goal_replans_without_movement_or_f():
    f, game, point, _ = configure()
    point.pop('special_resource')
    f.recovery_leg = Mock(side_effect=AssertionError('No direction to guess'))
    assert f.recover(LEG, {}, POSITION, 0, 1) is False
    assert not game.events and not game.keys


def test_stale_episode_origin_does_not_define_next_probe_direction():
    f, game, point, _ = configure()
    point.pop('special_resource')
    point['position'] = [POSITION[0]+3., POSITION[1], POSITION[2]]
    f.recovery.origin = tuple(point['position'])
    f.recovery_leg = Mock(return_value=False)
    assert f.recover(point['position'], {}, POSITION, 0, 1) is False
    assert f.recovery_leg.called and not game.keys


def test_inner_clear_ring_leaves_margin_inside_interaction_boundary():
    obstacle = ObstacleMap({'colliders': []}, POSITION, water_areas=())
    aim = interaction_standpoint(POSITION, RESOURCE, obstacle)
    assert math.hypot(aim[0]-RESOURCE[0], aim[2]-RESOURCE[2]) == pytest.approx(.9)


def test_frozen_resource_approach_returns_to_patrol_without_f_or_fatal_error(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_PATROL_DWELL_SECONDS', .01)
    game, report = Game(speed=0), Mock()
    game.position = list(POSITION)
    target = {'category': 'fluorite', 'resource_static_id': 42002, 'resource_available': 3,
              'map_dynamic': True, 'position': list(RESOURCE)}
    nav = LocalNavigator(lambda: {'state': 'ready', 'global_special_state': 'ready',
                                  'objects': [target], 'colliders': []}, clock=game.monotonic, water_areas=())
    plan = data([list(POSITION)])
    plan['special_patrol'].update(category='fluorite', static_id=42002)
    plan['points'][0]['name'] = '无暇萤石'
    plan['points'][0]['special_resource'] = {k: plan['special_patrol'][k] for k in ('category','static_id','press_count')}
    monkeypatch.setattr(_Follower, 'calibrate', lambda *a, **kw: {'forward': (0.,1.), 'right': (1.,0.)})
    assert drive_special_patrol(plan, game.size(), lambda: game, game, game, game, report,
                               clock=game, navigator=nav, point_runner=drive_points,
                               recovery_attempts=5, continuous_recovery=True, stable_navigation=True)
    assert any(c.kwargs.get('patrol_result') == 'unconfirmed' for c in report.call_args_list)
    assert any(c.kwargs.get('resource_outcome') == 'not_found' for c in report.call_args_list)
    assert not any(e.get('key') == 'f' for _, e in game.events)
    assert not game.keys
