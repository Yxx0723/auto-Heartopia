"""Collection / navigation runtime log for the shared bottom panel.

All Tk operations remain on the main thread. The playback worker only pushes
small status dictionaries to its independent bounded report queue.
"""
from collections import deque
import tkinter as tk
from tkinter import ttk

from utils.ui import theme
from utils.ui.log_window import STATUS_COLORS


STATUS_TAGS = {
    'finished': '已完成',
    'reached': '已确认',
    'cycle_finished': '已完成',
    'error': '失败',
    'blocked': '失败',
    'repair_paused': '未确认',
    'equip_paused': '未确认',
    'food_paused': '未确认',
    'partial': '未确认',
    'skipped': '未确认',
    'stopped': '未确认',
    'paused': '未确认',
    'playback_wait': '已派发',
    'collecting': '进行中',
    'playing': '进行中',
    'recovering': '进行中',
    'repairing': '进行中',
    'eating': '进行中',
    'equipping': '进行中',
}


def format_collection_event(event):
    """Produce a color-tagged entry without reading the game or touching Tk."""
    if not isinstance(event, dict):
        return None
    message = str(event.get('message') or '').strip()
    if not message:
        return None
    state = str(event.get('state') or 'unknown')
    status = STATUS_TAGS.get(state, '进行中')
    timestamp = str(event.get('at') or '')
    clock = timestamp.split('T')[-1][:8] if 'T' in timestamp else (timestamp[:8] or '--:--:--')
    context = []
    cycle, loop_count = event.get('cycle'), event.get('loop_count')
    if isinstance(cycle, int) and isinstance(loop_count, int) and loop_count > 1:
        context.append(f'循环 {cycle}/{loop_count}')
    waypoint, count = event.get('waypoint'), event.get('waypoint_count')
    if isinstance(waypoint, int) and waypoint >= 0 and isinstance(count, int) and count > 0:
        context.append(f'点位 {waypoint}/{count}')
    prefix = (' | '.join(context) + ' | ') if context else ''
    return (f'● {clock} | {prefix}{status} | {message[:600]}\n', status)


class CollectionLogPanel:
    """Bounded in-memory collection log, compatible with FullLogWindow."""

    def __init__(self, owner, runtime_parent):
        self.runtime_page = owner.frame(runtime_parent, card=True, border=False)
        self.runtime_status = tk.StringVar(value='采集状态：空闲 · 等待任务')
        owner.label(self.runtime_page, '', textvariable=self.runtime_status,
                    anchor='w', wraplength=390, justify='left').pack(fill='x')
        area = owner.frame(self.runtime_page, card=True, border=False)
        area.pack(fill='both', expand=True)
        self.runtime_log = tk.Text(area, height=3, width=42, wrap='word',
                                   state='disabled', font=theme.font(11),
                                   bg=theme.COLORS['card'], fg=theme.COLORS['text'],
                                   relief='flat', bd=0, highlightthickness=0,
                                   padx=theme.px(6), pady=theme.px(3))
        for name, color in STATUS_COLORS:
            self.runtime_log.tag_configure(name, foreground=color)
        self.runtime_log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(area, orient='vertical', command=self.runtime_log.yview)
        scroll.pack(side='right', fill='y')
        self.runtime_log.configure(yscrollcommand=scroll.set)
        self.log_buffer = deque(maxlen=2000)
        self.log_views = []
        self.runtime_lines = 0

    def apply_log_events(self, events):
        """Append all queued playback reports, including superseded states."""
        rows = []
        for event in events:
            row = format_collection_event(event)
            if row is not None:
                rows.append(row)
                self.runtime_status.set('采集状态：' + row[1] + ' · ' +
                                        str(event['message'])[:120])
        if not rows:
            return
        self.log_buffer.extend(rows)
        try:
            at_bottom = self.runtime_log.yview()[1] > 0.999
            self.runtime_log.configure(state='normal')
            for message, status in rows:
                self.runtime_log.insert('end', message, (status,))
            self.runtime_lines += len(rows)
            if self.runtime_lines > 500:
                self.runtime_log.delete('1.0', f'{self.runtime_lines - 500 + 1}.0')
                self.runtime_lines = 500
            self.runtime_log.configure(state='disabled')
            if at_bottom:
                self.runtime_log.see('end')
        except Exception:
            # Logging is display-only; it must never interrupt a game task.
            try:
                self.runtime_log.configure(state='disabled')
            except Exception:
                pass
        self._broadcast_log(rows)

    def log_lines(self):
        return list(self.log_buffer)

    def add_log_view(self, view):
        if view not in self.log_views:
            self.log_views.append(view)
        view.set_lines(self.log_lines())

    def remove_log_view(self, view):
        try:
            self.log_views.remove(view)
        except ValueError:
            pass

    def _broadcast_log(self, lines):
        for view in list(self.log_views):
            try:
                if view.alive():
                    view.append(lines)
                else:
                    self.remove_log_view(view)
            except Exception:
                self.remove_log_view(view)
