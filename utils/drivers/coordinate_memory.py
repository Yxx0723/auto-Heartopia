"""Windows position access. Query/VM_READ only; no writes, debug or injection."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
from pathlib import Path
import struct

from utils.core.coordinate_profile import validate_profile
from utils.core.coordinate_route import valid_position
from utils.core.thresholds import COORD_CONSENSUS
from utils.drivers.mono_position import MonoPositionLocator


class ModuleInfo(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('size', wintypes.DWORD), ('entry', ctypes.c_void_p)]


def position_snapshot(read, base, paths):
    resolved = []
    cache = {}

    def once(address, size):
        # Shared pointer nodes and the shared TRS are read once per sample.
        # Otherwise a moving character can disagree with its earlier self.
        key = (address, size)
        if key not in cache:
            cache[key] = read(address, size)
        return cache[key]

    for path in paths:
        address = base + path['rva']
        trace = []
        for offset in path['offsets']:
            data = once(address, 8)
            pointer = int.from_bytes(data, 'little') if data is not None and len(data) == 8 else 0
            if not 0x10000 <= pointer < 0x7fffffffffff:
                break
            trace.append({'read_at': address, 'pointer': pointer, 'offset': offset})
            address = pointer + offset
        else:
            data = once(address, 12)
            if data is not None and len(data) == 12:
                position = struct.unpack('<fff', data)
                if valid_position(position):
                    resolved.append({'rva': path['rva'], 'offsets': list(path['offsets']),
                                     'root_address': base + path['rva'], 'address': address,
                                     'position': position, 'trace': trace})
    if len(resolved) < 2:
        raise ValueError('角色坐标无法读取，请确认已进入游戏；场景变化后可能需要重新定位')
    position = resolved[0]['position']
    if any(math.dist(p['position'], position) > COORD_CONSENSUS for p in resolved[1:]):
        raise ValueError('坐标指针读数不一致，已停止，不能继续回放')
    return {'position': position, 'address': resolved[0]['address'],
            'module_base': base, 'valid_paths': len(resolved),
            'configured_paths': len(paths), 'paths': resolved}


def consensus_position(read, base, paths):
    return position_snapshot(read, base, paths)['position']


class ReadOnlyPosition:
    def __init__(self, pid, profile_file, hwnd=None):
        self.handle = None
        self.pid, self.hwnd = pid, hwnd
        self.tool_reader = None
        self.stamina_reader = None
        self.profile = json.loads(Path(profile_file).read_text(encoding='utf-8'))
        errors = validate_profile(self.profile)
        if errors:
            raise ValueError('；'.join(errors))
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.ReadProcessMemory.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x410, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), '无法只读访问游戏，请以管理员权限运行 MyRail')
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, name, ctypes.byref(length)) \
                    or Path(name.value).name.lower() != 'xdt.exe':
                raise ValueError('当前窗口不属于心动小镇 xdt.exe')
            self.configure_locator()
            self.read()
        except Exception:
            self.close()
            raise

    def configure_locator(self):
        self.locator = None
        semantic = self.profile['schema'] == 'myrail-position-mono-v1'
        module_name = 'mono-2.0-sgen.dll' if semantic else 'UnityPlayer.dll'
        self.base, size, module_file = self._module(module_name)
        self._fingerprint(module_file, self.profile['sha256'], self.profile['file_size'])
        if semantic:
            if size < 0x4d3888:
                raise ValueError('游戏运行时模块范围不匹配')
            _, _, unity_file = self._module('UnityPlayer.dll')
            self._fingerprint(unity_file, self.profile['unity_player_sha256'],
                              self.profile['unity_player_file_size'])
        elif any(path['rva'] + 8 > size for path in self.profile['paths']):
            raise ValueError('坐标入口超出游戏模块范围')
        _, _, assembly_file = self._module('GameAssembly.dll')
        self._fingerprint(assembly_file, self.profile['game_assembly_sha256'],
                          self.profile['game_assembly_file_size'])
        if semantic:
            self.locator = MonoPositionLocator(self._read, self.base)

    @staticmethod
    def window_pid(hwnd):
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowThreadProcessId.restype = wintypes.DWORD
        pid = wintypes.DWORD()
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise ValueError('游戏窗口已经关闭')
        return pid.value

    @classmethod
    def from_window(cls, window, profile_file):
        return cls(cls.window_pid(window.hwnd), profile_file, window.hwnd)

    @staticmethod
    def _fingerprint(file, expected_hash, expected_size):
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != expected_hash or file.stat().st_size != expected_size:
            raise ValueError('游戏版本与坐标配置不匹配，需要重新定位坐标')

    def _module(self, module_name):
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        psapi.EnumProcessModulesEx.restype = wintypes.BOOL
        psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                              ctypes.POINTER(ModuleInfo), wintypes.DWORD]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        modules, needed = (wintypes.HMODULE * 2048)(), wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(self.handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3) \
                or needed.value > ctypes.sizeof(modules):
            raise ValueError('无法枚举游戏模块')
        for module in list(modules)[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            name = ctypes.create_unicode_buffer(1024)
            if not psapi.GetModuleBaseNameW(self.handle, module, name, len(name)) \
                    or name.value.lower() != module_name.lower():
                continue
            info, file = ModuleInfo(), ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleInformation(self.handle, module, ctypes.byref(info), ctypes.sizeof(info)):
                break
            length = psapi.GetModuleFileNameExW(self.handle, module, file, len(file))
            if not 0 < length < len(file) - 1:
                break
            return info.base, info.size, Path(file.value)
        raise ValueError('游戏模块未加载，请进入角色后再开始')

    def _read(self, address, size):
        data, count = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not self.kernel.ReadProcessMemory(self.handle, address, data, size, ctypes.byref(count)) \
                or count.value != size:
            return None
        return data.raw

    def read(self):
        return self.snapshot()['position']

    def movement_basis(self):
        """Version-pinned read-only local camera direction; never sends input."""
        if self.locator is None:
            raise ValueError('当前坐标版本不支持只读方向检测')
        from utils.drivers.movement_basis import MovementBasisReader
        try:
            if getattr(self,'movement_reader',None) is None:
                self.movement_reader=MovementBasisReader(self._read,self.locator)
            return self.movement_reader.snapshot()
        except (OSError,ValueError):
            self.movement_reader=None
            raise

    def tools_snapshot(self):
        return self._tools_snapshot(held=False)

    def stamina_snapshot(self):
        if self.locator is None:
            raise ValueError('当前坐标版本不支持体力检测')
        from utils.drivers.stamina import StaminaReader
        try:
            if self.stamina_reader is None:
                self.stamina_reader = StaminaReader(self._read, self.locator)
            return self.stamina_reader.snapshot()
        except (ValueError, OSError):
            self.stamina_reader = None
            raise

    def held_tool_snapshot(self):
        return self._tools_snapshot(held=True)

    def food_inventory_snapshot(self, check=lambda: None):
        if self.locator is None:
            raise ValueError('当前坐标版本不支持背包检测')
        from utils.drivers.food_inventory import FoodInventoryReader
        if getattr(self, 'food_inventory_reader', None) is None:
            self.food_inventory_reader = FoodInventoryReader(self)
        return self.food_inventory_reader.snapshot(check)

    def backpack_counts_snapshot(self):
        """Read-only owned stock totals; shares the validated food metadata reader."""
        if self.locator is None:
            raise ValueError('当前坐标版本不支持背包检测')
        from utils.drivers.food_inventory import FoodInventoryReader
        if getattr(self, 'food_inventory_reader', None) is None:
            self.food_inventory_reader = FoodInventoryReader(self)
        return self.food_inventory_reader.counts_snapshot()

    def food_search_query(self):
        if getattr(self, 'food_inventory_reader', None) is None:
            raise ValueError('尚未读取当前背包')
        return self.food_inventory_reader.search_query()

    def _tools_snapshot(self, held):
        if self.locator is None:
            raise ValueError('当前坐标版本不支持工具耐久检测')
        # Lazy import avoids tying the coordinate reader's startup to optional
        # tool metadata, and preserves its existing read-only process handle.
        from utils.drivers.tool_durability import ToolDurabilityReader
        try:
            if self.tool_reader is None:
                self.tool_reader = ToolDurabilityReader(self._read, self.base, self.locator.domain)
            return self.tool_reader.snapshot(held=held)
        except (OSError, ValueError):
            self.tool_reader = None
            raise

    def snapshot(self):
        if self.hwnd and self.window_pid(self.hwnd) != self.pid:
            raise ValueError('游戏进程已更换，请重新启动录制或回放')
        if self.locator is not None:
            return self.locator.snapshot()
        return position_snapshot(self._read, self.base, self.profile['paths'])

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
