"""钓鱼动作测试面板：按钮手动触发单个动作 + 实时状态，用户自测操作流程。"""
import sys
import time
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk

import win32api
import win32con
import win32gui
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.core import thresholds as policy
from utils.drivers.mono_scene import MemorySceneMap
from utils.drivers.town_window import TownWindow, TimelineInput
from utils.drivers.screen import ScreenSource
from utils.flows.fishing_auto import FishingObserver, FishingSession
from utils.vision.fish_shadow import find_shadows
from utils.ui import theme

PROFILE = ROOT / 'webui/coordinate-monitor/profile.json'
PID = None
scene = None
state_reader = None
window = TownWindow('心动小镇')
device = TimelineInput(window)


def find_game():
    found = []
    def visit(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip() == '心动小镇':
            found.append(h)
    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def connect():
    global scene, state_reader, PID
    if scene is not None:
        try: scene.close()
        except Exception: pass
    h = find_game()
    if not h:
        return '未找到游戏窗口'
    import win32process
    _, PID = win32process.GetWindowThreadProcessId(h)
    scene = MemorySceneMap(PID, PROFILE)
    trace = scene.memory.snapshot()['paths'][0]['trace']
    entity = next(t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField')
    meta = scene.meta
    lpc = next(p for p in meta.dictionary(meta.ref(entity, '_components'), max_items=128)
               if meta.obj(p)['name'] == 'LocalPlayerComponent')
    def read():
        rod = meta.ref(lpc, '_interactTool')
        if not rod or meta.obj(rod)['name'] != 'HandHoldFishingRod':
            return 0, 0.0
        fc = meta.ref(rod, 'floatComponent')
        if not fc:
            return 0, 0.0
        s = meta.data(fc + 244, 4)
        p = meta.data(fc + 232, 4)
        import struct
        return struct.unpack('<i', s)[0], round(struct.unpack('<f', p)[0], 3)
    state_reader = read
    return f'已连接 PID={PID}'


class Panel:
    def __init__(self, root):
        self.root = root
        root.title('钓鱼动作测试')
        root.geometry('340x620')
        theme.apply_theme(root) if hasattr(theme, 'apply_theme') else None
        self.status = tk.StringVar(value='未连接')
        self.auto = {'thread': None, 'stop': False}
        self.follow = {'thread': None, 'stop': False}

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, textvariable=self.status, wraplength=320,
                  foreground='#2d4a2b').pack(pady=(0, 8), anchor='w')

        ttk.Button(frm, text='连接游戏', command=self.on_connect).pack(fill='x', pady=2)
        ttk.Separator(frm).pack(fill='x', pady=6)
        ttk.Label(frm, text='— 单步动作 —').pack(anchor='w')
        ttk.Button(frm, text='单击左键（抛竿）', command=lambda: self.click('click')).pack(fill='x', pady=2)
        ttk.Button(frm, text='按下左键（开始收线）', command=lambda: self.click('down')).pack(fill='x', pady=2)
        ttk.Button(frm, text='松开左键', command=lambda: self.click('up')).pack(fill='x', pady=2)
        ttk.Separator(frm).pack(fill='x', pady=6)
        ttk.Label(frm, text='— 观察 —').pack(anchor='w')
        ttk.Button(frm, text='读状态（State/playTime）', command=self.on_read).pack(fill='x', pady=2)
        ttk.Button(frm, text='识别鱼影', command=self.on_shadow).pack(fill='x', pady=2)
        ttk.Button(frm, text='开始跟随鱼影（鼠标）', command=self.on_follow).pack(fill='x', pady=2)
        ttk.Button(frm, text='停止跟随', command=self.on_follow_stop).pack(fill='x', pady=2)
        ttk.Button(frm, text='截图', command=self.on_shot).pack(fill='x', pady=2)
        ttk.Separator(frm).pack(fill='x', pady=6)
        ttk.Label(frm, text='— 全自动 —').pack(anchor='w')
        ttk.Button(frm, text='启动全自动（单击抛竿）', command=self.on_auto).pack(fill='x', pady=2)
        ttk.Button(frm, text='急停（松开左键）', command=self.on_stop).pack(fill='x', pady=2)

        self.log = tk.Text(frm, height=12, wrap='word')
        self.log.pack(fill='both', expand=True, pady=(8, 0))

        self.tick_state()

    def _log(self, msg):
        self.log.insert('end', f'{time.strftime("%H:%M:%S")} {msg}\n')
        self.log.see('end')

    def on_connect(self):
        try:
            msg = connect()
            self.status.set(msg)
            self._log(msg)
        except Exception as exc:
            self.status.set(f'连接失败: {exc}')
            self._log(f'连接失败 {type(exc).__name__}: {exc}')

    def click(self, kind):
        h = find_game()
        if not h:
            self._log('未找到游戏窗口')
            return
        def do():
            win32gui.SetForegroundWindow(h)
            time.sleep(.2)
            if kind == 'click':
                device.execute({'type': 'mouse_down', 'button': 'left'})
                time.sleep(.06)
                device.execute({'type': 'mouse_up', 'button': 'left'})
                self._log('单击左键')
            elif kind == 'down':
                device.execute({'type': 'mouse_down', 'button': 'left'})
                self._log('按下左键')
            else:
                device.execute({'type': 'mouse_up', 'button': 'left'})
                self._log('松开左键')
        threading.Thread(target=do, daemon=True).start()

    def on_read(self):
        if state_reader is None:
            self._log('请先连接')
            return
        try:
            s, p = state_reader()
            h = find_game()
            fg = win32gui.GetForegroundWindow() == h if h else False
            self._log(f'State={s} playTime={p} 游戏前台={fg}')
        except Exception as exc:
            self._log(f'读状态失败 {type(exc).__name__}: {exc}')

    def on_shadow(self):
        h = find_game()
        if not h:
            self._log('未找到游戏窗口')
            return
        l, t, r, b = win32gui.GetWindowRect(h)
        pic = ScreenSource.capture_window_background(h, (l, t, r-l, b-t))
        frame = cv2.cvtColor(np.asarray(pic.convert('RGB')), cv2.COLOR_RGB2BGR)
        shadows = find_shadows(frame)
        if not shadows:
            self._log('鱼影：0 条')
        else:
            for s in shadows[:5]:
                self._log(f'鱼影 area={s.area} 中心={s.center} bbox=({s.x},{s.y},{s.w},{s.h})')

    def on_follow(self):
        if self.follow['thread'] and self.follow['thread'].is_alive():
            self._log('跟随已在运行')
            return
        h = find_game()
        if not h:
            self._log('未找到游戏窗口')
            return
        self.follow['stop'] = False
        self.follow['thread'] = threading.Thread(target=self._follow_loop, args=(h,), daemon=True)
        self.follow['thread'].start()
        self._log('跟随鱼影启动')

    def _follow_loop(self, h):
        import win32api
        l, t, r, b = win32gui.GetWindowRect(h)
        cw, ch = r - l, b - t
        cur = None
        last_log = 0
        while not self.follow['stop']:
            try:
                pic = ScreenSource.capture_window_background(h, (l, t, cw, ch))
                frame = cv2.cvtColor(np.asarray(pic.convert('RGB')), cv2.COLOR_RGB2BGR)
                shadows = find_shadows(frame)
                if shadows:
                    s = max(shadows, key=lambda x: x.area)
                    # 鱼影坐标是相对截图(外框)，直接加外框偏移得屏幕坐标
                    tx, ty = l + int(s.cx), t + int(s.cy)
                    if cur is None:
                        cur = (tx, ty)
                    else:
                        # 平滑跟随：每次向目标移动 40%
                        cur = (round(cur[0] + (tx - cur[0]) * .4),
                               round(cur[1] + (ty - cur[1]) * .4))
                    win32api.SetCursorPos(cur)
                    if time.monotonic() - last_log > .5:
                        self._log(f'跟随 鱼影={s.center} 鼠标={cur}')
                        last_log = time.monotonic()
                else:
                    if time.monotonic() - last_log > 1:
                        self._log('跟随：暂无鱼影')
                        last_log = time.monotonic()
            except Exception as exc:
                self._log(f'跟随异常 {type(exc).__name__}: {exc}')
            time.sleep(.06)

    def on_follow_stop(self):
        self.follow['stop'] = True
        self._log('跟随已停止')

    def on_shot(self):
        h = find_game()
        if not h: return
        l, t, r, b = win32gui.GetWindowRect(h)
        pic = ScreenSource.capture_window_background(h, (l, t, r-l, b-t))
        out = ROOT.parent / '钓鱼测试截图.png'
        out = Path(r'D:/WorkBuddy/2026-11-12-20-11-41') / '钓鱼测试截图.png'
        pic.save(out)
        self._log(f'截图已存 {out}')

    def on_auto(self):
        if self.auto['thread'] and self.auto['thread'].is_alive():
            self._log('全自动已在运行')
            return
        if state_reader is None:
            self._log('请先连接')
            return
        self.auto['stop'] = False
        self.auto['thread'] = threading.Thread(target=self._auto_loop, daemon=True)
        self.auto['thread'].start()
        self._log('全自动启动')

    def _auto_loop(self):
        def sink(action):
            if action.kind in ('cast', 'hold'):
                device.execute({'type': 'mouse_down', 'button': 'left'})
            elif action.kind == 'release':
                device.execute({'type': 'mouse_up', 'button': 'left'})
            self._log(f'动作 {action.kind}: {action.reason}')
        sess = FishingSession(FishingObserver(state_reader), input_sink=sink)
        sess.start()
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < 180 and not self.auto['stop']:
            act = sess.update()
            if sess.state != last:
                last = sess.state
            time.sleep(policy.FISHING_POLL_SECONDS)
        if sess.holding:
            device.execute({'type': 'mouse_up', 'button': 'left'})
        self._log(f'全自动结束：抛竿{sess.casts}次')

    def on_stop(self):
        self.auto['stop'] = True
        device.execute({'type': 'mouse_up', 'button': 'left'})
        self._log('急停：已松开左键')

    def tick_state(self):
        if state_reader is not None:
            try:
                s, p = state_reader()
                self.status.set(f'State={s}  playTime={p}')
            except Exception:
                pass
        self.root.after(500, self.tick_state)


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == '__main__':
    main()
