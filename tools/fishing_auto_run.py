# -*- coding: utf-8 -*-
"""自动钓鱼实机运行器：视觉闭环瞄准 + 内存判咬钩 + 张力收线。

流程：
    等水面出现游动鱼影（后台截图，不抢前台）
    → 长按左键进入瞄准（实测约 450ms 才置 _aimState）
    → 识别水面落点圈（识别不稳时回退到该钓点固定锚点）
    → 按比例注入一次鼠标位移，让落点圈压到鱼影上
    → 立刻松开左键抛竿（注入的落点会回弹，必须马上松）
    → FloatComponent.State=6 咬钩 → 长按收线
    → 鱼线红色像素超阈值 → 松手放线；State=0 → 收尾

关键实现约束（2026-11-22 实机标定，勿随意改）：
    * 鼠标位移必须用裸 ``SendInput(MOUSEEVENTF_MOVE)``；带 0x2000
      （MOUSEEVENTF_MOVE_NOCOALESCE）会被 Unity 直接忽略。
    * 游戏窗口最小化时截图返回 None、SetForegroundWindow 会抛错，需先恢复。
    * 恢复窗口后的第一次点击会被"激活窗口"吞掉，因此抛竿后有 1.5s 未生效重试。

安全：任何异常/退出都会松开左键并关闭内存连接。
"""
import argparse
import ctypes
import json
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import win32con
import win32gui
import win32process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.core import thresholds as policy
from utils.drivers.mono_scene import MemorySceneMap
from utils.drivers.screen import ScreenSource
from utils.drivers.town_window import TimelineInput, TownWindow
from utils.vision.aim_ring import find_ring
from utils.vision.fish_shadow import find_shadows
from tools.fishing_only import bring_front, make_line_reader

#: 注入像素 → 落点圈屏幕位移 的经验增益（实测 2.4~3.1，取保守值）
AIM_GAIN_X = 2.4
AIM_GAIN_Y = 1.4
#: 落点圈静止锚点（本钓点 1382×807 画面实测），识别失败时兜底
AIM_ANCHOR = (685.0, 275.0)
#: 鱼影面积下限：排除木栈道/水花等小暗块
FISH_MIN_AREA = 1200
#: 活体判定：两帧中心位移下限（像素）
FISH_MIN_MOVE = 3.0


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [('mi', _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ('payload',)
    _fields_ = [('type', wintypes.DWORD), ('payload', _InputUnion)]


_USER32 = ctypes.WinDLL('user32', use_last_error=True)
_USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
_USER32.SendInput.restype = wintypes.UINT


def move_mouse(dx, dy):
    """裸相对移动。带 MOUSEEVENTF_MOVE_NOCOALESCE(0x2000) 时游戏会忽略。"""
    event = _Input(type=0, mi=_MouseInput(int(dx), int(dy), 0, 0x0001, 0, 0))
    _USER32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))


def find_game():
    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip() == '心动小镇':
            found.append(hwnd)

    win32gui.EnumWindows(visit, None)
    if not found:
        raise RuntimeError('未找到游戏窗口')
    return found[0]


class FishingRunner:
    def __init__(self, duration=600.0, out_dir=None, verbose=True):
        self.duration = float(duration)
        self.out = Path(out_dir) if out_dir else ROOT / 'fishing-runs'
        self.out.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.hwnd = find_game()
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            time.sleep(0.8)
        _, self.pid = win32process.GetWindowThreadProcessId(self.hwnd)
        self.rect = win32gui.GetWindowRect(self.hwnd)
        client = win32gui.ClientToScreen(self.hwnd, (0, 0))
        width, height = win32gui.GetClientRect(self.hwnd)[2:]
        self.client_rect = (client[0], client[1], width, height)
        bring_front(self.hwnd)
        time.sleep(0.4)

        self.scene = MemorySceneMap(self.pid, ROOT / 'webui/coordinate-monitor/profile.json')
        meta = self.scene.meta
        trace = self.scene.memory.snapshot()['paths'][0]['trace']
        entity = next(t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField')
        self.lpc = next(p for p in meta.dictionary(meta.ref(entity, '_components'), max_items=128)
                        if meta.obj(p)['name'] == 'LocalPlayerComponent')
        self.meta = meta
        self.rod = meta.ref(self.lpc, '_interactTool')
        self.rod_fields = meta.obj(self.rod)['fields']
        self.float_component = meta.ref(self.rod, 'floatComponent')
        self.float_fields = meta.obj(self.float_component)['fields']
        self.read_line = make_line_reader(self.hwnd, self.client_rect)
        self.device = TimelineInput(TownWindow('心动小镇'))

        self.held = False
        self.casts = 0
        self.phase = 'idle'
        self.phase_at = time.monotonic()
        self.started = time.monotonic()
        self.log_path = self.out / 'autofish.jsonl'

    # ---------- 观测 ----------
    def _bool(self, fields, name):
        return bool(self.meta.data(self.rod + 16, 4096)[fields[name]['offset'] - 16])

    def state(self):
        raw = self.meta.data(self.float_component + 16, 4096)
        offset = self.float_fields['<State>k__BackingField']['offset'] - 16
        return struct.unpack_from('<i', raw, offset)[0]

    def can_throw(self):
        return self._bool(self.rod_fields, 'CanThrow')

    def grab(self):
        left, top, right, bottom = self.rect
        pic = ScreenSource.capture_window_background(self.hwnd, (left, top, right - left, bottom - top))
        if pic is None:
            return None
        return cv2.cvtColor(np.asarray(pic.convert('RGB')), cv2.COLOR_RGB2BGR)

    def fish_of(self, frame):
        if frame is None:
            return None
        shadows = [s for s in find_shadows(frame) if s.area >= FISH_MIN_AREA]
        return max(shadows, key=lambda s: s.area) if shadows else None

    # ---------- 动作 ----------
    def press(self):
        if not self.held:
            self.device.execute({'type': 'mouse_down', 'button': 'left'})
            self.held = True

    def release(self):
        if self.held:
            self.device.execute({'type': 'mouse_up', 'button': 'left'})
            self.held = False

    def keep_front(self):
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.25)
            if win32gui.GetForegroundWindow() != self.hwnd:
                bring_front(self.hwnd)
                time.sleep(0.1)
        except Exception:
            time.sleep(0.15)
        return win32gui.GetForegroundWindow() == self.hwnd

    # ---------- 日志 ----------
    def log(self, event, reason='', **extra):
        row = {'dt': round(time.monotonic() - self.started, 2), 'ev': event,
               'reason': reason, **extra}
        try:
            with self.log_path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except Exception:
            pass
        if self.verbose:
            print(json.dumps(row, ensure_ascii=False), flush=True)

    # ---------- 主循环 ----------
    def run(self):
        self.log_path.write_text('', encoding='utf-8')
        self.log('init', '自动钓鱼启动', pid=self.pid, duration=self.duration)
        try:
            while time.monotonic() - self.started < self.duration:
                self._tick()
        except KeyboardInterrupt:
            self.log('interrupt', '用户中断')
        except Exception as exc:
            self.log('error', f'{type(exc).__name__}: {exc}')
        finally:
            self.release()
            try:
                self.scene.close()
            except Exception:
                pass
            self.log('end', '结束', casts=self.casts, phase=self.phase)

    def _tick(self):
        state = self.state()
        can_throw = self.can_throw()
        now = time.monotonic()

        if self.phase == 'idle':
            if not can_throw:
                if state == 1:          # 钩还在飞
                    time.sleep(0.2)
                    return
                if not self.keep_front():
                    time.sleep(0.3)
                    return
                self.press()
                self.phase = 'retrieve'
                self.phase_at = now
                self.log('retrieve', '浮标未回收，按住收线', state=state)
                return
            first = self.grab()
            fish1 = self.fish_of(first)
            if fish1 is None:
                time.sleep(0.25)
                return
            time.sleep(0.18)
            fish2 = self.fish_of(self.grab())
            if fish2 is None:
                time.sleep(0.25)
                return
            moved = ((fish2.cx - fish1.cx) ** 2 + (fish2.cy - fish1.cy) ** 2) ** 0.5
            if moved < FISH_MIN_MOVE:
                time.sleep(0.2)
                return
            if not self.keep_front():
                time.sleep(0.3)
                return
            self.press()
            self.phase = 'aiming'
            self.phase_at = now
            self.log('press', f'活鱼 area={fish2.area} 中心=({round(fish2.cx)},{round(fish2.cy)}) '
                              f'移动={moved:.1f}px')
            return

        if self.phase == 'aiming':
            if now - self.phase_at < 0.45:      # 瞄准态需要一点时间才置位
                time.sleep(0.05)
                return
            if not self.keep_front():
                self.release()
                self.phase = 'idle'
                self.log('abort_aim', '失焦，放弃本次')
                return
            frame = self.grab()
            fish = self.fish_of(frame)
            ring = find_ring(frame) if frame is not None else None
            if ring is not None and not (380 <= ring.cx <= 980 and 180 <= ring.cy <= 395):
                ring = None                      # 越界视为误检
            if fish is None:
                self.release()
                self.phase = 'idle'
                self.log('abort_aim', '瞄准时鱼影丢失')
                return
            anchor_x, anchor_y = (ring.cx, ring.cy) if ring else AIM_ANCHOR
            dx = fish.cx - anchor_x
            dy = fish.cy - anchor_y
            mx = int(np.clip(dx / AIM_GAIN_X, -420, 420))
            my = int(np.clip(dy / AIM_GAIN_Y, -300, 300))
            if abs(mx) >= 2 or abs(my) >= 2:
                move_mouse(mx, my)
            self.release()                       # 立刻抛竿，等回弹就晚了
            self.casts += 1
            self.phase = 'waiting'
            self.phase_at = now
            self.log('cast', f'抛竿#{self.casts} 环=({round(anchor_x)},{round(anchor_y)}) '
                             f'鱼=({round(fish.cx)},{round(fish.cy)}) '
                             f'偏差=({dx:.0f},{dy:.0f}) 注入=({mx},{my})')
            if frame is not None:
                cv2.imwrite(str(self.out / f'cast_{self.casts:02d}.png'), frame)
            return

        if self.phase == 'retrieve':
            if can_throw and state == 0:
                self.release()
                self.phase = 'idle'
                self.log('retrieved', '已收回', state=state)
            elif now - self.phase_at > 8.0:
                self.release()
                self.phase = 'idle'
                self.log('retrieve_timeout', '收线超时', state=state)
            time.sleep(0.09)
            return

        if self.phase == 'waiting':
            if state == 6:
                if not self.keep_front():
                    time.sleep(0.2)
                    return
                self.press()
                self.phase = 'hooked'
                self.phase_at = now
                self.log('hook', '咬钩 State=6，长按收线', color=self.read_line() or {})
            elif state == 0 and can_throw and now - self.phase_at > 1.5:
                self.phase = 'idle'
                self.log('cast_miss', '抛竿未生效，重试')
            elif now - self.phase_at > policy.FISHING_RECAST_SECONDS:
                if not self.keep_front():
                    time.sleep(0.2)
                    return
                self.press()
                self.phase = 'retrieve'
                self.phase_at = now
                self.log('recast', '久未咬钩，收线重来', state=state)
            time.sleep(0.07)
            return

        if self.phase == 'hooked':
            color = self.read_line() or {}
            if not self.keep_front():
                time.sleep(0.15)
                return
            if state == 0:
                self.release()
                self.phase = 'idle'
                self.phase_at = time.monotonic()
                self.log('land', '拉扯结束 State=0，松手')
            elif color.get('red', 0) >= policy.FISHING_LINE_RED_PIXELS and self.held:
                self.release()
                self.log('slack', f"线红 {color['red']}px，松手放线", color=color)
            elif not self.held and color.get('dom') in ('white', 'yellow'):
                self.press()
                self.log('rehold', '张力恢复，继续收线', color=color)
            elif now - self.phase_at > policy.FISHING_HOOK_TIMEOUT_SECONDS:
                self.release()
                self.phase = 'idle'
                self.log('hook_timeout', '咬钩超时，松手')
            time.sleep(0.05)
            return

        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description='自动钓鱼实机运行器（视觉闭环瞄准）')
    parser.add_argument('--duration', type=float, default=600.0, help='总运行秒数')
    parser.add_argument('--out', type=str, default=None, help='日志与截图目录')
    args = parser.parse_args()
    FishingRunner(duration=args.duration, out_dir=args.out).run()


if __name__ == '__main__':
    main()
