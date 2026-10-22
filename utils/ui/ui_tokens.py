"""Design tokens for the native monitor UI: colour, spacing, type and control size.

The palette is a forest HUD: near-black forest surfaces, moss-lime rules, warm
amber for warnings, monospaced numerals. It keeps the same temperature as
`map_presentation.MAP_STYLE` (v2 green cartography), so the window chrome and
the map no longer meet as cyan-on-forest. Contrast targets live next to the
palette so
`tests/unit/test_ui_tokens.py` can fail loudly when a "cooler" colour makes
text unreadable.
"""
import math

from utils.ui.map_presentation import MAP_STYLE

BASE_DPI = 96.0

COLORS = {
    'bg': '#050a07',           # window gutter, deeper than any panel
    'card': '#0a1410',         # HUD panel surface
    'card_hi': '#112019',      # inputs, value chips, hovered surface
    'plot': MAP_STYLE['ground'],
    'line': '#22452f',         # 1px moss-dim border on panels and fields
    'line_strong': '#356b4c',  # section rules, map frame
    'text': '#e9f6ea',
    'muted': '#9dbaa7',
    'faint': '#7fa38c',
    'green': '#8fe36a',        # primary accent: moss lime
    'accent_dim': '#2b6b46',
    'red': '#ff6b6b',          # alarm coral-red
    'blue': '#6fd7b0',         # secondary info: mint, keeps the forest cast
    'amber': '#f0c05a',
    'button': '#0d1a13',
    'button_hover': '#16301f',
    'button_pressed': '#1e4029',
    'disabled_text': '#5e7d6a',
    'danger_bg': '#1f0b0b',
    'primary_text': '#07120a',
    'primary_hover': '#a8f07d',
    'primary_pressed': '#6fc94f',
    # HUD-only shades; derived by hand so no widget needs a second palette.
    'glow': '#8fe36a',         # lit edge of a focused control
    'glow_soft': '#2a6b45',    # one step out from the lit edge
    'glow_faint': '#14351f',   # outermost halo
    'bevel_hi': '#25503a',     # top-left bevel face
    'bevel_lo': '#081209',     # bottom-right bevel face
    'bracket': '#1f4a34',      # viewfinder corner marks
    'track': '#0c1a13',        # empty segment of a bar
    'scan': '#0b1a12',         # scanline / radar fill
    'ok': '#7dff9b',           # fluorescent green for "live" pulses
}

# 前景令牌 -> 相对 surface 的最低对比度（WCAG 比例）
CONTRAST_TARGETS = {'text': 12.0, 'muted': 7.0, 'faint': 5.5, 'line': 1.6, 'line_strong': 2.0}

SPACE = {'sp1': 4, 'sp2': 8, 'sp3': 12, 'sp4': 16}

CONTROL = {'height': 30, 'icon': 30, 'min_width': 64, 'check_row': 26,
           'border': 1, 'canvas_inset': 6, 'row': 24, 'notice': 26,
           'bevel': 7, 'check': 14, 'bar': 9, 'segments': 22}

# 负数 = 像素字号，与既有窗口设定一致；数值读数为等宽大字号。
TYPE = {'section': -12, 'body': -12, 'value': -13, 'coord': -14,
        'label': -11, 'hint': -10, 'micro': -9}


def ui_scale(root=None):
    """DPI scale of the current display; exactly 1.0 on a 96 DPI monitor."""
    try:
        if root is None:
            import tkinter as tk
            root = tk._default_root
        if root is None:
            return 1.0
        return round(float(root.winfo_fpixels('1i')) / BASE_DPI, 3)
    except Exception:
        return 1.0


def px(value, root=None):
    """Pixel value corrected for the current DPI; identity at 100%."""
    return int(round(value * ui_scale(root)))


def _luminance(color):
    raw = color.lstrip('#')
    channels = [int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground, background):
    """WCAG contrast ratio between two #rrggbb colours."""
    first, second = _luminance(foreground), _luminance(background)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def is_readable(foreground, background, target):
    return round(contrast_ratio(foreground, background), 2) >= target or math.isclose(
        contrast_ratio(foreground, background), target, rel_tol=0.02)
