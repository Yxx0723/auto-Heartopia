import pytest

from utils.core.coordinate_trail_export import TRAIL_EXPORT_SCHEMA, trail_capture_payload


def test_export_keeps_xz_points_and_splits_disconnected_segments():
    trail = [
        {'x': 1, 'z': 2, 'segment': 4},
        {'x': 3, 'z': 4, 'segment': 4},
        {'x': 8, 'z': 9, 'segment': 5},
        {'x': 10, 'z': 11, 'segment': 5},
        {'x': 20, 'z': 30, 'segment': 6},
    ]

    payload = trail_capture_payload(trail, '2026-10-14T12:00:00+08:00')

    assert payload['schema'] == TRAIL_EXPORT_SCHEMA
    assert payload['coordinate_space'] == 'game-world-XZ'
    assert payload['captured_at'] == '2026-10-14T12:00:00+08:00'
    assert [segment['points_xz'] for segment in payload['segments']] == [
        [[1.0, 2.0], [3.0, 4.0]],
        [[8.0, 9.0], [10.0, 11.0]],
    ]
    assert [segment['point_count'] for segment in payload['segments']] == [2, 2]


@pytest.mark.parametrize('trail', [[], [{'x': 1, 'z': 2, 'segment': 0}]])
def test_export_requires_at_least_one_two_point_segment(trail):
    with pytest.raises(ValueError, match='没有完整的实走路段'):
        trail_capture_payload(trail, 'now')


def test_export_rejects_non_finite_coordinates():
    with pytest.raises(ValueError, match='无效坐标'):
        trail_capture_payload([
            {'x': 1, 'z': 2, 'segment': 0},
            {'x': float('nan'), 'z': 3, 'segment': 0},
        ], 'now')
