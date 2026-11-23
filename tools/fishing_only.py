"""全流程自动钓鱼（命令行，无 UI）。

只读游戏内存判咬钩（FloatComponent.State: 0=空闲 6=拉扯），
视觉取色判鱼线张力（红=松手），鼠标左键控制抛竿/收线。

安全：异常、Ctrl+C、失去前台都会松开左键；默认跑 10 分钟。
键位为鼠标左键（用户确认）。抛竿时长 FISHING_CAST_SECONDS 未标定，可命令行覆盖。
"""
import argparse
import ctypes
import sys
import time
from pathlib import Path

import win32api
import win32con
import win32gui

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.core import thresholds as policy
from utils.drivers.mono_scene import MemorySceneMap
from utils.drivers.town_window import TownWindow, TimelineInput
from utils.drivers.screen import ScreenSource
from utils.flows.fishing_auto import FishingObserver, FishingSession

import cv2
import numpy as np

PROFILE = ROOT / 'webui/coordinate-monitor/profile.json'
LINE_ROI = (slice(140, 430), slice(500, 620))   # 鱼线区域（实测咬钩帧 x≈530-610 y≈150-420）


def find_game():
    found = []
    def visit(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip() == '心动小镇':
            found.append(h)
    win32gui.EnumWindows(visit, None)
    if not found:
        raise RuntimeError('未找到游戏窗口')
    return found[0]


def bring_front(hwnd):
    ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint(-1).value)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(.1)
    win32gui.SetForegroundWindow(hwnd)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(.4)
    return win32gui.GetForegroundWindow() == hwnd


class MemoryState:
    """读 FloatComponent.State / playTime，经 LocalPlayerComponent._interactTool。"""

    def __init__(self, scene):
        import struct
        self.meta = scene.meta
        self.mem = scene.memory
        self.struct = struct
        trace = self.mem.snapshot()['paths'][0]['trace']
        entity = next(t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField')
        self.lpc = next(p for p in self.meta.dictionary(self.meta.ref(entity, '_components'), max_items=128)
                        if self.meta.obj(p)['name'] == 'LocalPlayerComponent')

    def read(self):
        rod = self.meta.ref(self.lpc, '_interactTool')
        if not rod or self.meta.obj(rod)['name'] != 'HandHoldFishingRod':
            return 0, 0.0
        fc = self.meta.ref(rod, 'floatComponent')
        if not fc:
            return 0, 0.0
        state = self.struct.unpack('<i', self.meta.data(fc + 244, 4))[0]
        play = self.struct.unpack('<f', self.meta.data(fc + 232, 4))[0]
        return state, round(play, 4)


def make_line_reader(hwnd, rect):
    def read_color():
        pic = ScreenSource.capture_window_background(hwnd, rect)
        if pic is None:
            return None
        frame = cv2.cvtColor(np.asarray(pic.convert('RGB')), cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        ys, xs = LINE_ROI
        h, s, v = H[ys, xs], S[ys, xs], V[ys, xs]
        bright = v > 180
        white = int((bright & (s < 55)).sum())
        yellow = int((bright & (s >= 55) & (h >= 15) & (h <= 40)).sum())
        orange = int((bright & (s >= 80) & (h >= 8) & (h < 15)).sum())
        red = int((bright & (s >= 100) & ((h < 8) | (h > 170))).sum())
        dom = max((('white', white), ('yellow', yellow), ('orange', orange), ('red', red)),
                  key=lambda x: x[1])
        return {'dom': dom[0], 'white': white, 'yellow': yellow, 'orange': orange, 'red': red}
    return read_color


def main():
    ap = argparse.ArgumentParser(description='全流程自动钓鱼')
    ap.add_argument('--pid', type=int, default=None, help='游戏进程 PID（默认自动从窗口取）')
    ap.add_argument('--cast', type=float, default=policy.FISHING_CAST_SECONDS, help='抛竿按住秒数')
    ap.add_argument('--duration', type=float, default=600., help='总运行时长秒数')
    ap.add_argument('--no-input', action='store_true', help='只观察不发送鼠标输入（干跑）')
    args = ap.parse_args()

    hwnd = find_game()
    import win32process
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    pid = args.pid or pid
    print(f'game hwnd={hwnd} pid={pid}', flush=True)

    # 置前
    bring_front(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    rw, rh = win32gui.GetClientRect(hwnd)[2:]
    rect = (left, top, rw, rh)
    print(f'client rect={rect}', flush=True)

    scene = MemorySceneMap(pid, PROFILE)
    observer_state = MemoryState(scene)
    read_line = make_line_reader(hwnd, rect)

    window = TownWindow('心动小镇')
    device = TimelineInput(window)
    held = {'v': False}

    def input_sink(action):
        if args.no_input:
            return
        if action.kind in ('cast', 'hold'):
            device.execute({'type': 'mouse_down', 'button': 'left'})
            held['v'] = True
        elif action.kind == 'release':
            device.execute({'type': 'mouse_up', 'button': 'left'})
            held['v'] = False

    session = FishingSession(FishingObserver(observer_state.read, read_line),
                             input_sink=input_sink, cast_seconds=args.cast)
    print(f'cast={args.cast}s duration={args.duration}s no_input={args.no_input}', flush=True)
    print('启动：3 秒后开始抛竿（Ctrl+C 安全停止）', flush=True)
    time.sleep(3)

    started = time.monotonic()
    last_state = None
    try:
        session.start()  # 第一次抛竿
        while time.monotonic() - started < args.duration:
            # 前台检查：失去前台立即停止并松键
            if win32gui.GetForegroundWindow() != hwnd:
                print('游戏失去前台，停止', flush=True)
                break
            act = session.update()
            if session.state != last_state:
                print(f'[{time.monotonic()-started:.1f}s] state={session.state} '
                      f'casts={session.casts} {act.reason}', flush=True)
                last_state = session.state
            time.sleep(max(0.005, policy.FISHING_POLL_SECONDS - .005))
    except KeyboardInterrupt:
        print('\n用户中断', flush=True)
    finally:
        if held['v']:
            device.execute({'type': 'mouse_up', 'button': 'left'})
            held['v'] = False
            print('已松开左键', flush=True)
        scene.close()
        actions = [a.kind for a in session.actions]
        print(f'结束：抛竿{session.casts}次，动作 {actions[:30]}', flush=True)


if __name__ == '__main__':
    main()
