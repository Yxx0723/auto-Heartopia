"""Native Tk controls in the v2 skin: flat rectangles, one-pixel rules, mono digits.

Tk has no alpha channel and no border-radius, which is exactly what this skin
wants: every control is a flat rectangle with a 1px edge, so the whole kit is
plain `create_rectangle` / `create_line` / `create_text` work. Every colour comes
from `ui_tokens.COLORS`; shades are mixed here so no widget owns a second palette.
"""
import tkinter as tk
import tkinter.font as tkfont

from utils.ui.ui_tokens import COLORS, CONTROL, TYPE, px

UI_FONT_CANDIDATES = ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI')
MONO_FONT_CANDIDATES = ('Cascadia Mono', 'Consolas', 'Courier New')

_font_cache, _family_cache = {}, {}


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def _rgb(color):
    raw = color.lstrip('#')
    return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))


def mix(first, second, ratio):
    """Blend two token colours; ratio 1 favours `second`."""
    a, b = _rgb(first), _rgb(second)
    return _hex(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


def lighten(color, ratio=.25):
    return mix(color, _hex((255, 255, 255)), ratio)


def darken(color, ratio=.25):
    return mix(color, _hex((0, 0, 0)), ratio)


def _pick_family(candidates, fallback):
    key = candidates[0]
    if key not in _family_cache:
        try:
            families = tkfont.families()
        except Exception:
            families = ()
        if families:
            available = {name.lower() for name in families}
            _family_cache[key] = next((name for name in candidates if name.lower() in available), fallback)
        else:
            # 没有可用 Tk 环境（导入期/无窗口测试）时不缓存，等有 root 再重新解析。
            return fallback
    return _family_cache[key]


def ui_family():
    return _pick_family(UI_FONT_CANDIDATES, 'Microsoft YaHei UI')


def mono_family():
    return _pick_family(MONO_FONT_CANDIDATES, 'Consolas')


def _sized(size):
    return -max(7, px(abs(size)))


def ui_font(size=None, bold=False):
    size = TYPE['body'] if size is None else size
    return (ui_family(), _sized(size), 'bold') if bold else (ui_family(), _sized(size))


def mono_font(size=None, bold=False):
    size = TYPE['value'] if size is None else size
    return (mono_family(), _sized(size), 'bold') if bold else (mono_family(), _sized(size))


def font_of(master, spec):
    """Memoised `tkfont.Font` for measuring; keyed by the spec tuple."""
    key = tuple(spec)
    if key not in _font_cache:
        try:
            _font_cache[key] = tkfont.Font(root=master, font=spec)
        except Exception:
            _font_cache[key] = tkfont.Font(root=master, font=(UI_FONT_CANDIDATES[0], -12))
    return _font_cache[key]


def bevel_points(x0, y0, x1, y1, cut=0):
    """Rectangle vertices. Kept for callers that still pass a cut value."""
    return (x0, y0, x1, y0, x1, y1, x0, y1)


class HudButton(tk.Canvas):
    """Flat rectangle button with a hover wash and a pressed shade.

    Keeps the small part of the `tk.Button` API the window actually uses:
    `configure(text=/state=/bg=/fg=/font=)`, `cget('state'|'text')`, `invoke`,
    and the `enabled` property.
    """

    IGNORED = ('highlightcolor', 'highlightthickness', 'activebackground', 'activeforeground',
               'pressedbackground', 'relief', 'bd', 'cursor', 'takefocus', 'offvalue',
               'onvalue', 'padx', 'pady', 'anchor', 'justify', 'disabledforeground',
               'highlightbackground', 'width', 'height', 'wraplength')

    def __init__(self, master=None, text='', command=None, primary=False, small=False, **options):
        self._text = text
        self._command = command
        self._primary = primary
        self._font = options.pop('font', ui_font(TYPE['body']))
        self._padx = px(options.pop('padx', 8 if small else 12))
        self._pady = px(options.pop('pady', 3))
        self._width_chars = options.pop('width', None)
        self._fill = options.pop('bg', COLORS['green' if primary else 'button'])
        self._fg = options.pop('fg', COLORS['primary_text' if primary else 'text'])
        self._state = options.pop('state', 'normal')
        self._disabled_fg = options.pop('disabledforeground', COLORS['disabled_text'])
        self._outline = options.pop('highlightbackground', COLORS['line'])
        self._hover = False
        self._pressed = False
        for key in list(options):
            if key in self.IGNORED or key in ('bg', 'fg', 'command', 'text', 'state'):
                options.pop(key)
        super().__init__(master, bg=self._surface(master), highlightthickness=0, bd=0,
                         takefocus=1, **options)
        self.bind('<Enter>', self._enter, add='+')
        self.bind('<Leave>', self._leave, add='+')
        self.bind('<ButtonPress-1>', self._press, add='+')
        self.bind('<ButtonRelease-1>', self._release, add='+')
        self.bind('<FocusIn>', lambda event: (self._redraw(), None)[1], add='+')
        self.bind('<FocusOut>', lambda event: (self._redraw(), None)[1], add='+')
        self.bind('<Configure>', lambda event: self._redraw(), add='+')
        self.bind('<Return>', lambda event: self._click())
        self.bind('<space>', lambda event: self._click())
        self._resize()
        self.configure(cursor='hand2' if self.enabled else 'arrow')

    # -- layout ------------------------------------------------------------
    @staticmethod
    def _surface(master):
        try:
            return master.cget('bg')
        except Exception:
            return COLORS['card']

    def _resize(self):
        metrics = font_of(self, self._font)
        if self._width_chars:
            width = metrics.measure('0') * int(self._width_chars) + 2 * self._padx
        else:
            width = metrics.measure(self._text) + 2 * self._padx + px(4)
        height = max(px(CONTROL['height']), metrics.metrics('linespace') + 2 * self._pady + px(2))
        tk.Canvas.configure(self, width=max(width, px(18)), height=height)
        self._redraw()

    # -- state -------------------------------------------------------------
    @property
    def enabled(self):
        return self._state != 'disabled'

    def invoke(self):
        if self.enabled and self._command:
            self._command()
        return None

    def _click(self):
        if self.enabled:
            self.invoke()

    def _enter(self, _event=None):
        self._hover = True
        self._redraw()

    def _leave(self, _event=None):
        self._hover, self._pressed = False, False
        self._redraw()

    def _press(self, event=None):
        if not self.enabled:
            return
        self._pressed = True
        self._redraw()
        self.focus_set()

    def _release(self, event=None):
        inside = event is not None and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        if was_pressed and inside and self.enabled:
            self.invoke()

    # -- drawing -----------------------------------------------------------
    def _colors(self):
        if not self.enabled:
            return COLORS['card'], COLORS['line'], self._disabled_fg
        if self._pressed:
            fill = COLORS['primary_pressed' if self._primary else 'button_pressed']
            return fill, COLORS['accent_dim'], self._fg
        if self._hover:
            fill = COLORS['primary_hover' if self._primary else 'button_hover']
            return fill, COLORS['line_strong'], self._fg
        return self._fill, self._outline, self._fg

    def _redraw(self):
        try:
            width, height = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            width = int(self.cget('width')) or px(40)
            height = int(self.cget('height')) or px(CONTROL['height'])
        fill, outline, fg = self._colors()
        self.delete('all')
        self.create_rectangle(0, 0, width - 1, height - 1, fill=fill, outline=outline, width=1)
        self.create_text(width / 2, height / 2 + (1 if self._pressed else 0), text=self._text,
                         fill=fg, font=self._font, anchor='center')

    # -- Tk API compatibility ---------------------------------------------
    def configure(self, **options):  # noqa: A003 - mirrors the Tk API
        redraw = resized = False
        passthrough = {}
        if 'cursor' in options:
            passthrough['cursor'] = options.pop('cursor')
        if 'text' in options:
            self._text, redraw = options.pop('text'), True
        if 'state' in options:
            self._state = options.pop('state')
            self.configure_cursor()
            redraw = True
        if 'bg' in options:
            self._fill, redraw = options.pop('bg'), True
        if 'fg' in options:
            self._fg, redraw = options.pop('fg'), True
        if 'font' in options:
            self._font, redraw = options.pop('font'), True
        if 'command' in options:
            self._command = options.pop('command')
        if 'highlightbackground' in options:
            self._outline = options.pop('highlightbackground')
            redraw = True
        if 'disabledforeground' in options:
            self._disabled_fg = options.pop('disabledforeground')
            redraw = True
        if 'padx' in options:
            self._padx, resized = px(options.pop('padx')), True
        if 'pady' in options:
            self._pady, resized = px(options.pop('pady')), True
        if 'width' in options:
            self._width_chars, resized = options.pop('width'), True
        for key in list(options):
            if key in self.IGNORED:
                options.pop(key)
        options.update(passthrough)
        result = tk.Canvas.configure(self, **options) if options else None
        if resized:
            self._resize()
        elif redraw:
            self._redraw()
        return result

    config = configure

    def cget(self, key):
        if key == 'state':
            return self._state
        if key == 'text':
            return self._text
        if key == 'bg':
            return self._fill
        if key == 'fg':
            return self._fg
        if key == 'font':
            return self._font
        return tk.Canvas.cget(self, key)

    def configure_cursor(self):
        self.configure(cursor='hand2' if self.enabled else 'arrow')


class HudCheck(tk.Frame):
    """Checkbox: 14px square, lime fill when checked, plain label next to it."""

    def __init__(self, master, text='', variable=None, command=None, font=None,
                 wraplength=0, justify='left', **options):
        self._state = options.pop('state', 'normal')
        self._variable = variable if variable is not None else tk.BooleanVar(master, False)
        self._command = command
        bg = options.pop('bg', self._surface(master))
        fg = options.pop('fg', COLORS['text'])
        self._fg = fg
        super().__init__(master, bg=bg, takefocus=0, **options)
        size = px(CONTROL['check'])
        self.box = tk.Canvas(self, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        self.box.pack(side='left', padx=(0, px(6)))
        self.text = tk.Label(self, text=text, bg=bg, fg=fg, font=font or ui_font(TYPE['body']),
                             anchor='w', justify=justify, wraplength=wraplength, cursor='hand2')
        self.text.pack(side='left')
        self._size = size
        for widget in (self, self.box, self.text):
            widget.bind('<Button-1>', self._toggle, add='+')
            widget.bind('<Enter>', lambda event: self._hover(True), add='+')
            widget.bind('<Leave>', lambda event: self._hover(False), add='+')
        self._hovering = False
        self._trace = self._variable.trace_add('write', lambda *_: self._draw())
        self._draw()

    @staticmethod
    def _surface(master):
        try:
            return master.cget('bg')
        except Exception:
            return COLORS['card']

    @property
    def enabled(self):
        return self._state != 'disabled'

    def _hover(self, state):
        self._hovering = state and self.enabled
        self._draw()

    def _toggle(self, _event=None):
        if not self.enabled:
            return 'break'
        self._variable.set(not self._variable.get())
        if self._command:
            self._command()
        self._draw()
        return 'break'

    def _draw(self):
        size = self._size
        self.box.delete('all')
        on = bool(self._variable.get())
        if not self.enabled:
            outline, fill = COLORS['line'], COLORS['card']
        elif on:
            outline, fill = COLORS['green'], COLORS['green']
        else:
            outline, fill = (COLORS['line_strong'] if self._hovering else COLORS['line']), COLORS['card_hi']
        self.box.create_rectangle(0, 0, size - 1, size - 1, fill=fill, outline=outline, width=1)
        if on:
            ink = COLORS['primary_text']
            self.box.create_line(size * .22, size * .52, size * .43, size * .74,
                                 size * .78, size * .26, fill=ink, width=2,
                                 capstyle='round', joinstyle='round')
        self.text.configure(fg=self._fg if self.enabled else COLORS['disabled_text'])

    def configure(self, **options):  # noqa: A003 - mirrors the Tk API
        if 'state' in options:
            self._state = options.pop('state')
            self.configure(cursor='hand2' if self.enabled else 'arrow')
            self.text.configure(cursor='hand2' if self.enabled else 'arrow')
            self._draw()
        if 'text' in options:
            self.text.configure(text=options.pop('text'))
        if 'variable' in options:
            self._variable = options.pop('variable')
            self._draw()
        if 'fg' in options:
            self._fg = options.pop('fg')
            self._draw()
        if 'font' in options:
            self.text.configure(font=options.pop('font'))
        if 'wraplength' in options:
            self.text.configure(wraplength=options.pop('wraplength'))
        if 'command' in options:
            self._command = options.pop('command')
        options.pop('highlightthickness', None)
        options.pop('highlightbackground', None)
        options.pop('activebackground', None)
        options.pop('activeforeground', None)
        options.pop('selectcolor', None)
        options.pop('bd', None)
        result = tk.Frame.configure(self, **options) if options else None
        self._draw()
        return result

    config = configure

    def cget(self, key):
        if key == 'state':
            return self._state
        if key == 'text':
            return self.text.cget('text')
        return tk.Frame.cget(self, key)

    def invoke(self):
        self._toggle()

    def destroy(self):
        if getattr(self, '_trace', None) is not None:
            try:
                self._variable.trace_remove('write', self._trace)
            except Exception:
                pass
            self._trace = None
        super().destroy()


class HudRule(tk.Canvas):
    """One-pixel divider, horizontal by default."""

    def __init__(self, master, vertical=False, color=None, **options):
        self._vertical = vertical
        self._color = color or COLORS['line_strong']
        size = px(1)
        super().__init__(master, width=size if vertical else 1, height=1 if vertical else size,
                         bg=self._surface(master), highlightthickness=0, bd=0, **options)
        self.bind('<Configure>', lambda event: self._draw(), add='+')

    @staticmethod
    def _surface(master):
        try:
            return master.cget('bg')
        except Exception:
            return COLORS['card']

    def _draw(self):
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.delete('all')
        if self._vertical:
            mid = width / 2
            self.create_line(mid, 0, mid, height, fill=self._color)
        else:
            mid = height / 2
            self.create_line(0, mid, width, mid, fill=self._color)


class SegmentedBar(tk.Canvas):
    """Continuous percentage bar; turns red and blinks when nearly empty."""

    def __init__(self, master, segments=None, height=None, **options):
        self.segments = int(segments or CONTROL['segments'])
        self._height = px(height or CONTROL['bar'])
        self._percent = 0.
        self._color = COLORS['green']
        self._pulse_job, self._pulse_on = None, True
        super().__init__(master, height=self._height, bg=self._surface(master),
                         highlightthickness=0, bd=0, **options)
        self.bind('<Configure>', lambda event: self._draw(), add='+')

    @staticmethod
    def _surface(master):
        try:
            return master.cget('bg')
        except Exception:
            return COLORS['card']

    def set(self, percent, color='green'):
        color = COLORS.get(color, color)
        percent = max(0., min(100., float(percent)))
        danger = 0 < percent < 20
        if danger:
            # 低于 20% 一律报警红并呼吸闪烁，不依赖调用方是否已经判红。
            color = COLORS['red']
        if (percent, color) == (self._percent, self._color):
            if danger:
                self._start_pulse()
            return
        self._percent, self._color = percent, color
        if danger:
            self._start_pulse()
        else:
            self._stop_pulse()
        self._draw()

    def _start_pulse(self):
        if self._pulse_job is None:
            self._pulse_job = self.after(520, self._pulse_tick)

    def _stop_pulse(self):
        if self._pulse_job is not None:
            try:
                self.after_cancel(self._pulse_job)
            except tk.TclError:
                pass
            self._pulse_job = None
        self._pulse_on = True

    def _pulse_tick(self):
        self._pulse_job = None
        self._pulse_on = not self._pulse_on
        self._draw()
        if self.winfo_exists():
            self._pulse_job = self.after(520, self._pulse_tick)

    def _draw(self):
        width, height = max(2, self.winfo_width()), max(2, self.winfo_height())
        self.delete('all')
        color = self._color if self._pulse_on else darken(self._color, .45)
        self.create_rectangle(0, 0, width - 1, height - 1,
                              fill=COLORS['track'], outline=COLORS['line'])
        filled = width * self._percent / 100
        if filled >= 1:
            self.create_rectangle(0, 0, max(1, filled - 1), height - 1, fill=color, outline='')

    def destroy(self):
        self._stop_pulse()
        super().destroy()


class HudReadout(tk.Canvas):
    """One axis of the XYZ gauge: a bordered cell, a coloured axis letter, mono digits."""

    def __init__(self, master, axis, accent='green', variable=None, height=None, **options):
        self._height = px(height or 54)
        self._axis = axis
        self._accent = COLORS.get(accent, accent)
        self._color = COLORS['green']
        self._variable = variable
        super().__init__(master, height=self._height, bg=self._surface(master),
                         highlightthickness=0, bd=0, **options)
        self.bind('<Configure>', lambda event: self._draw(), add='+')
        if variable is not None:
            self._trace = variable.trace_add('write', lambda *_: self._draw())

    @staticmethod
    def _surface(master):
        try:
            return master.cget('bg')
        except Exception:
            return COLORS['card']

    def set_color(self, color):
        color = COLORS.get(color, color)
        if color != self._color:
            self._color = color
            self._draw()

    def value(self):
        return self._variable.get() if self._variable is not None else ''

    def _draw(self):
        width, height = max(4, self.winfo_width()), max(4, self.winfo_height())
        layout = (width, height, self._axis, self._accent)
        if layout == getattr(self, '_layout_key', None):
            self.itemconfigure(self._value_item, text=self.value(), fill=self._color)
            return
        self._layout_key = layout
        self.delete('all')
        self.create_rectangle(0, 0, width - 1, height - 1,
                              fill=COLORS['card_hi'], outline=COLORS['line'], width=1)
        self.create_text(7, 8, text=self._axis, fill=self._accent, anchor='nw',
                         font=mono_font(TYPE['micro'], True))
        self.create_text(width - 7, 8, text='m', fill=COLORS['faint'], anchor='ne',
                         font=mono_font(TYPE['micro']))
        self._value_item = self.create_text(width / 2, height * .65, text=self.value(),
                                            fill=self._color, anchor='center',
                                            font=mono_font(TYPE['coord']))

    def destroy(self):
        if getattr(self, '_trace', None) is not None and self._variable is not None:
            try:
                self._variable.trace_remove('write', self._trace)
            except Exception:
                pass
            self._trace = None
        super().destroy()


def attach_brackets(frame, length=13, width=2):
    """Compatibility shim: the v2 skin has no viewfinder brackets."""
    return None


class HudCard(tk.Frame):
    """Flat panel with a 1px edge; the name is kept so call sites stay stable."""

    def __init__(self, master, **options):
        options.setdefault('bg', COLORS['card'])
        options.setdefault('highlightthickness', 1)
        options.setdefault('highlightbackground', COLORS['line_strong'])
        super().__init__(master, **options)
        self.brackets = None
