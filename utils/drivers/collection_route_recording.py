"""Passively record walking coordinates and physical F key presses."""
from datetime import datetime
from math import dist
from queue import Empty, SimpleQueue
import threading
import time

from utils.core.coordinate_route import valid_position
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.point_game_window import PointGameWindow
from utils.drivers.raw_mouse import RawMouse


class CollectionRouteRecording:
    def __init__(self, gate, pid, profile, scene_provider):
        self.gate, self.pid, self.profile = gate, pid, profile
        self.scene_provider = scene_provider
        self.cancelled = threading.Event()
        self.messages = SimpleQueue()
        self.thread = threading.Thread(target=self._run, name='collection-route-recording', daemon=True)

    def _run(self):
        try:
            with self.gate.claim('录制路线'):
                points, marks = self.capture()
            self.messages.put(('review', (points, marks)))
        except (ValueError, OSError, RuntimeError) as exc:
            self.messages.put(('error', str(exc)))

    def capture(self):
        window = PointGameWindow(self.pid)
        reader = ReadOnlyPosition.from_window(window, self.profile)
        events = SimpleQueue()
        held = set()
        points, marks = [], []
        active = False
        context = None
        next_sample = 0.

        def key(vk, _scan, _extended, down):
            if vk not in (0x46, 0x75, 0x78):  # F, F6, F9
                return
            if not down:
                held.discard(vk)
            elif vk not in held:
                held.add(vk)
                if window.foreground():
                    events.put((vk, time.monotonic()))

        def sample(mark=False, force=False):
            if not window.foreground():
                raise ValueError('游戏已失焦，录制停止')
            scene = self.scene_provider(self.pid)
            if not scene or scene.get('cache_context') != context:
                raise ValueError('场景归属已变化或无法确认，录制停止')
            position = reader.read_for_playback()
            if not valid_position(position) or not window.foreground():
                raise ValueError('无法确认游戏角色的新鲜坐标，录制停止')
            if points:
                gap = dist(position, points[-1]['position'])
                if gap > 1.5:
                    raise ValueError('录制坐标出现断点，请重新步行录制')
                if gap < .45 and not (mark or force):
                    return
            if len(points) >= 10000:
                raise ValueError('录制路线达到 10000 个位置上限')
            stamp = datetime.now().astimezone().isoformat(timespec='milliseconds')
            points.append({'id': len(points) + 1, 'name': f'轨迹点 {len(points) + 1}',
                           'position': list(position), 'sample_at': stamp, 'saved_at': stamp})
            if mark:
                marks.append(len(points))

        raw = RawMouse(lambda _x, _y: None, on_key=key)
        try:
            raw.start()
            self.messages.put(('status', '切回游戏按 F6 开始录制，按 F9 停止并核对采集位置。'))
            while not self.cancelled.wait(.01):
                if raw.error or not raw.thread.is_alive():
                    raise RuntimeError(f'键盘监听中断：{raw.error or "监听线程已退出"}')
                if active and not window.foreground():
                    raise ValueError('游戏已失焦，录制停止')
                while not self.cancelled.is_set():
                    try:
                        vk, received = events.get_nowait()
                    except Empty:
                        break
                    if vk == 0x75 and not active:
                        scene = self.scene_provider(self.pid)
                        context = scene.get('cache_context') if scene else None
                        if context is None:
                            raise ValueError('当前场景归属尚未确认')
                        sample(force=True)
                        active = True
                        self.messages.put(('status', '录制中：正常走路和按 F，按 F9 停止。'))
                    elif vk == 0x46 and active:
                        if time.monotonic() - received > .15:
                            raise ValueError('按 F 时未能及时读取坐标，请重新录制')
                        sample(mark=True)
                        if time.monotonic() - received > .15:
                            raise ValueError('按 F 的坐标采样已超时，请重新录制')
                    elif vk == 0x78:
                        if not active or not marks:
                            raise ValueError('本次没有录到 F 采集点')
                        sample(force=True)
                        return points, marks
                now = time.monotonic()
                if active and now >= next_sample:
                    sample()
                    next_sample = now + .05
            raise ValueError('录制已取消')
        finally:
            try:
                if raw.thread is not None:
                    raw.close()
            finally:
                reader.close()
