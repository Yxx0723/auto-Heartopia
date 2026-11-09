"""Cooking-only passive observations. No click, cursor-show or focus APIs."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import math
import win32api
import win32gui
import win32process


def attempt(call):
    try:return call()
    except Exception as exc:return {'state':'unavailable','error':type(exc).__name__+': '+str(exc)}


class GuiThreadInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),
              *[(name,wintypes.HWND) for name in ('hwndActive','hwndFocus','hwndCapture',
                 'hwndMenuOwner','hwndMoveSize','hwndCaret')],('rcCaret',wintypes.RECT)]


def capture_state(hwnd):
    def capture():
        user=ctypes.WinDLL('user32',use_last_error=True)
        fn=user.GetGUIThreadInfo;fn.argtypes=[wintypes.DWORD,ctypes.POINTER(GuiThreadInfo)];fn.restype=wintypes.BOOL
        info=GuiThreadInfo();info.cbSize=ctypes.sizeof(info)
        if not fn(win32process.GetWindowThreadProcessId(hwnd)[0],ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'target_thread_capture':info.hwndCapture or 0,'scope':'game GUI thread; not proof of game event consumption'}
    def dpi():
        user=ctypes.WinDLL('user32',use_last_error=True)
        fn=user.GetDpiForWindow;fn.argtypes=[wintypes.HWND];fn.restype=wintypes.UINT
        value=fn(hwnd)
        if not value:raise ValueError('GetDpiForWindow returned zero')
        return value/96.
    return {'timestamp':datetime.now().astimezone().isoformat(),
            'foreground_window':attempt(win32gui.GetForegroundWindow),
            'window_handle':hwnd,'window_client_rect':attempt(lambda:win32gui.GetClientRect(hwnd)),
            'client_screen_origin':attempt(lambda:win32gui.ClientToScreen(hwnd,(0,0))),
            'cursor_screen':attempt(win32gui.GetCursorPos),
            'cursor_info':attempt(win32gui.GetCursorInfo),'dpi_scale':attempt(dpi),
            'virtual_screen':attempt(lambda:[win32api.GetSystemMetrics(i) for i in (76,77,78,79)]),
            'mouse_capture_state':attempt(capture),
            'game_received_input':{'state':'unavailable','reason':'Windows observations do not confirm game input consumption'}}


def begin_move(window,point):
    value=capture_state(window.hwnd)
    value.update(cursor_target=list(point),cursor_before=attempt(window.pointer),
                 set_cursor_pos_result={'state':'not_called','reason':'existing path uses SendInput'},
                 send_input_result={'state':'unavailable','reason':'not observed yet'},failure_stage=[])
    value['cursor_target_screen']=attempt(lambda:win32gui.ClientToScreen(window.hwnd,point))
    return value


def execute_move(device,event,diagnostic):
    """Forward precisely one existing move; capture SendInput's original return."""
    native=getattr(device,'native',None);original=getattr(native,'send',None)
    if original is None:
        diagnostic['send_input_result']={'state':'unavailable','reason':'device does not expose native SendInput'}
        return device.execute(event)
    def observed(count,events,size):
        result=original(count,events,size);error=ctypes.get_last_error()
        diagnostic['send_input_result']={'requested':count,'inserted':result,'last_error':error}
        ctypes.set_last_error(error)
        return result
    native.send=observed
    try:return device.execute(event)
    except Exception as exc:
        diagnostic['move_exception']=type(exc).__name__+': '+str(exc)
        raise
    finally:native.send=original


def classify(value,size):
    reasons=[];target=value['cursor_target'];after=value.get('cursor_after')
    if not 0<=target[0]<size[0] or not 0<=target[1]<size[1]:reasons.append('target_outside_client')
    if value.get('foreground_window')!=value.get('window_handle'):reasons.append('window_not_foreground')
    result=value.get('send_input_result',{})
    if 'inserted' in result and result['inserted']!=result['requested']:reasons.append('send_input_failed')
    if isinstance(after,(tuple,list)) and len(after)==3:
        value['cursor_visible']=bool(after[2]);value['cursor_delta']=math.dist(after[:2],target)
        if not after[2]:reasons.append('cursor_hidden')
        if value['cursor_delta']>2:reasons.append('position_mismatch')
    else:
        value['cursor_visible']={'state':'unavailable'};value['cursor_delta']={'state':'unavailable'}
        reasons.append('pointer_read_unavailable')
    origin=value.get('client_screen_origin');screen=value.get('cursor_target_screen')
    if isinstance(origin,(tuple,list)) and isinstance(screen,(tuple,list)):
        if tuple(screen)!=(origin[0]+target[0],origin[1]+target[1]):reasons.append('coordinate_conversion_mismatch')
    value['failure_stage']=reasons or ['no_windows_level_failure_observed']
    return value


def finish_move(window,point,pointer,diagnostic,size):
    diagnostic['cursor_after']=list(pointer)
    diagnostic['after_environment']=capture_state(window.hwnd)
    diagnostic['foreground_window']=diagnostic['after_environment']['foreground_window']
    diagnostic['timestamp_after']=diagnostic['after_environment']['timestamp']
    return classify(diagnostic,size)
