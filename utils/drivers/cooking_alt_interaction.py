"""Own only the cooking task's ALT; release left mouse before modifiers."""
from contextlib import contextmanager
import win32api


def release_cooking_inputs(device):
    if 'left' in getattr(device,'buttons',set()):
        device.execute({'type':'mouse_up','button':'left'})
    device.release_all()


@contextmanager
def cooking_alt(device,window,guard,wait,report,alt_pressed=None):
    pressed=alt_pressed or (lambda:bool(win32api.GetAsyncKeyState(0x12)&0x8000))
    guard()
    if pressed():
        raise ValueError('玩家已持有 ALT，未接管或释放玩家按键，未执行翻锅点击')
    try:
        # TimelineInput records the key before injection, including failures.
        device.execute({'type':'key_down','key':'alt_l'})
        report('stir_alt_down','翻锅交互已按下程序持有的 ALT')
        wait(.05)
        pointer=window.pointer()
        if len(pointer)!=3 or not pointer[2]:
            raise ValueError('按住 ALT 后指针仍未确认可见，未执行翻锅点击')
        yield
    finally:
        release_cooking_inputs(device)
        report('stir_alt_released','翻锅交互输入已释放：先左键，后程序持有的 ALT')
