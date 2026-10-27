"""Worker/UI contracts; never activate a real game window."""
from unittest.mock import Mock
import pytest

from tests.unit.test_point_playback import FakeWindow, WaitingControls
from utils.ui.point_playback import PointPlayback
from utils.flows.native_guidance_follow import drive_guidance


def test_stop_before_f6_does_not_open_reader_or_press_any_key():
    readers, devices, controls = [], [], []
    def reader(*a, **k):
        readers.append(1)
        raise AssertionError('reader before F6')
    def device(window):
        d = Mock();devices.append(d);return d
    def control(*args):
        c = WaitingControls(*args);controls.append(c);return c
    worker = PointPlayback('profile.json', window_factory=FakeWindow, reader_factory=reader,
                            guidance_runner=drive_guidance,
                            device_factory=device, controls_factory=control,
                            scene_provider=lambda pid: {'state':'ready','colliders':[]})
    worker.start_guidance(77)
    worker.stop();worker.close()
    assert not readers and not worker.active and controls[0].closed
    devices[0].execute.assert_not_called()
    devices[0].release_all.assert_called()
    assert worker.take_latest()['state'] == 'stopped'


def test_guidance_does_not_enable_collection_food_tool_or_route_memory(tmp_path):
    runner = Mock(return_value={'arrived': True, 'reason':'已到引导线末端', 'target_distance': 5.7})
    food, repair, tool = Mock(), Mock(), Mock()
    worker = PointPlayback('profile.json', guidance_runner=runner, window_factory=FakeWindow,
                            controls_factory=WaitingControls, device_factory=lambda w: Mock(),
                            scene_provider=lambda pid: {'state':'ready','colliders':[]},
                            food_factory=food, repair_factory=repair, tool_factory=tool,
                            diagnostics_path=tmp_path/'日志'/'回放.jsonl')
    worker.start_guidance(77);worker.thread.join(timeout=2)
    assert not worker.active and worker.path_memory is None
    result = worker.take_latest()
    assert result['state'] == 'finished' and result['navigation_mode'] == 'native_guidance'
    assert '5.7' in result['message'] and 'completed_at' in result
    for factory in (food, repair, tool):factory.assert_not_called()
    assert set(runner.call_args.kwargs) == {'clock', 'navigator'}
    worker.close()


@pytest.mark.parametrize('pid,provider', [(None,lambda p: {}), (True,lambda p:{}), (7,None)])
def test_missing_pid_or_collision_provider_rejected_before_thread(pid, provider):
    worker = PointPlayback('profile.json', scene_provider=provider)
    with pytest.raises(ValueError):worker.start_guidance(pid)
    assert worker.thread is None
