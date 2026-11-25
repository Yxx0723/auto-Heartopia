"""Background fishing task for the native monitor."""
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread

from utils.core.fishing_log import append_fishing_event, fishing_event


class FishingControl:
    def __init__(self, profile, log_dir, input_gate):
        self.profile = Path(profile)
        self.log_dir = Path(log_dir)
        self.input_gate = input_gate
        self.cancelled = Event()
        self.thread = None
        self.events = SimpleQueue()
        self.out_dir = None
        self.metrics = {}
        self.runner = None
        self.message = '在钓点拿出鱼竿，点击开始后切回游戏按 F6。'

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, duration=None, catch_target=None):
        if self.active:
            return
        self.cancelled.clear()
        self.metrics = {}
        self.runner = None
        self.duration,self.catch_target = duration,catch_target
        self.message = '正在准备自动钓鱼…'
        self.thread = Thread(target=self.run, name='auto-fishing')
        self.thread.start()

    def stop(self):
        self.cancelled.set()

    def take_log_events(self):
        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except Empty:
                return events

    def log(self, event, message):
        row = fishing_event(event, message)
        self.events.put(row)
        if self.out_dir is not None:
            append_fishing_event(self.out_dir / '钓鱼日志.txt', row)

    def run(self):
        self.out_dir = None
        self.duration = getattr(self,'duration',None)
        self.catch_target = getattr(self,'catch_target',None)
        try:
            with self.input_gate.claim('自动钓鱼'):
                self.out_dir = self.log_dir / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                self.out_dir.mkdir(parents=True, exist_ok=True)
                self.log('prepare', '已点击「开始钓鱼」，正在准备')
                self.run_with_input()
        except InterruptedError as exc:
            self.message = str(exc)
            self.log('interrupt', self.message)
        except Exception as exc:
            self.message = f'自动钓鱼失败：{type(exc).__name__}：{exc}'
            self.log('error', self.message)

    def run_with_input(self):
        import win32api
        import win32con
        import win32gui
        from tools.fishing_auto_run import FishingRunner, find_game

        def stop_requested():
            return self.cancelled.is_set() or bool(win32api.GetAsyncKeyState(win32con.VK_F9) & 0x8000)

        hwnd = find_game()
        self.message = '已待命。切回游戏按 F6 开始，按 F9 取消。'
        self.log('standby', self.message)
        start_was_down = bool(win32api.GetAsyncKeyState(win32con.VK_F6) & 0x8000)
        while not stop_requested():
            if not win32gui.IsWindow(hwnd):
                raise InterruptedError('游戏窗口已关闭，已取消自动钓鱼')
            start_down = bool(win32api.GetAsyncKeyState(win32con.VK_F6) & 0x8000)
            if start_down and not start_was_down and win32gui.GetForegroundWindow() == hwnd:
                break
            start_was_down = start_down
            self.cancelled.wait(.02)
        else:
            raise InterruptedError('已取消自动钓鱼')

        self.log('hotkey', '在游戏前台检测到 F6，开始自动钓鱼')
        interrupted = None

        def update(row):
            nonlocal interrupted
            self.events.put(row)
            if row['ev'] == 'interrupt':
                interrupted = row['reason']
            if row['ev'] not in {'end', 'button', 'move', 'game_state', 'aim_observe'}:
                self.message = f"{row['reason']} · 抛竿 {runner.casts} 次"

        runner = FishingRunner(profile=self.profile, hwnd=hwnd, out_dir=self.out_dir, verbose=False,
                               stop_requested=stop_requested, on_event=update,
                               duration=self.duration, catch_target=self.catch_target)
        self.runner = runner
        runner.run()
        snapshot = getattr(runner,'metrics',None)
        if callable(snapshot):
            self.metrics = snapshot()
        self.message = f'{interrupted or "自动钓鱼已结束"} · 抛竿 {runner.casts} 次'
