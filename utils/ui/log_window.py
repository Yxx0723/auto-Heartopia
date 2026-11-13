"""Detached window that shows the complete run log.

The bottom pane only keeps the key lines; the 完整日志 button used to stretch
that pane in place. It now opens this separate top-level window instead, so the
map / control area never has to shrink. The window mirrors the same records the
pane receives (no file reads, no polling) and stays in sync until it is closed.
"""
import tkinter as tk
from tkinter import ttk

from utils.ui import theme

COLORS = theme.COLORS

STATUS_COLORS = (
    ('进行中', COLORS['muted']), ('已派发', COLORS['accent']),
    ('已确认', COLORS['green']), ('已完成', COLORS['green']),
    ('失败', COLORS['red']), ('未确认', COLORS['amber']),
)


class FullLogWindow:
    """Read-only live mirror of the complete log, in its own window."""

    MAX_LINES = 2000

    def __init__(self, parent, catalog):
        self.catalog = catalog
        self.closed = False
        self.top = tk.Toplevel(parent)
        self.top.title('心动小镇 · 运行日志（完整记录）')
        self.top.configure(bg=COLORS['bg'])
        self.top.minsize(theme.px(460), theme.px(260))
        self.top.geometry(self._geometry(parent))
        self.top.transient(parent)
        self.top.protocol('WM_DELETE_WINDOW', self.close)

        header = tk.Frame(self.top, bg=COLORS['bg'])
        header.pack(side='top', fill='x', padx=theme.px(12), pady=(theme.px(10), theme.px(6)))
        tk.Label(header, text='运行日志 · 完整记录', bg=COLORS['bg'], fg=COLORS['green'],
                 font=theme.font(13, bold=True)).pack(side='left')
        tk.Button(header, text='关闭', command=self.close, relief='flat', bd=0,
                  highlightthickness=1, highlightbackground=COLORS['button_border'],
                  bg=COLORS['button'], fg=COLORS['text'],
                  activebackground=COLORS['button_active'], activeforeground=COLORS['text'],
                  disabledforeground=COLORS['disabled'], cursor='hand2',
                  font=theme.font(12), padx=theme.px(10), pady=theme.px(3)).pack(side='right')
        self.count_label = tk.Label(header, text='', bg=COLORS['bg'], fg=COLORS['muted'],
                                    font=theme.font(11))
        self.count_label.pack(side='right', padx=(0, theme.px(10)))

        body = tk.Frame(self.top, bg=COLORS['card'], highlightbackground=COLORS['line'],
                        highlightthickness=1, bd=0)
        body.pack(side='top', fill='both', expand=True, padx=theme.px(12))
        self.text = tk.Text(body, wrap='word', state='disabled', bd=0, highlightthickness=0,
                            bg=COLORS['card'], fg=COLORS['text'], font=theme.font(11),
                            padx=theme.px(8), pady=theme.px(6))
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=scroll.set)
        for status, color in STATUS_COLORS:
            self.text.tag_configure(status, foreground=color)

        tk.Label(self.top, anchor='w', bg=COLORS['bg'], fg=COLORS['muted'],
                 font=theme.font(10),
                 text=f'完整记录随任务实时追加 · 最多保留最近 {self.MAX_LINES} 行 · '
                      '内容与主窗口日志同源，不会丢弃').pack(
            side='top', fill='x', padx=theme.px(14), pady=(theme.px(6), theme.px(8)))

        catalog.add_log_view(self)

    # -- placement --------------------------------------------------------
    @staticmethod
    def _geometry(parent):
        width, height = theme.px(720), theme.px(440)
        try:
            x = parent.winfo_rootx() + theme.px(28)
            y = parent.winfo_rooty() + theme.px(40)
        except Exception:
            x = y = theme.px(80)
        return f'{width}x{height}+{max(0, x)}+{max(0, y)}'

    # -- state ------------------------------------------------------------
    def alive(self):
        if self.closed:
            return False
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def raise_window(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
        except Exception:
            pass

    # -- content ----------------------------------------------------------
    def set_lines(self, lines):
        """Replace everything (used when the window opens)."""
        self._write(list(lines), reset=True)

    def append(self, lines):
        """Append new records without stealing the user's scroll position."""
        self._write(list(lines), reset=False)

    def _write(self, lines, reset):
        if not self.alive():
            return
        at_bottom = self._at_bottom()
        self.text.configure(state='normal')
        if reset:
            self.text.delete('1.0', 'end')
        for text, status in lines:
            self.text.insert('end', text, (status,) if status else ())
        total = int(self.text.index('end-1c').split('.')[0])
        if total > self.MAX_LINES:
            self.text.delete('1.0', f'{total - self.MAX_LINES + 1}.0')
        self.text.configure(state='disabled')
        if reset or at_bottom:
            self.text.see('end')
        self._update_count()

    def _at_bottom(self):
        try:
            return self.text.yview()[1] > 0.999
        except Exception:
            return True

    def _update_count(self):
        try:
            lines = max(1, int(self.text.index('end-1c').split('.')[0]))
            self.count_label.configure(text=f'{lines} 行 · 只读')
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.catalog.remove_log_view(self)
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
