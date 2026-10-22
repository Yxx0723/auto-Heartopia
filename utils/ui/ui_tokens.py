"""Design tokens for the native monitor UI: colour, spacing, type and control size.

The palette is a sci-fi HUD: near-black deep-space surfaces, neon cyan lines,
monospaced numerals. Contrast targets live next to the palette so
`tests/unit/test_ui_tokens.py` can fail loudly when a "cooler" colour makes
text unreadable.
"""
import math

from utils.ui.map_presentation import MAP_STYLE

BASE_DPI = 96.0

COLORS = {
    'bg': '#04070c',           # window gutter, deeper than any panel
    'card': '#070d16',         # HUD panel surface
    'card_hi': '#0c1724',      # inputs, value chips, hovered surface
    'plot': MAP_STYLE['ground'],
    'line': '#1b4353',         # 1px cyan-dim border on panels and fields
    'line_strong': '#2a6579',  # section rules, map frame
    'text': '#e6f8ff',
    'muted': '#93b7c7',
    'faint': '#7396a8',
    'green': '#00e5ff',        # primary accent: neon cyan
    'accent_dim': '#12697d',
    'red': '#ff3366',          # alarm magenta-red
    'blue': '#57c8ff',
    'amber': '#ffb020',
    'button': '#0a1420',
    'button_hover': '#10283a',
    'button_pressed': '#16384f',
    'disabled_text': '#55707f',
    'danger_bg': '#26060f',
    'primary_text': '#04121b',
    'primary_hover': '#5cf0ff',
    'primary_pressed': '#00bfd6',
    # HUD-only shades; derived by hand so no widget needs a second palette.
    'glow': '#00e5ff',         # lit edge of a focused control
    'glow_soft': '#0c5a69',    # one step out from the lit edge
    'glow_faint': '#0a2c38',   # outermost halo
    'bevel_hi': '#1c5867',     # top-left bevel face
    'bevel_lo': '#08151e',     # bottom-right bevel face
    'bracket': '#16505f',      # viewfinder corner marks
    'track': '#0a1622',        # empty segment of a bar
    'scan': '#0b1d28',         # scanline / radar fill
    'ok': '#39ff14',           # fluorescent green for "live" pulses
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
