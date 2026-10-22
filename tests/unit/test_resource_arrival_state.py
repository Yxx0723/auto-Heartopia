"""Regress the recorded rare-wood arrival before the first F press."""
from copy import deepcopy
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game
from tests.unit.test_rare_wood_route import route, scene, presses
from utils.core.coordinate_navigation import LocalNavigator
from utils.core.coordinate_points import ordered_waypoints
from utils.flows.coordinate_player import _Follower, drive_points


class SampleGame(Game):
    def read(self):
        # A rejected arrival must still let the test clock progress, just like
        # real process reads, so a busy-wait regression fails instead of hangs.
        self.time += .001
        return super().read()


@pytest.mark.parametrize('offset', [(0.75805664, 1.05980682), (1.27, 0.), (1.45, .1)])
def test_final_approach_outside_interaction_range_continues_before_f(offset):
    game = SampleGame(); game.position = [offset[0], 1.05, offset[1]]; game.stop_at = 12.
    game.movement_basis = lambda: {'forward': [0., 1.], 'right': [1., 0.]}
    snap, data = scene(), route()
    original = deepcopy(data)
    nav = LocalNavigator(lambda: snap, clock=game.monotonic, water_areas=())
    assert drive_points(data, game.size(), lambda: game, game, game, game, Mock(), clock=game,
                        navigator=nav, collect_wait_seconds=.2, skip_unreachable=True)
    assert len(presses(game)) == 3 and math.hypot(game.position[0], game.position[2]) <= 1.25
    assert any(e.get('key') in ('w', 'a', 's', 'd') for _, e in game.events)
    assert data == original and not game.keys


def follower(game, point, snap):
    nav = LocalNavigator(lambda: snap, clock=game.monotonic, water_areas=())
    return _Follower({'duration': 0., 'client_size': game.size()}, game, game, game, game, Mock(), game,
                     waypoints=[point], navigator=nav, collect_wait_seconds=.2)


def test_range_lost_before_first_f_does_not_start_a_collection_dwell():
    game = Game(); game.position = [1.31, 1.05, 0.]
    point = ordered_waypoints(route())[0]; point['position'] = list(game.position)
    session = follower(game, point, scene())
    assert not session.collect_point(point, 0, 1)
    assert session.collection is None and not presses(game)


def test_menu_move_before_first_f_requires_a_new_arrival_without_poisoning_state():
    game = Game(); game.position = [1.2, 1.05, 0.]
    point = ordered_waypoints(route())[0]
    session = follower(game, point, scene())
    session.tool_selector = object(); session.read_tool = lambda: None
    def move(*args):
        game.position[0] = 1.5
        return False
    session.maintain_tool = move
    assert not session.collect_point(point, 0, 1)
    assert session.collection is None and not presses(game)


def test_actual_departure_after_f_still_releases_keys_and_reports_it():
    game = Game(); game.position = [1.2, 1.05, 0.]
    point = ordered_waypoints(route())[0]
    session = follower(game, point, scene())
    def move(e):
        if e == {'type': 'key_down', 'key': 'f'}:
            game.position[0] += .4
    game.on_input = move
    with pytest.raises(ValueError, match='离开'):
        session.collect_point(point, 0, 1)
    assert len(presses(game)) == 1 and not game.keys


def test_reapproach_between_presses_keeps_the_completed_press_count():
    game = Game(); game.position = [1.2, 1.05, 0.]
    point = ordered_waypoints(route())[0]; point['collect_wait'] = 0.
    session = follower(game, point, scene())
    assert not session.collect_point(point, 0, 1)
    assert session.collection['sent_count'] == 1 and not session.collection['sent']
    game.position[0] = 1.31
    assert not session.collect_point(point, 0, 1)
    assert session.collection['sent_count'] == 1 and len(presses(game)) == 1


def test_real_tree_eight_floor_uses_local_feet_instead_of_another_trees_height_offset():
    game = SampleGame(); game.position = [1.4, -.13584, 0.]; game.stop_at = 12.
    game.movement_basis = lambda: {'forward': [0., 1.], 'right': [1., 0.]}
    snap, data, report = scene(), route(), Mock()
    original = deepcopy(data)
    nav = LocalNavigator(lambda: snap, clock=game.monotonic, water_areas=())
    assert drive_points(data, game.size(), lambda: game, game, game, game, report, clock=game,
                        navigator=nav, collect_wait_seconds=.2, skip_unreachable=True)
    assert len(presses(game)) == 3 and data == original
    assert not any('等待落地' in c.args[1] for c in report.call_args_list)


def test_a_tree_on_another_floor_cannot_be_authorized_by_horizontal_proximity():
    game = SampleGame(); game.position = [1.4, 3.5, 0.]; game.stop_at = 3.
    game.movement_basis = lambda: {'forward': [0., 1.], 'right': [1., 0.]}
    nav = LocalNavigator(scene, clock=game.monotonic, water_areas=())
    assert not drive_points(route(), game.size(), lambda: game, game, game, game, Mock(), clock=game,
                            navigator=nav, collect_wait_seconds=.2, continuous_recovery=True, recovery_attempts=5)
    assert not presses(game)
