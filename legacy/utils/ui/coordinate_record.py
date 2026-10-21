"""Coordinate recording entry point. Input is observed only for control hotkeys."""
import json
from pathlib import Path

from utils.core.coordinate_route import PositionTrack, save_coordinates, distance
from utils.core.personal_route import validate_recording_metadata
from utils.core.thresholds import COORD_MIN_RECORDING_MOVEMENT
from utils.drivers.coordinate_sampler import PositionSampler
from utils.drivers.timeline_capture import Capture
from utils.drivers.town_window import TownWindow
from utils.ui.recorder_feedback import Feedback


def record_main(name='我的路线', author='我', planet='1', base='.', target='心动小镇'):
    validate_recording_metadata(name, author, planet)
    base = Path(base)
    track, window = PositionTrack(), TownWindow(target)
    feedback = Feedback(base)
    sampler = PositionSampler(track, window, base / 'coordinate-profile.json')
    frames = []

    def remember_start():
        try:
            frames.append(window.snapshot())
        except Exception as exc:
            print(f'起点截图未保存，坐标录制继续：{exc}', flush=True)

    try:
        capture = Capture(track, window, feedback,
                          stop_requested=lambda: (base / 'logs/stop-request').exists(),
                          remember_start=remember_start, on_tick=sampler.poll)
        if not capture.run():
            feedback.update('stopped', '已取消录制，没有保存路线', track, window)
            return
        if len(track.samples) < 2:
            feedback.update('stopped', '没有录到位置：请进入角色后按F6，移动一小段，再按F9保存', track, window)
            return
        if max(distance(p['position'], track.samples[0]['position']) for p in track.samples) < COORD_MIN_RECORDING_MOVEMENT:
            raise ValueError('未读到明显角色位移，请确认角色已走动，且坐标提示随走动变化')
        path = save_coordinates(base / 'map/default', name, author, target, track, capture.size)
        if frames:
            image = base / 'my_starts' / (path.stem + '.png')
            image.parent.mkdir(exist_ok=True)
            try:
                frames[0].save(image)
            except Exception as exc:
                print(f'路线已保存，但起点截图保存失败：{exc}', flush=True)
        feedback.update('saved', f'坐标路线已保存：{path.name} · {len(track.samples)}个位置',
                        track, window, file=path.name)
    except Exception as exc:
        if track.samples:
            recovery = base / 'logs/coordinate-recovery.json'
            recovery.write_text(json.dumps({'name': name, 'samples': track.samples,
                                            'error': str(exc)}, ensure_ascii=False), encoding='utf-8')
        feedback.update('error', f'坐标录制停止：{exc}', track, window)
        raise
    finally:
        sampler.close()
        feedback.close()
