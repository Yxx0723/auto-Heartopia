import math

import pytest

from utils.core.coordinate_route import (PositionTrack, build_waypoints, direction_keys,
                                         make_basis, rotate_basis, validate_coordinate_route)


def route():
    return {'engine': 'coordinate_route', 'coordinate_version': 1, 'name': '路线', 'author': '我',
            'target_title': '心动小镇', 'manual_start': True, 'client_size': [800, 600],
            'duration': 1, 'samples': [{'at': 0, 'position': [0, 2, 0]},
                                      {'at': 1, 'position': [0, 2, 3]}]}


def test_coordinate_schema_rejects_nan_and_backward_time():
    data = route()
    assert validate_coordinate_route(data) == []
    data['samples'][1]['position'][0] = math.nan
    assert validate_coordinate_route(data)
    data = route()
    data['samples'][1]['at'] = -1
    assert validate_coordinate_route(data)


def test_coordinate_schema_rejects_teleport_and_wrong_version():
    data = route()
    data['samples'][1]['position'][2] = 100
    assert validate_coordinate_route(data)
    data = route()
    data['coordinate_version'] = 9
    assert validate_coordinate_route(data)


def test_pause_does_not_add_elapsed_time_or_unrecorded_movement():
    track = PositionTrack()
    track.resume(10)
    track.sample([1, 2, 3], 10)
    track.sample([1, 2, 4], 10.5)
    track.pause(11)
    track.resume(20)
    track.sample([1, 2, 4], 20)
    assert track.samples[-1]['at'] == 1
    assert track.at(20.5) == 1.5
    track.pause(21)
    track.resume(30)
    with pytest.raises(ValueError, match='暂停'):
        track.sample([20, 2, 4], 30)


def test_mouse_and_keyboard_events_do_not_pollute_coordinate_samples():
    track = PositionTrack()
    track.resume(0)
    track.key('w', True, 0)
    track.button('left', True, 0)
    track.move(10, 2, 0)
    track.sample([1, 2, 3], .05)
    assert len(track.events) == 1
    assert track.events[0]['position'] == [1, 2, 3]


def test_waypoints_keep_corners_final_position_and_idle_waits():
    samples = [{'at': 0, 'position': [0, 0, 0]}, {'at': .2, 'position': [0, 0, .8]},
               {'at': .8, 'position': [0, 0, .8]}, {'at': 1, 'position': [.6, 0, .8]},
               {'at': 1.05, 'position': [.7, 0, .8]}]
    points = build_waypoints(samples)
    assert any(p['position'] == [0, 0, .8] and p['wait'] >= .6 - 1e-6 for p in points)
    assert points[-1]['position'] == [.7, 0, .8]


@pytest.mark.parametrize('target,expected', [([0, 0, 3], {'w'}), ([3, 0, 0], {'d'}),
                                         ([0, 0, -3], {'s'}), ([-3, 0, 0], {'a'}),
                                         ([3, 0, 3], {'w', 'd'})])
def test_navigation_uses_world_displacement_not_recorded_key_duration(target, expected):
    basis = make_basis([0, 0, 1], [1, 0, 0])
    assert direction_keys([0, 0, 0], target, basis) == expected


def test_rotated_camera_basis_and_feedback_rotation():
    basis = make_basis([1, 0, 0], [0, 0, -1])
    assert direction_keys([0, 0, 0], [3, 0, 0], basis) == {'w'}
    updated = rotate_basis(basis, {'w'}, [1, 0, -.2])
    assert updated['forward'][1] < 0
    with pytest.raises(ValueError):
        make_basis([0, 0, 1], [0, 0, 2])


def test_no_movement_and_reversed_feedback_are_rejected():
    with pytest.raises(ValueError):
        make_basis([0, 0, 0], [1, 0, 0])
    with pytest.raises(ValueError):
        rotate_basis(make_basis([0, 0, 1], [1, 0, 0]), {'w'}, [0, 0, -1])
