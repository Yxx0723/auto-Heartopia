"""Foreground-only input recording. No listener or device IO at import time."""
import threading
import time

from pynput import keyboard, mouse
import win32api
import win32con
import win32gui


def move_relative(dx):
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, int(dx), 0, 0, 0)


def record_session(session, window, on_start=None):
    lock = threading.RLock()
    failure = []
    pressed = set()

    def foreground():
        return win32gui.GetForegroundWindow() == window.hwnd

    def guard():
        if session.active and not foreground():
            raise ValueError('游戏失去前台，本次录制停止且未保存。请重录；切换窗口前先按F6暂停。')

    def key_name(key):
        char = getattr(key, 'char', None)
        return char.lower() if char else getattr(key, 'name', str(key))

    def press(key):
        with lock:
            name = key_name(key)
            if name in pressed:
                return
            pressed.add(name)
            try:
                now = time.perf_counter()
                if name == 'f9':
                    guard()
                    session.finish(now)
                    return False
                if name == 'f6':
                    if not session.active and not foreground():
                        print('请切回游戏后按F6开始录制', flush=True)
                        return
                    guard()
                    if not session.steps and not session.active and on_start:
                        on_start()
                    active = session.toggle(time.perf_counter())
                    print('录制中' if active else '已暂停；F6继续，F9保存', flush=True)
                    return
                guard()
                if not session.active:
                    return
                if name.startswith(('shift', 'ctrl', 'alt')):
                    raise ValueError('首版录制不支持疾跑或组合键，本次未保存；请不按Shift逐键录制。')
                if name in {'w', 'a', 's', 'd', 'v'}:
                    session.press(name, now)
                elif name in {'x', 'e', 'f', 'r', 'space', 'esc', 'left', 'right'}:
                    session.action(name, now)
                    if name in {'left', 'right'}:
                        move_relative(-200 if name == 'left' else 200)
                    elif name == 'x':
                        # X is a recording command: initiate one ordinary attack at
                        # the game centre and omit the subsequent battle duration.
                        left, top, right, bottom = window.get_rect()
                        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
                        try:
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                        finally:
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        print('已标记战斗并暂停；战斗结束按F6继续', flush=True)
            except Exception as exc:
                failure.append(exc)
                return False

    def release(key):
        with lock:
            name = key_name(key)
            pressed.discard(name)
            try:
                guard()
                session.release(name, time.perf_counter())
            except Exception as exc:
                failure.append(exc)
                return False

    def click(x, y, button, down):
        with lock:
            try:
                guard()
                if not session.active or not down:
                    return
                if button != mouse.Button.left:
                    raise ValueError('首版录制不支持右键疾跑；本次未保存。')
                session.action('click', time.perf_counter())
            except Exception as exc:
                failure.append(exc)
                keys.stop()
                return False

    keys = keyboard.Listener(on_press=press, on_release=release)
    clicks = mouse.Listener(on_click=click)
    try:
        clicks.start()
        keys.start()
        while keys.is_alive():
            keys.join(0.2)
            with lock:
                try:
                    guard()
                except Exception as exc:
                    failure.append(exc)
                    keys.stop()
    finally:
        keys.stop()
        clicks.stop()
        keys.join()
        clicks.join()
    if failure:
        raise failure[0]
