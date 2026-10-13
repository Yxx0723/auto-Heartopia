"""Stable physical key names shared by capture, validation and playback."""
import string

CONTROL_KEYS = {'f6', 'f9', 'f10'}
KEY_VK = {
    **{c: ord(c.upper()) for c in string.ascii_lowercase + string.digits},
    **{f'f{i}': 0x6f + i for i in range(1, 25)},
    **{f'numpad_{i}': 0x60 + i for i in range(10)},
    'backspace': 8, 'tab': 9, 'enter': 13, 'numpad_enter': 13, 'pause': 19,
    'caps_lock': 20, 'esc': 27, 'space': 32, 'page_up': 33, 'page_down': 34,
    'end': 35, 'home': 36, 'left': 37, 'up': 38, 'right': 39, 'down': 40,
    'print_screen': 44, 'insert': 45, 'delete': 46, 'cmd': 91, 'cmd_l': 91, 'cmd_r': 92,
    'menu': 93, 'num_lock': 144, 'scroll_lock': 145,
    'shift': 160, 'shift_l': 160, 'shift_r': 161, 'ctrl': 162, 'ctrl_l': 162,
    'ctrl_r': 163, 'alt': 164, 'alt_l': 164, 'alt_r': 165, 'alt_gr': 165,
    'multiply': 106, 'add': 107, 'separator': 108, 'subtract': 109, 'decimal': 110, 'divide': 111,
    'semicolon': 186, 'equal': 187, 'comma': 188, 'minus': 189, 'period': 190,
    'slash': 191, 'backtick': 192, 'bracket_left': 219, 'backslash': 220,
    'bracket_right': 221, 'quote': 222, 'oem_102': 226,
}
EXTENDED_KEYS = {'numpad_enter', 'ctrl_r', 'alt_r', 'alt_gr', 'cmd', 'cmd_l', 'cmd_r',
                 'menu', 'insert', 'delete', 'home', 'end', 'page_up', 'page_down',
                 'left', 'right', 'up', 'down', 'divide', 'num_lock', 'print_screen'}
MODIFIERS = {'shift', 'shift_l', 'shift_r', 'ctrl', 'ctrl_l', 'ctrl_r', 'alt', 'alt_l',
             'alt_r', 'alt_gr', 'cmd', 'cmd_l', 'cmd_r'}


def ordered_keys(keys):
    return sorted(keys, key=lambda key: (key not in MODIFIERS, key))


def key_from_vk(vk, scan=0, extended=False):
    if vk in (16, 160, 161):
        return 'shift_r' if scan == 54 or vk == 161 else 'shift_l'
    if vk in (17, 162, 163):
        return 'ctrl_r' if extended or vk == 163 else 'ctrl_l'
    if vk in (18, 164, 165):
        return 'alt_r' if extended or vk == 165 else 'alt_l'
    if vk == 13 and extended:
        return 'numpad_enter'
    return next((name for name, value in KEY_VK.items() if value == vk), '')
