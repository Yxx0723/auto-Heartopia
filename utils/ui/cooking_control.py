"""One background cooking task, independent of Tk and coordinate route memory."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
from uuid import uuid4

from utils.core.cooking import Recipe, CookingSession
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.point_game_window import PointGameWindow
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import PreciseClock
from utils.vision.cooking_menu import CookingMenu


class CookingControl:
    def __init__(self, profile, root, busy=lambda: False, window_factory=PointGameWindow,
                 reader_factory=ReadOnlyPosition, controls_factory=PlaybackControls,
                 device_factory=TimelineInput, clock=PreciseClock, runner=None, input_gate=None):
        self.profile, self.root, self.busy = Path(profile), Path(root), busy
        self.window_factory, self.reader_factory = window_factory, reader_factory
        self.controls_factory, self.device_factory, self.clock = controls_factory, device_factory, clock
        self.runner = runner
        self.input_gate = input_gate
        self.thread = None
        self.stop_event = threading.Event()
        self.results = queue.Queue(maxsize=1)
        self.lock = threading.RLock()
        self.stream = None
        self.signature = None
        self.path = None
        self.pots = {}
        self.task_state = 'idle'

    @property
    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def update(self, state, message, window=None, **extra):
        with self.lock:
            pot = extra.get('pot') or {}
            signature = (state, message, pot.get('id'), pot.get('stirs'), pot.get('confirmed'))
            if signature == self.signature:
                return
            self.signature = signature
            data = deepcopy({'state': state, 'message': message, **extra})
            self.task_state = state
            if pot.get('id'):
                self.pots[pot['id']] = deepcopy(pot)
            if self.stream:
                self.stream.write(json.dumps({'at': datetime.now().astimezone().isoformat(), **data},
                                             ensure_ascii=False, allow_nan=False)+'\n')
                self.stream.flush()
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass
            self.results.put_nowait(data)

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def start(self, pid, visual_profile, target=1, reserve=20, max_star=1):
        if self.active or self.busy():
            raise ValueError('已有回放或烹饪任务，请先停止')
        if self.runner is None:
            raise ValueError('烹饪入口未装配，请使用新版窗口程序')
        if type(reserve) is not int or not 0 <= reserve <= 100000000:
            raise ValueError('咖啡豆保留量无效')
        if type(max_star) is not int or not 0 <= max_star <= 5:
            raise ValueError('原料星级范围无效')
        menu = CookingMenu(visual_profile)
        if menu.profile.get('recipe_name') != '咖啡':
            raise ValueError('首版仅支持已只读核对的咖啡食谱')
        # 2026-10-24: exact TableBagitem names 45108/46106 and four observed slots.
        session = CookingSession(Recipe('咖啡', (45108,), {46106: 4}, {46106: reserve}), target)
        window = self.window_factory(pid)
        if tuple(window.size()) != menu.size:
            raise ValueError('当前游戏尺寸与烹饪标定不符，请恢复尺寸或重新标定')
        self.stop_event.clear()
        self.pots = {}
        self.signature = None
        self.thread = threading.Thread(target=self._run, args=(pid, window, menu, session, max_star),
                                       name='single-stove-cooking', daemon=True)
        self.thread.start()

    def _run(self, *args):
        if self.input_gate is None:
            return self._execute(*args)
        try:
            with self.input_gate.claim('自动烹饪'):
                return self._execute(*args)
        except ValueError as exc:
            self.update('error', str(exc))

    def _execute(self, pid, window, menu, session, max_star):
        reader = device = controls = None
        try:
            self.path = self.root/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
            self.path.mkdir(parents=True, exist_ok=False)
            with self.lock:
                self.stream = (self.path/'events.jsonl').open('a', encoding='utf-8')
            device = self.device_factory(window)
            controls = self.controls_factory(window, window.size(), self, self.stop_event.is_set)
            controls.start()
            # Opening this handle is read-only; no food/equipment maintenance is run.
            reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
            self.update('cooking_wait', '咖啡单灶任务已就绪 · 切回游戏按 F6 · F9停止')
            self.runner(session, window, controls, device, menu.observe,
                        lambda: reader.cooking_inventory_snapshot(max_star), self.clock, self.update)
        except Exception as exc:
            self.update('error', '自动烹饪停止：'+str(exc), **session.record())
        finally:
            for obj, method in ((device, 'release_all'), (controls, 'close'), (reader, 'close')):
                if obj is not None:
                    try:
                        getattr(obj, method)()
                    except Exception:
                        pass
            with self.lock:
                if self.stream:
                    self.stream.close()
                    self.stream = None
            if self.path:
                (self.path/'summary.json').write_text(json.dumps({**session.record(), 'task_state': self.task_state,
                            'pots': self.pots}, ensure_ascii=False,
                                                               allow_nan=False, indent=2), 'utf-8')

    def stop(self):
        self.stop_event.set()

    def close(self):
        self.stop()
        if self.thread:
            self.thread.join(timeout=2)
