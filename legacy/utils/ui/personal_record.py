"""Personal town routes, with no Star Rail window or combat dependency."""
from pathlib import Path

from utils.core.input_timeline import Timeline, save_timeline
from utils.core.personal_route import validate_recording_metadata
from utils.drivers.timeline_capture import Capture
from utils.drivers.town_window import TownWindow
from utils.ui.recorder_feedback import Feedback


def record_main(name='我的路线', author='我', planet='1', base='.', target='心动小镇'):
    validate_recording_metadata(name, author, planet)
    base = Path(base)
    timeline, window = Timeline(), TownWindow(target)
    feedback = Feedback(base)
    frames = []

    def remember_start():
        try:
            frames.append(window.snapshot())
        except Exception as exc:
            print(f'起点截图未保存，键鼠录制继续：{exc}', flush=True)

    try:
        capture = Capture(timeline, window, feedback,
                          stop_requested=lambda: (base / 'logs/stop-request').exists(),
                          remember_start=remember_start)
        if not capture.run():
            feedback.update('stopped', '已取消录制，没有保存路线', timeline, window)
            return
        if not timeline.events:
            feedback.update('stopped', '没有录到动作：请启动录制后，在游戏中按F6再移动；F9保存', timeline, window)
            return
        path = save_timeline(base / 'map/default', name, author, target, timeline, capture.size)
        if frames:
            image = base / 'my_starts' / (path.stem + '.png')
            image.parent.mkdir(exist_ok=True)
            try:
                frames[0].save(image)
            except Exception as exc:
                print(f'路线已保存，但起点截图保存失败：{exc}', flush=True)
        feedback.update('saved', f'已保存：{path.name} · {timeline.duration:.1f}秒 · {len(timeline.events)}个动作',
                        timeline, window, file=path.name)
    except Exception as exc:
        feedback.update('error', f'录制失败：{exc}', timeline, window)
        raise
    finally:
        feedback.close()
