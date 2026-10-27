"""Current game guidance following, entirely fake devices and clocks."""
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game
from tests.unit.test_coordinate_navigation import scene
from utils.core.guidance_path import GuidanceCursor, validate_guidance
from utils.core.coordinate_navigation import LocalNavigator
from utils.flows.native_guidance_follow import drive_guidance


def snapshot(path=((0, .5, 0), (0, .5, 4), (4, .5, 4)), target=(8, 0, 4), now=0.):
    return {'state': 'ready', 'context': 'game-level-a', 'target': target,
            'path': path, 'captured_at': now}


def test_curve_follows_intermediate_legs_instead_of_final_target():
    cursor = GuidanceCursor(validate_guidance(snapshot(), 0.), (0, 0, 0))
    aim = cursor.aim((0, 0, 0))
    assert aim['position'][0] == 0 and 0 < aim['position'][2] < 4
    aim = cursor.aim((0, 0, 3.7))
    assert aim['position'][0] > 0 and aim['position'][2] == 4


def test_start_mid_route_does_not_walk_back_to_historical_start():
    cursor = GuidanceCursor(validate_guidance(snapshot(), 0.), (0, 0, 3))
    assert cursor.aim((0, 0, 3))['position'][2] >= 3


def test_duplicate_points_and_height_offset_do_not_become_ground_height():
    cursor = GuidanceCursor(validate_guidance(snapshot(((0, .5, 0), (0, .5, 0), (0, .5, 4))), 0.), (0, 0, 0))
    assert len(cursor.path) == 2
    assert cursor.aim((0, 0, 0))['position'][1] == 0  # steering uses current body layer


def test_bridge_above_player_and_large_off_corridor_entry_are_rejected():
    value = validate_guidance(snapshot(((0, 7, 0), (0, 7, 4))), 0.)
    with pytest.raises(ValueError):GuidanceCursor(value, (0, 0, 0))
    with pytest.raises(ValueError):GuidanceCursor(validate_guidance(snapshot(), 0.), (30, 0, 0))


def test_end_of_display_path_is_not_claimed_as_exact_target_arrival():
    cursor = GuidanceCursor(validate_guidance(snapshot(), 0.), (0, 0, 0))
    cursor.aim((0, 0, 3.9))
    result = cursor.aim((3.8, 0, 4))
    assert result['arrived'] and result['target_distance'] > 4


@pytest.mark.parametrize('mutate', [
    lambda d:d.update(captured_at=-20.), lambda d:d.update(captured_at=20.),
    lambda d:d.update(path=[]), lambda d:d.update(target=(math.inf, 0, 0)),
    lambda d:d.update(path=[(0, 0, 0), (0, math.nan, 1)]),
    lambda d:d.update(context=''), lambda d:d.update(state='inactive'),
])
def test_invalid_or_stale_guidance_cannot_generate_movement(mutate):
    value = snapshot();mutate(value)
    with pytest.raises(ValueError):validate_guidance(value, 0.)


def guide_game():
    game = Game()
    game.movement_basis = lambda: {'forward': [0., 1.], 'right': [1., 0.]}
    game.guidance = lambda: snapshot(now=game.time)
    return game


def run(game, report=None, navigator=None):
    nav = navigator or LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=())
    return drive_guidance(game.size(), lambda: game, game, game, game,
                          report or (lambda *a, **k: None), clock=game, navigator=nav)


def test_follows_curve_stops_near_end_and_never_interacts():
    game = guide_game()
    outcome = run(game)
    assert outcome['arrived'] and math.hypot(game.position[0]-4, game.position[2]-4) < 1.
    assert game.position[2] > 3.5
    assert set(e['key'] for _,e in game.events) <= {'w', 'a', 's', 'd', 'space'}
    assert not game.keys and game.closed


def test_reader_is_not_opened_before_f6_and_stop_releases_everything():
    game = guide_game();game.running = False;game.stop_at = .2
    reader = Mock(side_effect=AssertionError('reader before F6'))
    result = drive_guidance(game.size(), reader, game, game, game, Mock(), clock=game,
                            navigator=LocalNavigator(lambda:scene(), water_areas=()))
    assert not result['arrived'] and not game.events
    reader.assert_not_called()


@pytest.mark.parametrize('fault', ['target', 'scene', 'inactive', 'stale', 'direction', 'collision'])
def test_changed_or_missing_inputs_pause_without_blind_movement(fault):
    game = guide_game();game.stop_at = 8.
    original = game.guidance
    def source():
        value = original()
        if game.time < .6:return value
        if fault == 'target':value['target'] = (50,0,50)
        elif fault == 'scene':value['context'] = 'other-level'
        elif fault == 'inactive':value['state'] = 'inactive'
        elif fault == 'stale':value['captured_at'] = -10
        return value
    game.guidance = source
    if fault == 'direction':game.movement_basis = Mock(side_effect=ValueError('no direction'))
    nav = LocalNavigator(lambda: scene() if fault != 'collision' else {'state':'loading'},
                         clock=game.monotonic, water_areas=())
    outcome = run(game, navigator=nav)
    assert not outcome['arrived'] and not game.keys and game.closed
    if fault in ('direction','collision'):assert not game.events
    assert not any(e['key'] in ('f','b','t') for _,e in game.events)


def test_stop_focus_loss_and_position_discontinuity_still_release_input():
    game = guide_game();game.stop_at = .8
    assert not run(game)['arrived'] and not game.keys
    game = guide_game();game.focus = False;game.stop_at = .2
    assert not run(game)['arrived'] and not game.events
    game = guide_game();original = game.read
    game.read = lambda: (50,0,0) if game.time > .8 else original()
    with pytest.raises(ValueError, match='跳变'):run(game)
    assert not game.keys and game.closed


def test_stationary_character_terminates_bounded_recovery_without_interaction():
    game = guide_game();game.speed = 0
    outcome = run(game)
    assert not outcome['arrived'] and game.time < 45
    assert not game.keys and game.closed
    assert not any(e['key'] in ('f','b','t') for _,e in game.events)


def test_target_change_during_jump_releases_input_without_finishing_old_jump():
    game = guide_game();game.speed = 0;game.stop_at = 5.
    game.guidance = lambda: snapshot(target=(50,0,50) if game.time >= 2.7 else (8,0,4), now=game.time)
    outcome = run(game)
    assert not outcome['arrived'] and not game.keys
    assert not [e for t,e in game.events if t >= 2.85 and e['type'] == 'key_down']


def test_changed_path_same_target_is_replaced_and_followed():
    game = guide_game()
    game.guidance = lambda: snapshot(path=((0,.5,0),(2,.5,2),(4,.5,4)) if game.time >= .6
                                     else ((0,.5,0),(0,.5,4),(4,.5,4)), now=game.time)
    revisions = []
    outcome = run(game, lambda s,m,**e: revisions.append(e['guidance_revision']) if s == 'guidance_path' else None)
    assert outcome['arrived'] and len(set(revisions)) == 2 and not game.keys


def test_slow_planning_never_holds_old_keys_and_revalidates_before_next_pulse():
    game = guide_game()
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=())
    original = nav.next_target
    delayed = [False]
    def plan(*args, **kwargs):
        if game.time >= .5 and not delayed[0]:
            delayed[0] = True
            assert not game.keys
            game.sleep(.7)
        return original(*args, **kwargs)
    nav.next_target = plan
    assert run(game, navigator=nav)['arrived']
    assert delayed[0] and not game.keys
