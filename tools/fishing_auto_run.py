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
from utils.core.fishing_log import append_fishing_event, fishing_event, format_fishing_event
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
AIM_REFERENCE_SIZE = (1382, 807)
#: 鱼影面积下限：排除木栈道/水花等小暗块
FISH_MIN_AREA = 1200
#: 活体判定：两帧中心位移下限（像素）
FISH_MIN_MOVE = 3.0
FISH_TRACK_MIN_AREA = 600
FISH_TRACK_RADIUS = 55.0
AIM_SETTLE_SECONDS = 0.45
AIM_HOLD_SECONDS = 5.0
AIM_SAMPLE_SECONDS = 0.20
AIM_TARGET_MAX_AGE = 0.75


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
    if _USER32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)) != 1:
        raise OSError('无法发送瞄准输入')


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
    def __init__(self, duration=600.0, out_dir=None, verbose=True, *, profile=None,
                 hwnd=None, stop_requested=None, on_event=None):
        self.stop_requested = stop_requested or (lambda: False)
        self.on_event = on_event
        self.require_foreground = hwnd is not None
        self.duration = float(duration)
        self.out = Path(out_dir) if out_dir else ROOT / 'fishing-runs'
        self.out.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.hwnd = hwnd if hwnd is not None else find_game()
        if not self.require_foreground and win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            time.sleep(0.8)
        _, self.pid = win32process.GetWindowThreadProcessId(self.hwnd)
        self.rect = win32gui.GetWindowRect(self.hwnd)
        client = win32gui.ClientToScreen(self.hwnd, (0, 0))
        width, height = win32gui.GetClientRect(self.hwnd)[2:]
        self.client_rect = (client[0], client[1], width, height)
        if not self.require_foreground:
            bring_front(self.hwnd)
            time.sleep(0.4)
        self.check_input()

        self.scene = MemorySceneMap(self.pid, profile or ROOT / 'webui/coordinate-monitor/profile.json')
        try:
            self.bind_rod()
            self.read_line = make_line_reader(self.hwnd, self.client_rect)
            self.device = TimelineInput(TownWindow('心动小镇'))
        except BaseException:
            self.scene.close()
            raise

        self.held = False
        self.casts = 0
        self.phase = 'idle'
        self.phase_at = time.monotonic()
        self.started = time.monotonic()
        self.log_path = self.out / 'autofish.jsonl'
        self.last_game_state = None

    def bind_rod(self):
        meta = self.scene.meta
        trace = self.scene.memory.snapshot()['paths'][0]['trace']
        entity = next(t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField')
        self.lpc = next(p for p in meta.dictionary(meta.ref(entity, '_components'), max_items=128)
                        if meta.obj(p)['name'] == 'LocalPlayerComponent')
        self.meta = meta
        self.rod = meta.ref(self.lpc, '_interactTool')
        if not self.rod:
            raise ValueError('请先拿出鱼竿')
        rod = meta.obj(self.rod)
        if rod['name'] != 'HandHoldFishingRod':
            raise ValueError('请先拿出鱼竿')
        self.rod_fields = rod['fields']
        self.float_component = meta.ref(self.rod, 'floatComponent')
        if not self.float_component:
            raise ValueError('当前鱼竿没有可读取的浮标')
        self.float_fields = meta.obj(self.float_component)['fields']

    def check_input(self):
        if self.stop_requested():
            raise InterruptedError('已停止自动钓鱼')
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise InterruptedError('游戏失去前台，已停止自动钓鱼')

    # ---------- 观测 ----------
    def _bool(self, fields, name):
        return bool(self.meta.data(self.rod + 16, 4096)[fields[name]['offset'] - 16])

    def state(self):
        if (self.meta.ref(self.lpc, '_interactTool') != self.rod
                or self.meta.ref(self.rod, 'floatComponent') != self.float_component):
            raise ValueError('鱼竿或浮标已更换，请重新启动钓鱼')
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

    def fish_of(self, frame, previous=None):
        if frame is None:
            return None
        minimum = FISH_MIN_AREA if previous is None else FISH_TRACK_MIN_AREA
        shadows = find_shadows(frame, min_area=minimum)
        if not shadows:
            return None
        if previous is None:
            return shadows[0]
        height, width = frame.shape[:2]
        scale_x = width / AIM_REFERENCE_SIZE[0]
        scale_y = height / AIM_REFERENCE_SIZE[1]

        def distance(shadow):
            return np.hypot((shadow.cx - previous.cx) / scale_x,
                            (shadow.cy - previous.cy) / scale_y)

        nearest = min(shadows, key=distance)
        return nearest if distance(nearest) <= FISH_TRACK_RADIUS else None

    # ---------- 动作 ----------
    def press(self):
        if not self.held:
            self.check_input()
            self.held = True
            self.device.execute({'type': 'mouse_down', 'button': 'left'})
            self.log('button', '已发送：按住鼠标左键')

    def release(self, *, report=True):
        if self.held:
            self.device.execute({'type': 'mouse_up', 'button': 'left'})
            self.held = False
            if report:
                self.log('button', '已发送：松开鼠标左键')

    def keep_front(self):
        if self.require_foreground or self.held:
            return win32gui.GetForegroundWindow() == self.hwnd
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
        row = fishing_event(event, reason, dt=round(time.monotonic() - self.started, 2), **extra)
        try:
            with self.log_path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except Exception:
            pass
        append_fishing_event(self.out / '钓鱼日志.txt', row)
        if self.verbose:
            print(format_fishing_event(row)[0], end='', flush=True)
        if self.on_event is not None:
            self.on_event(row)

    # ---------- 主循环 ----------
    def run(self):
        try:
            self.log_path.write_text('', encoding='utf-8')
            self.log('init', '自动钓鱼启动', pid=self.pid, duration=self.duration)
            while not self.stop_requested() and time.monotonic() - self.started < self.duration:
                if self.require_foreground:
                    self.check_input()
                self._tick()
        except (KeyboardInterrupt, InterruptedError) as exc:
            self.log('interrupt', str(exc) or '用户中断')
        except Exception as exc:
            self.log('error', f'{type(exc).__name__}: {exc}')
            raise
        finally:
            try:
                try:
                    self.release()
                except Exception:
                    self.device.release_all()
                    self.held = False
                    raise
            finally:
                self.scene.close()
                self.log('end', '自动钓鱼已结束', casts=self.casts, phase=self.phase)

    def _tick(self):
        state = self.state()
        if state != self.last_game_state:
            self.last_game_state = state
            description = {0: '空闲／拉扯结束', 1: '浮标飞行中', 2: '浮标已入水，等待咬钩',
                           6: '鱼已咬钩，正在拉扯'}.get(state, f'其他浮标状态（{state}）')
            self.log('game_state', description, state=state)
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
            second = self.grab()
            fish2 = self.fish_of(second, fish1)
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
            self.aim_fish = fish2
            self.aim_frame_shape = second.shape[:2]
            self.log('fish_detected', f'确认移动鱼影，中心（{fish2.cx:.0f}, {fish2.cy:.0f}），'
                     f'两帧间移动 {moved:.1f} 像素')
            self.press()
            self.phase = 'aiming'
            self.phase_at = time.monotonic()
            self.aim_seen_at = self.phase_at
            self.aim_sample_at = self.phase_at
            self.log('press', f'开始瞄准，保留 {AIM_HOLD_SECONDS:g} 秒观察时间')
            return

        if self.phase == 'aiming':
            elapsed = now - self.phase_at
            if elapsed < AIM_SETTLE_SECONDS:
                time.sleep(0.05)
                return
            if not self.keep_front():
                self.release()
                self.phase = 'idle'
                self.log('abort_aim', '失焦，放弃本次')
                return
            preview = elapsed < AIM_HOLD_SECONDS
            if preview and now - self.aim_sample_at < AIM_SAMPLE_SECONDS:
                time.sleep(0.04)
                return
            self.aim_sample_at = now
            frame = self.grab()
            matching_frame = frame is not None and frame.shape[:2] == self.aim_frame_shape
            fish = self.fish_of(frame, self.aim_fish) if matching_frame else None
            if fish is not None:
                self.aim_fish = fish
                self.aim_seen_at = now
            if preview and fish is None:
                self.log('aim_observe', f'{elapsed:.1f} 秒：暂未重新识别到同一条鱼影，继续观察')
                return
            if not matching_frame or now - self.aim_seen_at > AIM_TARGET_MAX_AGE:
                self.release()
                self.phase = 'idle'
                self.log('abort_aim', '无法取得有效瞄准画面' if not matching_frame else '鱼影已丢失，未校准抛竿')
                return
            target_source = '跟踪' if fish is not None else '最近一次确认'
            fish = self.aim_fish
            height, width = frame.shape[:2]
            sx, sy = width / AIM_REFERENCE_SIZE[0], height / AIM_REFERENCE_SIZE[1]
            ring = find_ring(
                frame, x_range=(380 * sx, 980 * sx), y_range=(180 * sy, 395 * sy),
                min_radius=max(1, round(13 * sy)), max_radius=max(2, round(42 * sy)),
            )
            if preview and ring is None:
                self.log('aim_observe', f'{elapsed:.1f} 秒：鱼影（{fish.cx:.0f}, {fish.cy:.0f}），落点圈未识别，暂不移动')
                return
            anchor_x, anchor_y = (ring.cx, ring.cy) if ring else (AIM_ANCHOR[0] * sx, AIM_ANCHOR[1] * sy)
            dx, dy = fish.cx - anchor_x, fish.cy - anchor_y
            mx = int(np.clip(dx / (AIM_GAIN_X * sx), -420, 420))
            my = int(np.clip(dy / (AIM_GAIN_Y * sy), -300, 300))
            anchor_source = '落点圈' if ring else '预设锚点'
            self.log('aim_observe', f'{elapsed:.1f} 秒：鱼影（{fish.cx:.0f}, {fish.cy:.0f}，{target_source}），'
                     f'{anchor_source}（{anchor_x:.0f}, {anchor_y:.0f}），画面偏差（{dx:+.0f}, {dy:+.0f}）像素')
            moved = abs(mx) >= 2 or abs(my) >= 2
            self.check_input()
            if moved:
                move_mouse(mx, my)
            if not preview:
                # The aim marker recenters quickly. Send the release before doing any log I/O.
                self.release(report=False)
            if moved:
                horizontal = f'向右 {mx}' if mx > 0 else f'向左 {-mx}' if mx < 0 else '水平不动'
                vertical = f'向下 {my}' if my > 0 else f'向上 {-my}' if my < 0 else '垂直不动'
                self.log('move', f'已发送：鼠标相对移动，{horizontal}、{vertical}', dx=mx, dy=my)
            else:
                self.log('aim_observe', '偏差低于移动阈值，本帧没有发送鼠标移动')
            if preview:
                return
            self.log('button', '已发送：松开鼠标左键')
            self.casts += 1
            self.phase = 'waiting'
            self.phase_at = time.monotonic()
            self.log('cast', f'已发送第 {self.casts} 次抛竿，等待游戏反馈',
                     target_source=target_source, anchor_source=anchor_source)
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
                self.log('hook', '游戏报告咬钩，开始长按收线', color=self.read_line() or {})
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
            if not self.keep_front():
                self.release()
                self.phase = 'idle'
                self.log('interrupt', '游戏失去前台，已松开左键')
                return
            if state == 0:
                self.release()
                self.phase = 'idle'
                self.phase_at = time.monotonic()
                self.log('land', '拉扯结束，已松开左键；鱼是否入包未确认')
            elif now - self.phase_at > policy.FISHING_HOOK_TIMEOUT_SECONDS:
                self.release()
                self.phase = 'idle'
                self.log('hook_timeout', '咬钩超时，松手')
            else:
                self.adjust_tension()
            time.sleep(0.05)
            return

        time.sleep(0.1)

    def adjust_tension(self):
        color = self.read_line() or {}
        if color.get('red', 0) >= policy.FISHING_LINE_RED_PIXELS:
            if self.held:
                self.release()
                self.log('slack', f"线红 {color['red']}px，松手放线", color=color)
        elif not self.held and color.get('dom') in ('white', 'yellow'):
            self.press()
            self.log('rehold', '张力恢复，继续收线', color=color)


def main():
    parser = argparse.ArgumentParser(description='自动钓鱼实机运行器（视觉闭环瞄准）')
    parser.add_argument('--duration', type=float, default=600.0, help='总运行秒数')
    parser.add_argument('--out', type=str, default=None, help='日志与截图目录')
    args = parser.parse_args()
    FishingRunner(duration=args.duration, out_dir=args.out).run()


if __name__ == '__main__':
    main()
