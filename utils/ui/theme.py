"""Central visual theme for the native Tk UI.

Only presentation values live here: palette, typography, spacing and small
rounded-panel helpers. No game state, no I/O and no import-time side effects.
Everything is expressed with the standard library + Tk so the packaging spec
and the existing module interfaces stay unchanged.
"""
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Palette — cream white, soft green and sage green.
# ---------------------------------------------------------------------------
CREAM = '#F7F5EE'
CARD = '#FFFEFB'
CARD_ALT = '#F1F6EE'
SAGE_50 = '#EAF2E6'
SAGE_100 = '#DDEAD7'
SAGE_200 = '#C9DFC2'
SAGE_300 = '#A9CDA2'
SAGE_500 = '#7FA57C'
SAGE_600 = '#5C8A62'
SAGE_700 = '#3E6B4A'
INK = '#2C3B30'
INK_SOFT = '#5E7062'
LINE = '#DCE5D5'
DISABLED = '#405343'

# Backwards-compatible key set. Existing modules and tests read COLORS[...];
# keeping the names means no call site or assertion has to change.
COLORS = {
    'bg': CREAM,
    'card': CARD,
    'plot': SAGE_50,
    'line': LINE,
    'text': INK,
    'muted': INK_SOFT,
    'green': SAGE_700,
    'red': '#B8514A',
    'blue': '#4F7BA6',
    'amber': '#A9761F',
    'button': SAGE_100,
    'shrub': '#6E915F',
    'shrub_outline': '#5F8A55',
    'blueberry': '#4E72A8',
    'blueberry_outline': '#5A7FB0',
    'raspberry': '#A85C7E',
    'raspberry_outline': '#A85C7E',
    # Extra keys used only by the new chrome / central widget factories.
    'primary': SAGE_300,
    'primary_active': SAGE_200,
    'primary_fg': INK,
    'button_active': SAGE_200,
    'button_border': SAGE_300,
    'segment_bg': SAGE_50,
    'segment_border': SAGE_200,
    'segment_hover': SAGE_100,
    'selected': SAGE_200,
    'disabled': DISABLED,
    'panel_alt': CARD_ALT,
    # 方案三「游戏伴侣风」：暖黄 / 暖橙点缀与柔和状态色。
    'honey': '#F2C879',
    'accent': '#E0A458',
    'accent_soft': '#FBEED6',
    'leaf': '#8FB782',
    'status_on': '#5F9A6B',
    'status_off': '#C4CDBF',
    'status_warn': '#E0A458',
}

# Cartographic palette. Symbols keep their own darker fills so they stay
# legible on the lighter ground, while grid / chrome recede into the background.
MAP_STYLE = {
    'ground': SAGE_50,
    'grid': '#D3E2CC',
    'grid_major': '#BED3B6',
    'edge': '#C2D5BB',
    'panel': CARD,
    'muted': INK_SOFT,
    'water': '#9CC7DE',
    'shore': '#6FA9C6',
    'shore_outer': '#CFE3EE',
    'collision': '#C6D6BF',
    'road': '#B7AC8B',
    'route': '#5C8BAE',
    'route_line': '#9CB4C6',
    'navigation': SAGE_600,
    'trail': '#8FAE86',
    'player': SAGE_700,
    'fence': '#C69A4E',
    'handrail': '#79AFC4',
}

# ---------------------------------------------------------------------------
# Typography and spacing. Negative font sizes are pixels (DPI-stable).
# ---------------------------------------------------------------------------
FONT_FAMILY = 'Microsoft YaHei UI'
MONO_FAMILY = 'Consolas'
_ui_scale = 1.0


def set_scale(value):
    """Set the DPI-derived scale used by the central widget factories."""
    global _ui_scale
    _ui_scale = max(0.8, min(2.0, float(value)))


def scale(value):
    return max(1, int(round(value * _ui_scale)))


def font(size=13, bold=False, mono=False):
    family = MONO_FAMILY if mono else FONT_FAMILY
    return (family, -scale(size), 'bold') if bold else (family, -scale(size))


def px(value):
    return scale(value)


class RoundedPanel(tk.Canvas):
    """A canvas with a soft rounded-card background.

    Children are placed with ``create_window``/``create_text`` and stay above
    the background. Corners remain visible because no full-size rectangular
    child frame covers them.
    """

    def __init__(self, master, radius=14, fill=None, outline=None, **kwargs):
        kwargs.setdefault('bg', COLORS['bg'])
        kwargs.setdefault('bd', 0)
        kwargs.setdefault('highlightthickness', 0)
        super().__init__(master, **kwargs)
        self.radius = radius
        self.fill = fill or COLORS['card']
        self.outline = outline or COLORS['line']
        self.bind('<Configure>', self._redraw, add='+')

    def _redraw(self, event=None):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 4 or height < 4:
            return
        self.delete('panel-bg')
        radius = min(self.radius, width / 2., height / 2.)
        x1, y1, x2, y2 = 2., 2., float(width - 2), float(height - 2)
        r = radius
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                  x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                  x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(points, smooth=True, fill=self.fill,
                            outline=self.outline, width=1, tags='panel-bg')
        self.tag_lower('panel-bg')


# ---------------------------------------------------------------------------
# 方案三「游戏伴侣风」装饰
#
# 全部用 Canvas 图元静态绘制：不读取图片文件、不做动画、不挂定时器。
# 相比位图资源，这既不占额外内存，也不存在启动时反复 IO 的问题，
# 且天然随 DPI 缩放清晰。
# ---------------------------------------------------------------------------
DECOR = {
    'roof': '#D08F72', 'roof_edge': '#B9765C',
    'wall': '#F7E8CF', 'wall_edge': '#DCC39C', 'door': '#C98A62',
    'window': '#CFE6EE', 'window_edge': '#A9CBBA',
    'leaf': '#8FB782', 'leaf_dark': '#6E9C68',
    'petal': '#F3C9D0', 'petal_core': '#F2C879',
    'cloud': '#EDF3EA', 'cloud_edge': '#DCE7D8',
    'bush': '#A9CDA2', 'trunk': '#C4A484',
}


def draw_house(canvas, x, y, size=1.0, tags='decor-house'):
    """静态小镇小屋：屋顶 + 墙 + 门 + 窗。返回 tag 便于整体删除/置前。"""
    s = float(size)
    w, h = 18.0 * s, 12.0 * s
    canvas.create_rectangle(x - w / 2, y, x + w / 2, y + h, fill=DECOR['wall'],
                            outline=DECOR['wall_edge'], width=max(1, int(round(s))), tags=tags)
    canvas.create_polygon(x - w / 2 - 3 * s, y + 1.5 * s, x, y - 8.5 * s,
                          x + w / 2 + 3 * s, y + 1.5 * s, fill=DECOR['roof'],
                          outline=DECOR['roof_edge'], width=max(1, int(round(s))), tags=tags)
    canvas.create_rectangle(x - 2.4 * s, y + h - 5.2 * s, x + 2.4 * s, y + h,
                            fill=DECOR['door'], outline='', tags=tags)
    canvas.create_rectangle(x + w / 2 - 6.4 * s, y + 3.2 * s,
                            x + w / 2 - 2.6 * s, y + 6.6 * s,
                            fill=DECOR['window'], outline=DECOR['window_edge'],
                            width=1, tags=tags)
    return tags


def draw_cloud(canvas, x, y, size=1.0, tags='decor-cloud'):
    """静态云朵，由三个椭圆组成。"""
    s = float(size)
    for dx, dy, rx, ry in ((-7 * s, 1 * s, 6 * s, 4 * s), (0, -1.5 * s, 7 * s, 5 * s),
                           (7 * s, 1 * s, 6 * s, 4 * s)):
        canvas.create_oval(x + dx - rx, y + dy - ry, x + dx + rx, y + dy + ry,
                           fill=DECOR['cloud'], outline=DECOR['cloud_edge'], width=1, tags=tags)
    return tags


def draw_bush(canvas, x, y, size=1.0, tags='decor-bush'):
    """静态草丛：两个叠放的半球。"""
    s = float(size)
    canvas.create_oval(x - 8 * s, y - 5 * s, x + 2 * s, y + 3 * s,
                       fill=DECOR['bush'], outline='', tags=tags)
    canvas.create_oval(x - 1 * s, y - 7 * s, x + 9 * s, y + 3 * s,
                       fill=DECOR['leaf'], outline='', tags=tags)
    return tags


def draw_flower(canvas, x, y, size=1.0, tags='decor-flower'):
    """静态小花：四瓣 + 花心，尺寸很小，只作点缀。"""
    s = float(size)
    canvas.create_line(x, y, x, y + 6 * s, fill=DECOR['leaf_dark'], width=1, tags=tags)
    for dx, dy in ((-2.2 * s, 0), (2.2 * s, 0), (0, -2.2 * s), (0, 2.2 * s)):
        canvas.create_oval(x + dx - 1.9 * s, y + dy - 1.9 * s, x + dx + 1.9 * s,
                           y + dy + 1.9 * s, fill=DECOR['petal'], outline='', tags=tags)
    canvas.create_oval(x - 1.3 * s, y - 1.3 * s, x + 1.3 * s, y + 1.3 * s,
                       fill=DECOR['petal_core'], outline='', tags=tags)
    return tags


def draw_cat(canvas, x, y, size=1.0, tags='decor-cat'):
    """静态小猫剪影：身体 + 头 + 双耳 + 尾巴。"""
    s = float(size)
    canvas.create_oval(x - 6 * s, y - 4 * s, x + 4 * s, y + 3 * s,
                       fill=DECOR['roof'], outline='', tags=tags)
    canvas.create_oval(x + 1 * s, y - 7 * s, x + 7 * s, y - 1 * s,
                       fill=DECOR['roof'], outline='', tags=tags)
    canvas.create_polygon(x + 2 * s, y - 6.4 * s, x + 3 * s, y - 9.4 * s, x + 4.4 * s, y - 6.6 * s,
                          fill=DECOR['roof'], outline='', tags=tags)
    canvas.create_polygon(x + 5.4 * s, y - 6.6 * s, x + 6.6 * s, y - 9.2 * s, x + 7.2 * s, y - 5.8 * s,
                          fill=DECOR['roof'], outline='', tags=tags)
    canvas.create_line(x - 6 * s, y + 1 * s, x - 9 * s, y - 3 * s, x - 7 * s, y - 5 * s,
                       fill=DECOR['roof'], width=max(1, int(round(1.4 * s))),
                       smooth=True, tags=tags)
    return tags


def draw_scene(canvas, x, y, width, tags='decor-scene', compact=False):
    """把房屋、云、草丛组合成一幅静态小景，宽度受限时自动精简。"""
    draw_cloud(canvas, x + 6, y - 14, 0.9, tags=tags)
    draw_house(canvas, x + 30, y + 6, 1.0, tags=tags)
    draw_bush(canvas, x + 52, y + 8, 0.9, tags=tags)
    if not compact and width >= 120:
        draw_flower(canvas, x + 64, y + 4, 0.9, tags=tags)
        draw_cat(canvas, x + 78, y + 8, 0.9, tags=tags)
    return tags



def apply_theme(root):
    """Configure the option database and every ttk style in one place."""
    root.configure(bg=COLORS['bg'])
    root.option_add('*Font', font(13))
    for key, value in {
        '*Text.background': COLORS['card'],
        '*Text.foreground': COLORS['text'],
        '*Text.insertBackground': COLORS['green'],
        '*Text.selectBackground': COLORS['selected'],
        '*Text.selectForeground': COLORS['text'],
        '*Text.highlightThickness': 1,
        '*Text.highlightBackground': COLORS['line'],
        '*Text.highlightColor': COLORS['line'],
        '*Text.borderWidth': 0,
        '*Text.padX': px(6),
        '*Text.padY': px(4),
        '*Entry.background': COLORS['card'],
        '*Entry.foreground': COLORS['text'],
        '*Entry.insertBackground': COLORS['green'],
        '*Entry.selectBackground': COLORS['selected'],
        '*Entry.selectForeground': COLORS['text'],
        '*Spinbox.background': COLORS['card'],
        '*Spinbox.foreground': COLORS['text'],
        '*Spinbox.buttonBackground': COLORS['panel_alt'],
        '*Spinbox.insertBackground': COLORS['green'],
        '*Spinbox.selectBackground': COLORS['selected'],
        '*Spinbox.selectForeground': COLORS['text'],
        '*Listbox.background': COLORS['card'],
        '*Listbox.foreground': COLORS['text'],
        '*Listbox.selectBackground': COLORS['selected'],
        '*Listbox.selectForeground': COLORS['text'],
        '*LabelFrame.background': COLORS['bg'],
        '*LabelFrame.foreground': COLORS['text'],
    }.items():
        root.option_add(key, value)

    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass
    style.configure('.', background=COLORS['bg'], foreground=COLORS['text'],
                    fieldbackground=COLORS['card'], bordercolor=COLORS['line'],
                    lightcolor=COLORS['card'], darkcolor=COLORS['line'],
                    font=font(12))
    style.configure('TFrame', background=COLORS['bg'])
    style.configure('TLabel', background=COLORS['bg'], foreground=COLORS['text'])
    style.configure('TButton', background=COLORS['button'], foreground=COLORS['text'],
                    bordercolor=COLORS['line'], lightcolor=COLORS['button'],
                    darkcolor=COLORS['button'], font=font(12), padding=(px(8), px(4)))
    style.map('TButton', background=[('active', COLORS['button_active']),
                                     ('disabled', COLORS['panel_alt'])],
              foreground=[('disabled', COLORS['disabled'])])
    for name in ('TEntry', 'TSpinbox', 'TCombobox'):
        style.configure(name, fieldbackground=COLORS['card'], foreground=COLORS['text'],
                        background=COLORS['card'], bordercolor=COLORS['line'],
                        lightcolor=COLORS['line'], darkcolor=COLORS['line'],
                        arrowcolor=COLORS['green'], insertcolor=COLORS['green'],
                        font=font(12), padding=(px(6), px(3)))
        style.map(name, fieldbackground=[('disabled', COLORS['panel_alt']),
                                         ('readonly', COLORS['card'])],
                  foreground=[('disabled', COLORS['disabled'])],
                  bordercolor=[('focus', COLORS['green'])])
    style.configure('TCheckbutton', background=COLORS['bg'], foreground=COLORS['text'],
                    font=font(12))
    style.map('TCheckbutton', background=[('active', COLORS['bg'])],
              foreground=[('disabled', COLORS['disabled'])])
    style.configure('TRadiobutton', background=COLORS['bg'], foreground=COLORS['text'],
                    font=font(12))
    style.configure('Treeview', background=COLORS['card'], fieldbackground=COLORS['card'],
                    foreground=COLORS['text'], bordercolor=COLORS['line'],
                    lightcolor=COLORS['card'], darkcolor=COLORS['card'], font=font(12),
                    rowheight=px(24))
    style.map('Treeview', background=[('selected', COLORS['selected'])],
              foreground=[('selected', COLORS['text'])])
    style.configure('Treeview.Heading', background=COLORS['panel_alt'],
                    foreground=COLORS['text'], bordercolor=COLORS['line'],
                    font=font(12, bold=True), padding=(px(6), px(4)))
    style.map('Treeview.Heading', background=[('active', SAGE_100)])
    style.configure('TProgressbar', background=SAGE_500, troughcolor=SAGE_50,
                    bordercolor=COLORS['line'], lightcolor=SAGE_500, darkcolor=SAGE_500)
    style.configure('TScrollbar', background=SAGE_100, troughcolor=CREAM,
                    bordercolor=CREAM, arrowcolor=SAGE_700)
    style.map('TScrollbar', background=[('active', SAGE_200)])
    style.configure('TNotebook', background=COLORS['bg'], borderwidth=0,
                    tabmargins=(0, px(4), 0, 0))
    style.configure('TNotebook.Tab', background=SAGE_50, foreground=INK_SOFT,
                    padding=(px(14), px(5)), borderwidth=0, font=font(12))
    style.map('TNotebook.Tab', background=[('selected', COLORS['card']),
                                           ('active', SAGE_100)],
              foreground=[('selected', SAGE_700), ('disabled', DISABLED)])
    style.configure('Monitor.TNotebook', background=COLORS['bg'], borderwidth=0,
                    tabmargins=(0, 0, 0, 0))
    style.configure('Monitor.TNotebook.Tab', background=SAGE_50, foreground=INK_SOFT,
                    padding=(px(12), px(4)), borderwidth=0, font=font(12))
    style.map('Monitor.TNotebook.Tab', background=[('selected', COLORS['card']),
                                                    ('active', SAGE_100)],
              foreground=[('selected', SAGE_700)])
    style.configure('Monitor.TCombobox', fieldbackground=COLORS['card'],
                    foreground=COLORS['text'], background=COLORS['card'],
                    bordercolor=COLORS['line'], lightcolor=COLORS['line'],
                    darkcolor=COLORS['line'], arrowcolor=COLORS['green'],
                    insertcolor=COLORS['green'], font=font(13),
                    padding=(px(7), px(4)), arrowsize=px(14), borderwidth=1)
    style.map('Monitor.TCombobox',
              fieldbackground=[('disabled', COLORS['panel_alt']), ('readonly', COLORS['card'])],
              foreground=[('disabled', COLORS['disabled'])],
              arrowcolor=[('disabled', COLORS['disabled'])],
              background=[('active', COLORS['button_active'])],
              bordercolor=[('focus', COLORS['green']), ('active', SAGE_200)])
    for key, value in {'background': COLORS['card'], 'foreground': COLORS['text'],
                       'selectBackground': COLORS['selected'],
                       'selectForeground': COLORS['text'],
                       'font': font(13), 'borderWidth': 0,
                       'highlightThickness': 0, 'selectBorderWidth': 0}.items():
        root.option_add(f'*TCombobox*Listbox.{key}', value)
    return style


def enable_dpi_awareness():
    """Declare system-DPI awareness before Tk creates any window."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            return
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_dpi_scale(root):
    """Align Tk's point-to-pixel scaling with the current monitor DPI."""
    try:
        factor = root.winfo_fpixels('1i') / 96.0
        root.tk.call('tk', 'scaling', root.winfo_fpixels('1i') / 72.0)
        set_scale(factor)
    except Exception:
        set_scale(1.0)
