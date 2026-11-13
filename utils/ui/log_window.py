"""Detached window that shows the complete run log.

The bottom pane only keeps the key lines; the 完整日志 button used to stretch
that pane in place. It now opens this separate top-level window instead, so the
map / control area never has to shrink. The window mirrors the same records the
pane receives (no file reads, no polling) and stays in sync until it is closed.
"""
from collections import deque
import tkinter as tk
from tkinter import ttk

from utils.ui import theme

COLORS = theme.COLORS

STATUS_COLORS = (
    ('进行中', COLORS['muted']), ('已派发', COLORS['accent']),
    ('已确认', COLORS['green']), ('已完成', COLORS['green']),
    ('失败', COLORS['red']), ('未确认', COLORS['amber']),
)


def append_log_lines(widget, lines, max_lines):
    """Append a batch while keeping the current reading position."""
    at_bottom = widget.yview()[1] > .999
    chunks = []
    for text, status in lines:
        chunks.extend((text, (status,) if status else ()))
    widget.configure(state='normal')
    try:
        if chunks:
            widget.insert('end', *chunks)
        excess = int(widget.index('end-1c').split('.')[0]) - 1 - max_lines
        if excess > 0:
            widget.delete('1.0', f'{excess + 1}.0')
        if at_bottom:
            widget.see('end')
    finally:
        widget.configure(state='disabled')


class CollectionLog:
    """Collection history and its compact view in the shared log host."""

    def __init__(self, owner, host):
        self.history = deque(maxlen=2000)
        self.views = []
        self.page = owner.frame(host, card=True, border=False)
        self.status = owner.label(self.page, '采集状态：空闲 · 暂无运行记录', color='muted')
        self.status.pack(fill='x')
        self.text = tk.Text(self.page, wrap='word', height=3, width=40, state='disabled',
                            bg=COLORS['card'], fg=COLORS['text'], font=theme.font(11))
        scroll = ttk.Scrollbar(self.page, orient='vertical', command=self.text.yview)
        scroll.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        self.text.configure(yscrollcommand=scroll.set)
        for status, color in STATUS_COLORS:
            self.text.tag_configure(status, foreground=color)

    def apply_events(self, events):
        lines = []
        for event in events:
            state = event.get('state')
            message = ' '.join(str(event.get('message', '')).split())
            if not message:
                continue
            label, style = COLLECTION_STATES.get(state, ('状态', '进行中'))
            if state == 'reached' and message.startswith('已完成采集与等待'):
                message = message.replace('已完成采集与等待', '已结束采集交互与等待，产出未确认', 1)
                label, style = '结果', '未确认'
            elif state == 'reached' and message.startswith('已确认采到（背包增量核对）'):
                label, style = '结果', '已确认'
            if state == 'playing' and event.get('waypoint') is not None:
                label = '寻路'
            stamp = str(event.get('at', ''))[11:19] or '—'
            cycle, loops = event.get('cycle'), event.get('loop_count')
            prefix = f'循环 {cycle}/{loops} · ' if cycle and loops else ''
            lines.append((f'{stamp} [{label}] {prefix}{message}\n', style))
        if not lines:
            return
        self.history.extend(lines)
        try:
            self.status.configure(text='采集状态：'+label)
            append_log_lines(self.text, lines, 500)
        except tk.TclError:
            pass
        for view in self.views[:]:
            try:
                if view.alive():
                    view.append(lines)
                else:
                    self.remove_log_view(view)
            except tk.TclError:
                self.remove_log_view(view)

    def add_log_view(self, view):
        if view not in self.views:
            view.set_lines(self.history)
            self.views.append(view)

    def remove_log_view(self, view):
        if view in self.views:
            self.views.remove(view)


COLLECTION_STATES = {
    'preparation': ('准备', '进行中'), 'playback_wait': ('等待', '未确认'),
    'playing': ('运行', '进行中'), 'navigating': ('寻路', '进行中'),
    'calibrating': ('导航', '进行中'), 'recovering': ('恢复', '未确认'),
    'blocked': ('受阻', '未确认'), 'paused': ('暂停', '未确认'),
    'collecting': ('采集', '进行中'), 'collection_wait': ('等待交互', '已派发'),
    'reached': ('到达', '进行中'), 'skipped': ('跳过', '未确认'),
    'supplementing': ('补采', '进行中'), 'cycle_finished': ('循环', '已完成'),
    'cycle_partial': ('循环', '未确认'), 'finished': ('完成', '已完成'),
    'partial': ('提醒', '未确认'), 'stopped': ('停止', '未确认'),
    'error': ('失败', '失败'), 'tool_retry': ('工具', '未确认'),
    'equipping': ('装备', '进行中'), 'repairing': ('维修', '进行中'),
    'eating': ('体力', '进行中'), 'food_paused': ('体力暂停', '未确认'),
    'repair_paused': ('维修暂停', '未确认'), 'equip_paused': ('装备暂停', '未确认'),
}


class FullLogWindow:
    """Read-only live mirror of the complete log, in its own window."""

    MAX_LINES = 2000

    def __init__(self, parent, catalog, name='自动烹饪'):
        self.catalog = None
        self.positions = {}
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
        self.title_label = tk.Label(header, bg=COLORS['bg'], fg=COLORS['green'],
                                    font=theme.font(13, bold=True))
        self.title_label.pack(side='left')
        tk.Button(header, text='关闭', command=self.close, relief='flat', bd=0,
                  highlightthickness=1, highlightbackground=COLORS['button_border'],
                  bg=COLORS['button'], fg=COLORS['text'],
                  activebackground=COLORS['button_active'], activeforeground=COLORS['text'],
                  disabledforeground=COLORS['disabled'], cursor='hand2',
                  font=theme.font(12), padx=theme.px(10), pady=theme.px(3)).pack(side='right')
        self.count_label = tk.Label(header, text='', bg=COLORS['bg'], fg=COLORS['muted'],
                                    font=theme.font(11))
        self.count_label.pack(side='right', padx=(0, theme.px(10)))

        # Footer first so the expanding text area can never squeeze it out.
        tk.Label(self.top, anchor='w', bg=COLORS['bg'], fg=COLORS['muted'],
                 font=theme.font(10),
                 text=f'完整记录随任务实时追加 · 最多保留最近 {self.MAX_LINES} 行 · '
                      '内容与主窗口日志同源').pack(
            side='bottom', fill='x', padx=theme.px(14), pady=(theme.px(6), theme.px(8)))

        body = tk.Frame(self.top, bg=COLORS['card'], highlightbackground=COLORS['line'],
                        highlightthickness=1, bd=0)
        body.pack(side='top', fill='both', expand=True, padx=theme.px(12), pady=(0, theme.px(4)))
        self.text = tk.Text(body, wrap='word', state='disabled', bd=0, highlightthickness=0,
                            height=6, width=40,
                            bg=COLORS['card'], fg=COLORS['text'], font=theme.font(11),
                            padx=theme.px(8), pady=theme.px(6))
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=scroll.set)
        for status, color in STATUS_COLORS:
            self.text.tag_configure(status, foreground=color)

        self.set_source(catalog, name)

    def set_source(self, source, name):
        if not self.alive() or source is self.catalog:
            return
        if self.catalog is not None:
            self.positions[self.catalog] = (self._at_bottom(), self.text.yview()[0])
            self.catalog.remove_log_view(self)
        self.catalog = source
        self.top.title(f'心动小镇 · {name}完整日志')
        self.title_label.configure(text=f'运行日志 · {name}')
        source.add_log_view(self)
        at_bottom, position = self.positions.get(source, (True, 1.))
        if not at_bottom:
            self.text.yview_moveto(position)

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
        if reset:
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
        append_log_lines(self.text, lines, self.MAX_LINES)
        if reset:
            self.text.see('end')
        self._update_count()

    def _at_bottom(self):
        try:
            return self.text.yview()[1] > 0.999
        except Exception:
            return True

    def _update_count(self):
        try:
            line, column = map(int, self.text.index('end-1c').split('.'))
            lines = line - 1 + bool(column)
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
