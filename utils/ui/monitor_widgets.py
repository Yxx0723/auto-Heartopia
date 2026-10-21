"""Tk primitives and dropdown styling shared by the native layout."""
import tkinter as tk
from tkinter import ttk
from utils.ui.monitor_style import COLORS


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


class MonitorWidgets:
    @staticmethod
    def frame(parent, card=False, border=None, **options):
        return tk.Frame(parent, bg=COLORS['card' if card else 'bg'],
                        highlightbackground=COLORS['line'],
                        highlightthickness=int(card if border is None else border), **options)

    @staticmethod
    def label(parent, text='', color='text', font=None, **options):
        return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=COLORS[color],
                        font=font or ('Microsoft YaHei UI', -13), **options)

    def button(self, parent, text, action, primary=False, small=False):
        return tk.Button(parent, text=text, command=action, relief='flat', bd=0,
                         highlightthickness=1, highlightbackground=COLORS['line'],
                         bg=COLORS['green' if primary else 'button'],
                         fg='#1b2815' if primary else COLORS['text'],
                         activebackground='#bde379' if primary else '#2a382d',
                         activeforeground='#1b2815' if primary else COLORS['text'],
                         disabledforeground='#667362', cursor='hand2',
                         font=('Microsoft YaHei UI', -12 if small else -13),
                         padx=7 if small else 10, pady=4 if small else 6)

    def field(self, parent, key, text='', **options):
        variable = DisplayValue(self.root, value=text)
        self.fields[key] = variable
        label = self.label(parent, **options)
        label.configure(textvariable=variable)
        return label

    def configure_dropdowns(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Monitor.TCombobox', font=('Microsoft YaHei UI', -13),
                        padding=(7, 4), arrowsize=14, borderwidth=1,
                        foreground=COLORS['text'], fieldbackground=COLORS['button'],
                        background=COLORS['button'], bordercolor=COLORS['line'],
                        lightcolor=COLORS['line'], darkcolor=COLORS['line'],
                        arrowcolor=COLORS['green'], selectbackground=COLORS['button'],
                        selectforeground=COLORS['text'])
        style.map('Monitor.TCombobox',
                  fieldbackground=[('disabled', COLORS['card']), ('readonly', COLORS['button'])],
                  foreground=[('disabled', '#667362')],
                  arrowcolor=[('disabled', '#667362')],
                  background=[('active', '#2a382d')],
                  bordercolor=[('focus', COLORS['green']), ('active', '#667362')])
        style.configure('TScrollbar', background=COLORS['line'], troughcolor=COLORS['button'],
                        bordercolor=COLORS['button'], arrowcolor=COLORS['muted'])
        style.map('TScrollbar', background=[('active', '#667362')])
        for key, value in {'background': COLORS['button'], 'foreground': COLORS['text'],
                           'selectBackground': COLORS['green'], 'selectForeground': '#1b2815',
                           'font': ('Microsoft YaHei UI', -15), 'borderWidth': 0,
                           'highlightThickness': 0, 'selectBorderWidth': 0}.items():
            self.root.option_add(f'*TCombobox*Listbox.{key}', value)

    @staticmethod
    def dropdown(parent, variable, values, width, command=None, postcommand=None):
        combo = ttk.Combobox(parent, textvariable=variable, values=values, width=width,
                             state='readonly', height=8, style='Monitor.TCombobox',
                             cursor='hand2', font=('Microsoft YaHei UI', -13))
        if command:
            combo.bind('<<ComboboxSelected>>', lambda event: command(variable.get()))
        if postcommand:
            combo.configure(postcommand=postcommand)
        # A wheel gesture over a closed field must not silently switch routes/settings.
        # The popup list keeps its native wheel scrolling and keyboard navigation.
        combo.bind('<MouseWheel>', lambda event: 'break')
        combo.bind('<Button-4>', lambda event: 'break')
        combo.bind('<Button-5>', lambda event: 'break')
        return combo
