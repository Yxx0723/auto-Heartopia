import json
from unittest.mock import Mock

import pytest

from utils.core.coordinate_route import PositionTrack, save_coordinates
from utils.core.schema import validate_map
from utils.drivers.coordinate_sampler import PositionSampler
from utils.flows.timeline_player import play_main
from utils.ui.recorder_feedback import Feedback


def test_sampler_is_lazy_rate_limited_and_never_samples_background_window():
    track = PositionTrack()
    window = Mock()
    window.foreground.return_value = True
    reader = Mock()
    reader.read.return_value = (1, 2, 3)
    factory = Mock(return_value=reader)
    now = [0.]
    sampler = PositionSampler(track, window, 'profile.json', lambda: now[0], factory)
    sampler.poll()
    factory.assert_not_called()
    track.resume(0)
    sampler.poll()
    now[0] = .01
    sampler.poll()
    assert reader.read.call_count == 1
    now[0] = .06
    sampler.poll()
    window.foreground.return_value = False
    now[0] = .12
    sampler.poll()
    assert len(track.samples) == 2
    sampler.close()
    reader.close.assert_called_once()


def test_saved_coordinates_round_trip_and_dispatch_to_position_player(tmp_path, monkeypatch):
    track = PositionTrack()
    track.resume(0)
    track.sample([1, 2, 3], 0)
    track.sample([1, 2, 4], .5)
    track.pause(1)
    folder = tmp_path / 'map/default'
    first = save_coordinates(folder, '长' * 80, '我', '心动小镇', track, [1280, 720])
    second = save_coordinates(folder, '长' * 80, '我', '心动小镇', track, [1280, 720])
    assert first != second and first.is_file()
    data = json.loads(first.read_text(encoding='utf-8'))
    assert not validate_map(data) and len(data['name']) <= 80
    callback = Mock()
    monkeypatch.setattr('utils.flows.coordinate_player.play_main', callback)
    play_main(tmp_path, first.stem.removeprefix('map_'))
    callback.assert_called_once_with(tmp_path, data)


def test_position_feedback_contains_coordinates_without_input_events(tmp_path):
    track = PositionTrack()
    track.resume(0)
    track.sample([1, 2, 3], 0)
    track.pause(1)
    feedback = Feedback(tmp_path, overlay=False)
    feedback.update('recording', 'test', track)
    data = json.loads((tmp_path / 'logs/recorder-state.json').read_text(encoding='utf-8'))
    assert data['engine'] == 'coordinate_route' and data['point_count'] == 1
    assert data['position'] == [1, 2, 3]


def test_sampler_does_not_hide_read_failures():
    track = PositionTrack()
    track.resume(0)
    reader = Mock()
    reader.read.side_effect = ValueError('invalid')
    window = Mock()
    window.foreground.return_value = True
    sampler = PositionSampler(track, window, 'profile.json', lambda: 0, Mock(return_value=reader))
    with pytest.raises(ValueError, match='invalid'):
        sampler.poll()
    sampler.close()
    reader.close.assert_called_once()
