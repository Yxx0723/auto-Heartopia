"""Central visual theme for the native Tk UI.

Only presentation values live here: palette, typography, spacing and small
rounded-panel helpers. No game state, no I/O and no import-time side effects.
Everything is expressed with the standard library + Tk so the packaging spec
and the existing module interfaces stay unchanged.
"""
import math
import tkinter as tk
from tkinter import font as tkfont
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
    'button_press': SAGE_300,
    'button_border_hover': SAGE_500,
    'button_border_press': SAGE_600,
    'primary_press': '#93BD8C',
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
    'disabled_on_dark': '#D7E2D2',
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


ui_font = font


def rounded_points(x1, y1, x2, y2, radius, steps=6):
    """Flat point list tracing a true rounded rectangle (quarter arcs).

    ``create_polygon(..., smooth=True)`` only approximates a curve and ends up
    with a radius far smaller than requested, so the corners are sampled from
    real arcs instead. That keeps the declared 8/10/12/14px radii honest.
    """
    r = max(1., min(float(radius), (x2 - x1) / 2., (y2 - y1) / 2.))
    points = []
    for (cx, cy), start in (((x1 + r, y1 + r), 180.), ((x2 - r, y1 + r), 270.),
                            ((x2 - r, y2 - r), 0.), ((x1 + r, y2 - r), 90.)):
        for index in range(steps + 1):
            angle = math.radians(start + 90. * index / steps)
            points.append(cx + r * math.cos(angle))
            points.append(cy + r * math.sin(angle))
    return points


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
        x1, y1, x2, y2 = 2., 2., float(width - 2), float(height - 2)
        points = rounded_points(x1, y1, x2, y2, self.radius)
        self.create_polygon(points, fill=self.fill, outline=self.outline,
                            width=1, tags='panel-bg')
        self.tag_lower('panel-bg')


def _parent_fill(master):
    """Background colour of the parent, used to blend the button corners."""
    for option in ('bg', 'background'):
        try:
            return master.cget(option)
        except Exception:
            continue
    return COLORS['bg']


def _is_dark(color):
    """True for a dark hex fill, where the muted disabled text would vanish."""
    try:
        red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    except Exception:
        return False
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0 < 0.55


_FONT_CACHE = {}


def _font_object(widget, spec):
    """Cached tkinter font object; creating one per measurement is expensive."""
    key = (str(widget.tk), spec if isinstance(spec, str) else tuple(spec))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        try:
            cached.actual()  # drops fonts whose interpreter has been destroyed
            return cached
        except Exception:
            _FONT_CACHE.pop(key, None)
    if len(_FONT_CACHE) > 64:
        _FONT_CACHE.clear()
    cached = tkfont.Font(root=widget, font=spec)
    _FONT_CACHE[key] = cached
    return cached


class RoundedButton(tk.Canvas):
    """A button whose visible border is a real arc, not a rectangle.

    Tk's native button can only paint rectangles, so the rounded shape and the
    label are drawn on a lightweight canvas. The widget keeps the button
    contract the rest of the UI relies on: ``command`` callbacks, keyboard
    activation (Space/Return), Tab focus with a visible focus ring, hover,
    pressed and disabled states, plus ``configure``/``cget`` for the options
    the call sites already use (text/state/bg/fg/font/padx/pady).

    Redraws happen only when a state or size actually changes — there is no
    timer, animation or bitmap resource, so idle CPU cost stays at zero.
    """

    def __init__(self, master, text='', command=None, radius=None,
                 fill=None, fill_active=None, fill_pressed=None, fill_disabled=None,
                 fg=None, fg_active=None, fg_disabled=None,
                 border=None, border_active=None, border_pressed=None,
                 font=None, padx=None, pady=None, anchor='center',
                 width=None, height=None, back=None, cursor='hand2',
                 state='normal', takefocus=True, **_ignored):
        super().__init__(master, bd=0, highlightthickness=0,
                         bg=back or _parent_fill(master), cursor=cursor,
                         width=width or 1, height=height or 1, takefocus=takefocus)
        self._text = '' if text is None else str(text)
        self._command = command
        self._radius = px(radius if radius is not None else 10)
        self._fill = fill or COLORS['button']
        self._fill_active = fill_active or COLORS['button_active']
        self._fill_pressed = fill_pressed or COLORS['button_press']
        self._fill_disabled = fill_disabled
        self._fg = fg or COLORS['text']
        self._fg_active = fg_active or self._fg
        self._fg_disabled = fg_disabled or (
            COLORS['disabled_on_dark'] if _is_dark(self._fill) else COLORS['disabled'])
        self._border = COLORS['button_border'] if border is None else border
        self._border_active = (COLORS['button_border_hover']
                               if border_active is None else border_active)
        self._border_pressed = (COLORS['button_border_press']
                                if border_pressed is None else border_pressed)
        self._font = font or ui_font(12)
        self._padx = px(9) if padx is None else int(padx)
        self._pady = px(5) if pady is None else int(pady)
        self._anchor = anchor
        self._state = str(state)
        self._hover = False
        self._pressed = False
        self._focused = False
        self._fixed_width = width is not None
        self._fixed_height = height is not None
        self._shape_id = None
        self._label_id = None
        self._drawn = None
        self.bind('<Configure>', self._on_configure, add='+')
        self.bind('<Enter>', self._on_enter, add='+')
        self.bind('<Leave>', self._on_leave, add='+')
        self.bind('<ButtonPress-1>', self._on_press, add='+')
        self.bind('<ButtonRelease-1>', self._on_release, add='+')
        self.bind('<FocusIn>', self._on_focus_in, add='+')
        self.bind('<FocusOut>', self._on_focus_out, add='+')
        self.bind('<KeyPress-space>', self._on_key_press, add='+')
        self.bind('<KeyRelease-space>', self._on_key_release, add='+')
        self.bind('<KeyRelease-Return>', self._on_key_release, add='+')
        self._resize()
        # The first <Configure> paints the shape; skipping a pre-map redraw
        # halves the drawing done while the window is being built.

    # -- geometry ---------------------------------------------------------
    def _text_metrics(self):
        try:
            measure = _font_object(self, self._font)
            return measure.measure(self._text), measure.metrics('linespace')
        except Exception:
            return px(10) * max(1, len(self._text)), px(16)

    def _resize(self):
        text_width, line_height = self._text_metrics()
        if not self._fixed_width:
            tk.Canvas.configure(self, width=text_width + 2 * self._padx + 2)
        if not self._fixed_height:
            tk.Canvas.configure(self, height=line_height + 2 * self._pady + 2)

    # -- state ------------------------------------------------------------
    def _active_fill(self):
        if self._state == 'disabled':
            return self._fill_disabled or self._fill
        if self._pressed:
            return self._fill_pressed
        if self._hover:
            return self._fill_active
        return self._fill

    def _active_border(self):
        if self._focused and self._state != 'disabled':
            return COLORS['green']  # focus ring is always visible
        if not self._border and not self._border_active and not self._border_pressed:
            return ''  # borderless: the button blends into its container
        if self._state == 'disabled':
            return COLORS['line']
        if self._focused:
            return COLORS['green']
        if self._pressed:
            return self._border_pressed
        if self._hover:
            return self._border_active
        return self._border

    def _active_fg(self):
        if self._state == 'disabled':
            return self._fg_disabled
        if self._hover and not self._pressed:
            return self._fg_active
        return self._fg

    # -- drawing ----------------------------------------------------------
    def _redraw(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        if width < 4 or height < 4:
            return
        radius = max(2., min(self._radius, (width - 2) / 2., (height - 2) / 2.))
        x1, y1, x2, y2 = 1., 1., float(width - 1), float(height - 1)
        fill, outline = self._active_fill(), self._active_border()
        fg = self._active_fg()
        if self._anchor == 'w':
            x, y, anchor = self._padx + 2, height / 2., 'w'
        elif self._anchor == 'e':
            x, y, anchor = width - self._padx - 2, height / 2., 'e'
        else:
            x, y, anchor = width / 2., height / 2., 'center'
        # Skip identical repaints: a resize sends many <Configure> events whose
        # size/state did not actually change.
        signature = (width, height, round(radius, 2), fill, outline, fg,
                     self._text, tuple(self._font) if isinstance(self._font, tuple) else self._font,
                     anchor)
        if signature == self._drawn:
            return
        self._drawn = signature
        if self._shape_id is not None:
            try:
                self.coords(self._shape_id, *rounded_points(x1, y1, x2, y2, radius))
                self.itemconfigure(self._shape_id, fill=fill, outline=outline)
            except tk.TclError:
                self._shape_id = None
        if self._shape_id is None:
            self._shape_id = self.create_polygon(
                rounded_points(x1, y1, x2, y2, radius), fill=fill, outline=outline,
                width=1, tags='button-shape')
        if self._label_id is not None:
            try:
                self.coords(self._label_id, x, y)
                self.itemconfigure(self._label_id, text=self._text, fill=fg,
                                   font=self._font, anchor=anchor)
            except tk.TclError:
                self._label_id = None
        if self._label_id is None:
            self._label_id = self.create_text(x, y, text=self._text, fill=fg,
                                              font=self._font, anchor=anchor,
                                              tags='button-label')

    # -- events -----------------------------------------------------------
    def _on_configure(self, event=None):
        self._redraw()

    def _on_enter(self, event=None):
        if self._state == 'disabled' or self._hover:
            return
        self._hover = True
        self._redraw()

    def _on_leave(self, event=None):
        if not (self._hover or self._pressed):
            return
        self._hover = self._pressed = False
        self._redraw()

    def _on_press(self, event=None):
        if self._state == 'disabled':
            return
        self._pressed = True
        try:
            self.focus_set()
        except Exception:
            pass
        self._redraw()

    def _on_release(self, event=None):
        was_pressed = self._pressed
        self._pressed = False
        inside = True
        if event is not None and hasattr(event, 'x'):
            inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._hover = inside and self._state != 'disabled'
        self._redraw()
        if was_pressed and inside and self._state != 'disabled':
            self.invoke()

    def _on_focus_in(self, event=None):
        if not self._focused:
            self._focused = True
            self._redraw()

    def _on_focus_out(self, event=None):
        if self._focused:
            self._focused = False
            self._redraw()

    def _on_key_press(self, event=None):
        self._on_press()
        return 'break'

    def _on_key_release(self, event=None):
        if self._pressed:
            self._pressed = False
            self._redraw()
            if self._state != 'disabled':
                self.invoke()
        return 'break'

    def invoke(self):
        if self._state != 'disabled' and callable(self._command):
            self._command()

    # -- option compatibility --------------------------------------------
    def _set_option(self, key, value):
        """Apply one option; returns (needs_resize, needs_redraw)."""
        key = str(key).lower().replace('-', '')
        if key == 'text':
            self._text = '' if value is None else str(value)
            return True, True
        if key in ('bg', 'background', 'fill'):
            self._fill = value
            return False, True
        if key in ('activebackground', 'fillactive'):
            self._fill_active = value
            return False, True
        if key in ('fg', 'foreground'):
            self._fg = value
            return False, True
        if key in ('activeforeground', 'fgactive'):
            self._fg_active = value
            return False, True
        if key in ('disabledforeground', 'fgdisabled'):
            self._fg_disabled = value
            return False, True
        if key == 'state':
            self._state = str(value)
            return False, True
        if key == 'font':
            self._font = value
            return True, True
        if key == 'padx':
            self._padx = int(value)
            return True, True
        if key == 'pady':
            self._pady = int(value)
            return True, True
        if key == 'anchor':
            self._anchor = str(value)
            return False, True
        if key in ('border', 'outline'):
            self._border = value
            return False, True
        if key in ('radius',):
            self._radius = px(int(value))
            return False, True
        if key in ('command', 'action'):
            self._command = value
            return False, False
        if key in ('cursor',):
            tk.Canvas.configure(self, cursor=value)
            return False, False
        if key in ('width',):
            self._fixed_width = True
            tk.Canvas.configure(self, width=int(value))
            return False, True
        if key in ('height',):
            self._fixed_height = True
            tk.Canvas.configure(self, height=int(value))
            return False, True
        # Purely native-button options (relief, borderwidth, highlight*) have no
        # meaning for a canvas shape; anything else is handed back to Tk.
        if key in ('relief', 'bd', 'borderwidth', 'highlightthickness', 'highlightbackground',
                   'highlightcolor', 'overrelief', 'offrelief', 'default', 'indicatoron',
                   'takefocus', 'underline', 'wraplength', 'justify'):
            return False, False
        try:
            tk.Canvas.configure(self, **{key: value})
        except Exception:
            pass
        return False, False

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        resize = redraw = False
        for key, value in kwargs.items():
            resized, redrawn = self._set_option(key, value)
            resize = resize or resized
            redraw = redraw or redrawn
        if resize:
            self._resize()
        if redraw or resize:
            self._redraw()
        return None

    config = configure

    def cget(self, key):
        key = str(key).lower().replace('-', '')
        values = {'text': self._text, 'state': self._state, 'bg': self._fill,
                  'background': self._fill, 'fg': self._fg, 'foreground': self._fg,
                  'font': self._font, 'command': self._command,
                  'activebackground': self._fill_active,
                  'disabledforeground': self._fg_disabled,
                  'padx': self._padx, 'pady': self._pady, 'anchor': self._anchor,
                  'radius': self._radius}
        if key in values:
            return values[key]
        return tk.Canvas.cget(self, key)


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
