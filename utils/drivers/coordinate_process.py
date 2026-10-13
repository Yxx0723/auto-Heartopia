"""Discover xdt.exe without touching keyboard, mouse or process memory."""
import ctypes
from ctypes import wintypes


class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


def find_game_processes():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), '无法读取游戏进程列表')
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        pids = []
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not more and ctypes.get_last_error() != 18:
            raise OSError(ctypes.get_last_error(), '无法读取游戏进程列表')
        while more:
            if entry.szExeFile.lower() == 'xdt.exe':
                pids.append(entry.th32ProcessID)
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise OSError(ctypes.get_last_error(), '游戏进程列表读取中断')
        return pids
    finally:
        kernel.CloseHandle(handle)


def show_monitor_error(message):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user.MessageBoxW.restype = ctypes.c_int
    user.MessageBoxW(None, str(message), '心动小镇 · 坐标监视器', 0x10)
