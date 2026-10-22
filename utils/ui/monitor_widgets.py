"""Tk primitives, HUD buttons, stepper and ttk theming shared by the native layout."""
import tkinter as tk
from tkinter import ttk

from utils.ui.hud_kit import (HudButton, HudCard, HudCheck, HudReadout, HudRule, SegmentedBar,
                              mono_family, mono_font, ui_family, ui_font)
from utils.ui.ui_tokens import COLORS, CONTROL, TYPE, px

UI_FONT = ui_family()
MONO_FONT = mono_family()

__all__ = ['DisplayValue', 'UiButton', 'Stepper', 'MonitorWidgets', 'configure_changed',
           'mono_font', 'ui_font', 'HudButton', 'HudCard', 'HudCheck', 'HudReadout',
           'HudRule', 'SegmentedBar', 'UI_FONT', 'MONO_FONT']


class DisplayValue(tk.StringVar):
    """Read-only display fields must not invalidate Tk layout when unchanged."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.display_value = self.get()

    def set(self, value):
        value = str(value)
        if value != getattr(self, 'display_value', None):
            super().set(value)
            self.display_value = value


def configure_changed(widget, **options):
    changed = {key: value for key, value in options.items() if str(widget.cget(key)) != str(value)}
    if changed:
        widget.configure(**changed)


UiButton = HudButton


class Stepper(tk.Frame):
    """30px numeric field with −/+ keys; the bound StringVar always stays valid."""

    def __init__(self, master, variable, minimum, maximum, increment=1, width=54,
                 integer=False, command=None):
        super().__init__(master, bg=COLORS['card_hi'], highlightthickness=CONTROL['border'],
                         highlightbackground=COLORS['line'], height=px(CONTROL['height']),
                         width=px(width), takefocus=0)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.variable, self.minimum, self.maximum = variable, minimum, maximum
        self.increment, self.integer, self.command = increment, integer, command
        self.state = 'normal'
        key = dict(relief='flat', bd=0, bg=COLORS['card_hi'], fg=COLORS['muted'],
                   activebackground=COLORS['button_hover'], activeforeground=COLORS['text'],
                   disabledforeground=COLORS['disabled_text'], highlightthickness=0,
                   font=mono_font(TYPE['label']), padx=0, pady=0, width=2, takefocus=0,
                   text='−', command=self.step_down)
        self.minus = HudButton(self, **key)
        self.minus.pack(side='left', fill='y')
        self.plus = HudButton(self, **{**key, 'text': '+', 'command': self.step_up})
        self.plus.pack(side='right', fill='y')
        self.value = tk.Entry(self, textvariable=variable, relief='flat', bd=0,
                              bg=COLORS['card_hi'], fg=COLORS['green'], justify='center',
                              insertbackground=COLORS['green'], disabledbackground=COLORS['card'],
                              disabledforeground=COLORS['disabled_text'], highlightthickness=0,
                              font=mono_font(TYPE['value']), width=3)
        self.value.pack(side='left', fill='both', expand=True)
        self.value.bind('<FocusOut>', self._commit)
        self.value.bind('<Return>', lambda event: self._commit() or 'break')
        self.bind('<MouseWheel>', self._wheel)
        # 发光下划线：数字输入框的 HUD 提示线。
        tk.Frame(self, bg=COLORS['green'], height=px(1)).place(
            x=0, rely=1.0, relwidth=1.0, anchor='sw', height=px(1))

    def cget(self, key):
        if key == 'state':
            return self.state
        return super().cget(key)

    def configure(self, **options):  # noqa: A003 - mirrors the Tk API
        if 'state' in options:
            self.set_state(options.pop('state'))
            if not options:
                return None
        return super().configure(**options)

    config = configure

    def set_state(self, state):
        self.state = state
        for widget in (self.value, self.minus, self.plus):
            widget.configure(state=state)
        self.configure(highlightbackground=COLORS['line'] if state == 'disabled' else COLORS['line_strong'])

    def set_value(self, value, notify=True):
        value = min(self.maximum, max(self.minimum, float(value)))
        text = str(int(round(value))) if self.integer else f'{value:.1f}'
        self.variable.set(text)
        if notify and self.command:
            self.command(text)
        return text

    def step(self, direction):
        try:
            current = float(self.variable.get())
        except (TypeError, ValueError):
            current = float(self.minimum)
        self.set_value(current + direction * self.increment)

    def step_up(self):
        if self.state == 'normal':
            self.step(1)

    def step_down(self):
        if self.state == 'normal':
            self.step(-1)

    def _wheel(self, event):
        if self.state != 'normal' or not getattr(event, 'delta', 0):
            return 'break'
        self.step(1 if event.delta > 0 else -1)
        return 'break'

    def _commit(self, event=None):
        try:
            self.set_value(float(self.variable.get()), notify=False)
        except (TypeError, ValueError):
            self.set_value(float(self.minimum), notify=False)
        return 'break'


class MonitorWidgets:
    @staticmethod
    def frame(parent, card=False, border=None, **options):
        if card:
            return HudCard(parent, **options)
        return tk.Frame(parent, bg=COLORS['bg'], **options)

    @staticmethod
    def label(parent, text='', color='text', font=None, **options):
        return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=COLORS[color],
                        font=font or ui_font(), **options)

    @staticmethod
    def separator(parent, vertical=True):
        """Hairline that fades at both ends; groups controls without a new card."""
        return HudRule(parent, vertical=vertical)

    @staticmethod
    def rule(parent, color='line_strong'):
        """Full-width dividing rule; the caller decides pack or grid placement."""
        return HudRule(parent, vertical=False, color=COLORS[color])

    def section_header(self, parent, text, color='accent_dim', tag=''):
        """Section caption with a small lime bar; the latin HUD tag is ignored."""
        head = tk.Frame(parent, bg=parent.cget('bg'))
        tk.Frame(head, bg=COLORS['green'], width=px(3), height=px(11)).pack(
            side='left', padx=(0, px(7)))
        self.label(head, text, color='text', font=ui_font(TYPE['section'], True)).pack(side='left')
        return head

    def section(self, parent, text, color='accent_dim', **pack):
        """Section caption packed at the left edge of a pack-managed parent."""
        head = self.section_header(parent, text, color)
        head.pack(anchor='w', **pack)
        return head

    def button(self, parent, text, action, primary=False, small=False, **options):
        base = dict(command=action, bg=COLORS['green' if primary else 'button'],
                    fg=COLORS['primary_text' if primary else 'text'],
                    disabledforeground=COLORS['disabled_text'], cursor='hand2',
                    font=ui_font(TYPE['body']),
                    padx=8 if small else 12, pady=3,
                    highlightbackground=COLORS['line_strong'])
        base.update(options)
        return HudButton(parent, text=text, primary=primary, small=small, **base)

    def icon_button(self, parent, text, action, size=None, **options):
        """Square icon button with an exact pixel footprint."""
        size = px(CONTROL['icon'] if size is None else size)
        holder = tk.Frame(parent, bg=parent.cget('bg'), width=size, height=size)
        holder.pack_propagate(False)
        button = self.button(holder, text, action, small=True, **options)
        button.pack(fill='both', expand=True)
        holder.button = button
        return holder

    def check(self, parent, text, variable, command=None, font=None, **options):
        return HudCheck(parent, text=text, variable=variable, command=command,
                        font=font or ui_font(TYPE['body']), **options)

    def field(self, parent, key, text='', **options):
        variable = DisplayValue(self.root, value=text)
        self.fields[key] = variable
        label = self.label(parent, **options)
        label.configure(textvariable=variable)
        return label

    def configure_dropdowns(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Monitor.TCombobox', font=ui_font(TYPE['body']),
                        padding=(px(6), px(4)), arrowsize=px(14), borderwidth=CONTROL['border'],
                        foreground=COLORS['text'], fieldbackground=COLORS['card_hi'],
                        background=COLORS['card_hi'], bordercolor=COLORS['line_strong'],
                        lightcolor=COLORS['card_hi'], darkcolor=COLORS['card_hi'],
                        arrowcolor=COLORS['green'], selectbackground=COLORS['card_hi'],
                        selectforeground=COLORS['green'])
        style.map('Monitor.TCombobox',
                  fieldbackground=[('disabled', COLORS['card']), ('readonly', COLORS['card_hi'])],
                  foreground=[('disabled', COLORS['disabled_text']), ('readonly', COLORS['text'])],
                  arrowcolor=[('disabled', COLORS['disabled_text']), ('active', COLORS['glow'])],
                  background=[('active', COLORS['button_hover'])],
                  bordercolor=[('focus', COLORS['glow']), ('active', COLORS['glow']),
                               ('disabled', COLORS['line']), ('readonly', COLORS['line_strong'])])
        style.configure('TScrollbar', background=COLORS['line_strong'], troughcolor=COLORS['card'],
                        bordercolor=COLORS['card'], arrowcolor=COLORS['faint'],
                        lightcolor=COLORS['line_strong'], darkcolor=COLORS['line_strong'])
        style.map('TScrollbar', background=[('active', COLORS['green'])])
        for key, value in {'background': COLORS['card_hi'], 'foreground': COLORS['text'],
                           'selectBackground': COLORS['button_pressed'], 'selectForeground': COLORS['green'],
                           'font': ui_font(TYPE['body']), 'borderWidth': 0,
                           'highlightThickness': 0, 'selectBorderWidth': 0}.items():
            self.root.option_add(f'*TCombobox*Listbox.{key}', value)

    @staticmethod
    def dropdown(parent, variable, values, width, command=None, postcommand=None):
        # v2 皮肤用 ttk 原生下拉，保持系统箭头与 1px 边框。
        combo = ttk.Combobox(parent, textvariable=variable, values=list(values),
                             width=width, state='readonly', style='Monitor.TCombobox',
                             font=ui_font(TYPE['body']))
        if command is not None:
            combo.bind('<<ComboboxSelected>>', lambda event: command(variable.get()))
        if postcommand is not None:
            combo.configure(postcommand=postcommand)
        return combo
