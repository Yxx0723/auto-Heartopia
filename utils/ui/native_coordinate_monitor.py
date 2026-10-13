"""Native Tk coordinate window. No HTML, browser, HTTP server or sockets."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from utils.core.coordinate_monitor_view import MonitorView, locator_details
from utils.core.coordinate_ce_table import current_position_table
from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core import thresholds as policy
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.ui.coordinate_monitor import CoordinateMonitor
from utils.ui.point_playback import PointPlayback
from utils.ui.point_wait_editor import PointWaitEditor


COLORS = {'bg': '#101714', 'header': '#151e18', 'card': '#19221d', 'plot': '#151e19',
          'line': '#2b382f', 'text': '#edf3e8', 'muted': '#9ba99c', 'green': '#cef18b',
          'red': '#f1a19a', 'blue': '#a8c8ee', 'button': '#202c24'}


def hexadecimal(value):
    return f'0x{value:X}' if isinstance(value, int) else '—'


class MonitorWorker:
    """One owner for the reader; Tk consumes snapshots without blocking on it."""
    def __init__(self, monitor):
        self.monitor = monitor
        self.results = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.reconnecting = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='coordinate-reader', daemon=True)
        self.thread.start()

    def set_paused(self, paused):
        self.paused.set() if paused else self.paused.clear()

    def reconnect(self):
        self.reconnecting.set()

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def poll_once(self):
        if self.reconnecting.is_set():
            self.reconnecting.clear()
            self.monitor.reconnect()
        if self.paused.is_set():
            return
        status = self.monitor.status()
        try:
            self.results.put_nowait(status)
        except queue.Full:
            self.take_latest()
            self.results.put_nowait(status)

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.poll_once()
                self.stop_event.wait(policy.COORD_MONITOR_REFRESH_SECONDS)
        finally:
            self.monitor.close()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        else:
            self.monitor.close()


class CoordinateWindow:
    def __init__(self, root, monitor, resources, clock=time.monotonic, point_path=None, wall_clock=None,
                 playback_runner=None):
        self.root, self.resources, self.clock = root, Path(resources), clock
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        data_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) \
            else Path(__file__).resolve().parents[2]
        self.routes = PointRoutes(point_path or data_dir / '点位' / '我的点位.json')
        self.route_name = self.routes.selected_name()
        self.point_store = self.routes.store(self.route_name)
        self.saved_points, self.points_error = [], ''
        try:
            self.saved_points = self.point_store.load()['points']
        except (OSError, ValueError) as exc:
            self.points_error = f'点位文件读取失败：{exc}'
        self.view = MonitorView()
        self.worker = MonitorWorker(monitor)
        self.playback = PointPlayback(self.resources / 'profile.json', runner=playback_runner)
        self.playback_status = {'state': 'idle', 'message': '按保存顺序回放 · F6开始/暂停 · F9停止'}
        self.zoom = 1.
        self.details_visible = False
        self.closed = False
        self.fields = {}
        self.last_path_text = ''
        self.wait_editor = None
        root.title('心动小镇 · 坐标观测台（窗口版）')
        root.configure(bg=COLORS['bg'])
        root.geometry('1180x880+50+50')
        root.minsize(1000, 840)
        icon = self.resources / 'monitor.ico'
        if icon.exists():
            root.iconbitmap(str(icon))
        root.option_add('*Font', ('Microsoft YaHei UI', -13))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.worker.start()
        self.timer = root.after(0, self.tick)

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
                         padx=10 if small else 14, pady=6 if small else 8)

    def field(self, parent, key, text='', **options):
        variable = tk.StringVar(self.root, value=text)
        self.fields[key] = variable
        label = self.label(parent, **options)
        label.configure(textvariable=variable)
        return label

    def configure_dropdowns(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Monitor.TCombobox', font=('Microsoft YaHei UI', -13),
                        padding=(10, 6), arrowsize=16, borderwidth=1,
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

    def build(self):
        self.configure_dropdowns()
        header = tk.Frame(self.root, bg=COLORS['header'], height=60)
        header.pack(fill='x')
        brand = self.label(header, '⊙  心动小镇 · 坐标观测台', font=('Microsoft YaHei UI', -16, 'bold'))
        brand.pack(side='left', padx=26, pady=17)
        self.button(header, '退出监视器', self.close, small=True).pack(side='right', padx=(8, 26))
        self.label(header, '原生窗口 · 仅读取', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='right')
        body = self.frame(self.root)
        body.pack(fill='both', expand=True, padx=26, pady=(20, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)
        intro = self.frame(body)
        intro.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        title = self.frame(intro)
        title.pack(side='left')
        self.label(title, 'LIVE POSITION', color='green', font=('Segoe UI', -10)).pack(anchor='w')
        self.label(title, '看见你的每一步。', font=('Microsoft YaHei UI', -24, 'bold')).pack(anchor='w', pady=(4, 5))
        self.label(title, '切回游戏走动，即可观察实时坐标与轨迹。', color='muted', font=('Microsoft YaHei UI', -12)).pack(anchor='w')
        actions = self.frame(intro)
        actions.pack(side='right', anchor='s', pady=5)
        self.badge = self.label(actions, '● 正在连接', color='muted', font=('Microsoft YaHei UI', -12))
        self.badge.pack(side='left', padx=(0, 12))
        self.pause_button = self.button(actions, '暂停显示', self.pause)
        self.pause_button.pack(side='left', padx=4)
        self.copy_button = self.button(actions, '复制 XYZ', self.copy_position)
        self.copy_button.pack(side='left', padx=(4, 0))
        metrics = self.frame(body)
        metrics.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        for i, (axis, text, color) in enumerate(zip('XYZ', ['水平位置', '角色高度', '水平位置'], ['red', 'green', 'blue'])):
            metrics.grid_columnconfigure(i, weight=1, uniform='metrics')
            card = self.frame(metrics, card=True)
            card.grid(row=0, column=i, sticky='ew', padx=(0 if i == 0 else 7, 0 if i == 2 else 7))
            self.label(card, f'{axis}  {text}', color=color, font=('Microsoft YaHei UI', -11, 'bold')).pack(anchor='w', padx=12, pady=(8, 0))
            self.field(card, axis, '—', font=('Consolas', -24)).pack(anchor='w', padx=12, pady=(3, 9))
        workspace = self.frame(body)
        workspace.grid(row=2, column=0, sticky='nsew')
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_columnconfigure(1, minsize=320)
        workspace.grid_rowconfigure(0, weight=1)
        plotcard = self.frame(workspace, card=True)
        plotcard.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        plotcard.grid_columnconfigure(0, weight=1)
        plotcard.grid_rowconfigure(1, weight=1)
        plothead = self.frame(plotcard, card=True)
        plothead.grid(row=0, column=0, sticky='ew')
        self.label(plothead, '平面移动轨迹  / X · Z', font=('Microsoft YaHei UI', -13, 'bold')).pack(side='left', padx=16, pady=13)
        for text, action in reversed([('−', lambda: self.set_zoom(self.zoom / 1.5)),
                                      ('＋', lambda: self.set_zoom(self.zoom * 1.5)),
                                      ('适应轨迹', lambda: self.set_zoom(1.)), ('清空轨迹', self.clear_trail)]):
            self.button(plothead, text, action, small=True).pack(side='right', padx=4, pady=8)
        self.canvas = tk.Canvas(plotcard, bg=COLORS['plot'], bd=0, highlightthickness=0, height=270)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', lambda event: self.draw())
        plotfoot = self.frame(plotcard, card=True)
        plotfoot.grid(row=2, column=0, sticky='ew')
        self.field(plotfoot, 'points', '轨迹点 0', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left', padx=15, pady=10)
        self.field(plotfoot, 'speed', '平面速度 — 单位/秒', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left', padx=16)
        self.field(plotfoot, 'scale', '刻度 —', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='right', padx=15)
        pointcard = self.frame(workspace, card=True)
        pointcard.grid(row=1, column=0, sticky='ew', padx=(0, 16), pady=(10, 0))
        routebar = self.frame(pointcard, card=True, border=False)
        routebar.pack(fill='x', padx=14, pady=(10, 0))
        self.label(routebar, '当前路线', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left', padx=(0, 6))
        self.route_var = tk.StringVar(self.root, value=self.route_name)
        self.route_menu = self.dropdown(routebar, self.route_var, [self.route_name], width=16,
                                        command=self.switch_route, postcommand=self.refresh_route_menu)
        self.route_menu.pack(side='left')
        self.route_buttons = {}
        for action, text in [('new', '新建'), ('copy', '另存为'), ('rename', '改名'), ('delete', '删除')]:
            button = self.button(routebar, text, lambda action=action: self.manage_route(action), small=True)
            button.pack(side='left', padx=(5, 0))
            self.route_buttons[action] = button
        self.clear_points_button = self.button(routebar, '清空点位', self.clear_saved_points, small=True)
        self.clear_points_button.pack(side='left', padx=(5, 0))
        self.refresh_route_menu()
        pointrow = self.frame(pointcard, card=True, border=False)
        pointrow.pack(fill='x')
        pointhead = self.frame(pointrow, card=True, border=False)
        pointhead.pack(side='left', padx=14, pady=10)
        self.save_point_button = self.button(pointhead, '＋ 添加当前点位', self.save_point, primary=True, small=True)
        self.save_point_button.pack(side='left')
        pointinfo = self.frame(pointrow, card=True, border=False)
        pointinfo.pack(side='left', fill='x', expand=True, padx=(0, 14), pady=8)
        self.field(pointinfo, 'saved_count', '已保存 0 个点', color='green',
                   font=('Microsoft YaHei UI', -12)).pack(anchor='w')
        self.saved_summary = self.field(pointinfo, 'saved_summary', '点击按钮，将当前 XYZ 添加到当前路线',
                                        color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.saved_summary.pack(fill='x', pady=(3, 0))
        sidebar = self.frame(workspace, card=True)
        sidebar.grid(row=0, column=1, rowspan=2, sticky='nsew')
        self.build_sidebar(sidebar)
        bottom = self.frame(body)
        bottom.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        toolbar = self.frame(bottom)
        toolbar.pack(fill='x')
        self.detail_button = self.button(toolbar, '▶ 指针入口详情', self.toggle_details, small=True)
        self.detail_button.pack(side='left')
        self.label(toolbar, '起点方式', color='muted', font=('Microsoft YaHei UI', -12)).pack(
            side='left', padx=(12, 6))
        self.start_mode_var = tk.StringVar(self.root, value='完整回放')
        self.start_menu = self.dropdown(toolbar, self.start_mode_var, ['完整回放', '最近点接入'], width=10)
        self.start_menu.pack(side='left')
        self.play_button = self.button(toolbar, '▶ 按顺序回放', self.start_playback, primary=True, small=True)
        self.play_button.pack(side='left', padx=(4, 5))
        self.stop_button = self.button(toolbar, '■ 停止', self.stop_playback, small=True)
        self.stop_button.pack(side='left', padx=(0, 12))
        self.label(toolbar, '循环', color='muted', font=('Microsoft YaHei UI', -12)).pack(side='left')
        self.loop_count_var = tk.StringVar(self.root, value=str(policy.COORD_PLAYBACK_DEFAULT_LOOPS))
        self.loop_count_spin = tk.Spinbox(toolbar, from_=1, to=policy.COORD_PLAYBACK_MAX_LOOPS,
            increment=1, textvariable=self.loop_count_var, width=4,
            bg=COLORS['button'], fg=COLORS['text'], buttonbackground=COLORS['button'],
            insertbackground=COLORS['text'], relief='flat', font=('Microsoft YaHei UI', -12))
        self.loop_count_spin.pack(side='left', padx=(6, 3))
        self.label(toolbar, '次', color='muted', font=('Microsoft YaHei UI', -12)).pack(
            side='left', padx=(0, 12))
        self.label(toolbar, '按 F 后等待', color='muted',
                   font=('Microsoft YaHei UI', -12)).pack(side='left')
        self.collect_wait_var = tk.StringVar(self.root, value=f'{policy.COORD_COLLECTION_DEFAULT_WAIT:g}')
        self.collect_wait_spin = tk.Spinbox(toolbar, from_=0, to=policy.COORD_COLLECTION_MAX_WAIT,
            increment=.1, textvariable=self.collect_wait_var, width=5,
            bg=COLORS['button'], fg=COLORS['text'], buttonbackground=COLORS['button'],
            insertbackground=COLORS['text'], relief='flat', font=('Microsoft YaHei UI', -12))
        self.collect_wait_spin.pack(side='left', padx=(6, 3))
        self.label(toolbar, '秒', color='muted', font=('Microsoft YaHei UI', -12)).pack(side='left')
        self.point_wait_button = self.button(toolbar, '逐点等待', self.edit_point_waits, small=True)
        self.point_wait_button.pack(side='left', padx=(8, 0))
        recoverybar = self.frame(bottom)
        recoverybar.pack(fill='x', pady=(8, 0))
        self.label(recoverybar, '连续失败上限', color='muted',
                   font=('Microsoft YaHei UI', -12)).pack(side='left', padx=(0, 8))
        self.recovery_count_var = tk.StringVar(self.root, value=str(policy.COORD_RECOVERY_DEFAULT_ATTEMPTS))
        self.recovery_menu = self.dropdown(recoverybar, self.recovery_count_var,
            [str(i) for i in range(policy.COORD_RECOVERY_MAX_ATTEMPTS + 1)], width=4)
        self.recovery_menu.pack(side='left')
        self.label(recoverybar, '次', color='muted', font=('Microsoft YaHei UI', -12)).pack(
            side='left', padx=(6, 0))
        self.recovery_jump_var = tk.BooleanVar(self.root, value=True)
        self.recovery_jump_check = tk.Checkbutton(recoverybar, text='每次先大跳（长按空格）',
            variable=self.recovery_jump_var, bg=COLORS['bg'], fg=COLORS['text'],
            activebackground=COLORS['bg'], activeforeground=COLORS['text'],
            selectcolor=COLORS['button'], font=('Microsoft YaHei UI', -12), bd=0,
            highlightthickness=0)
        self.recovery_jump_check.pack(side='left', padx=(12, 16))
        self.label(recoverybar, '0 关闭 · 通过不计次 · 恢复前进后清零', color='muted',
                   font=('Microsoft YaHei UI', -11)).pack(side='left')
        self.playback_label = self.field(bottom, 'playback', self.playback_status['message'],
                                         color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.playback_label.pack(fill='x', pady=(6, 0))
        self.pointer_box = tk.Text(bottom, height=4, bg=COLORS['card'], fg=COLORS['text'],
                                   font=('Consolas', -11), relief='flat', padx=12, pady=8, wrap='word')
        footer = self.frame(body)
        footer.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        self.label(footer, '点位自动保存到当前路线；各条路线保存在程序旁的「点位」文件夹。', color='muted', font=('Microsoft YaHei UI', -10)).pack(side='left')
        self.hint = self.label(footer, '换场景后需重新核对坐标入口', color='muted', font=('Microsoft YaHei UI', -10))
        self.hint.pack(side='right')
        self.copy_button.configure(state='disabled')
        self.save_point_button.configure(state='disabled')
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='disabled')
        if self.points_error:
            self.hint.configure(text='点位文件读取失败，请检查原文件', fg=COLORS['red'])

    def build_sidebar(self, sidebar):
        inner = self.frame(sidebar, card=True, border=False)
        inner.pack(fill='both', expand=True, padx=18, pady=10)
        inner.grid_columnconfigure(1, weight=1)
        self.label(inner, '游戏连接', color='muted').grid(row=0, column=0, sticky='w', pady=(0, 8))
        self.label(inner, '10 Hz', color='green', font=('Consolas', -12)).grid(row=0, column=1, sticky='e')
        entries = [('游戏进程', 'pid'), ('有效入口', 'valid'), ('最近采样', 'updated'),
                   ('读取状态', 'freshness'), ('模块基址', 'base'), ('坐标地址', 'address'),
                   ('数据类型', 'type')]
        for row, (name, key) in enumerate(entries, 1):
            self.label(inner, name, color='muted', font=('Microsoft YaHei UI', -11)).grid(row=row, column=0, sticky='w', pady=3, padx=(0, 16))
            self.field(inner, key, 'Float32 × 3 / XYZ' if key == 'type' else '—',
                       font=('Consolas', -12), anchor='w').grid(row=row, column=1, sticky='w', pady=3)
        self.frame(inner, card=True, height=1).grid(row=8, column=0, columnspan=2, sticky='ew', pady=6)
        for row, axis in enumerate('XYZ', 9):
            self.label(inner, axis + ' 地址', color='muted', font=('Microsoft YaHei UI', -11)).grid(row=row, column=0, sticky='w', pady=2)
            self.field(inner, 'a' + axis, '—', font=('Consolas', -12)).grid(row=row, column=1, sticky='w')
        self.message = self.label(inner, '正在连接心动小镇…', color='muted', font=('Microsoft YaHei UI', -11),
                                  wraplength=270, justify='left', anchor='w')
        self.message.grid(row=12, column=0, columnspan=2, sticky='ew', pady=(8, 8))
        controls = self.frame(inner, card=True, border=False)
        controls.grid(row=13, column=0, columnspan=2, sticky='ew')
        self.button(controls, '重新连接', self.reconnect, small=True).pack(side='left', expand=True, fill='x', padx=(0, 5))
        self.button(controls, '导出 CE 坐标表', self.export_ct, small=True).pack(side='left', expand=True, fill='x', padx=(5, 0))

    def tick(self):
        if self.closed:
            return
        status = self.worker.take_latest()
        now = self.clock()
        if status is not None:
            self.view.accept(status, now)
        self.view.expire(now)
        playback = self.playback.take_latest()
        if playback is not None:
            self.playback_status = playback
        self.render()
        self.timer = self.root.after(int(policy.COORD_MONITOR_REFRESH_SECONDS * 1000), self.tick)

    def render(self):
        view, status = self.view, self.view.status
        live = view.position is not None and not view.paused
        playing = self.playback.active
        self.route_menu.configure(state='disabled' if playing else 'readonly')
        for action, button in self.route_buttons.items():
            enabled = not playing and (action not in ('copy', 'rename') or not self.points_error)
            if action == 'delete':
                enabled = enabled and self.point_store.path.exists()
            button.configure(state='normal' if enabled else 'disabled')
        self.start_menu.configure(state='disabled' if playing else 'readonly')
        self.recovery_menu.configure(state='disabled' if playing else 'readonly')
        self.recovery_jump_check.configure(state='disabled' if playing else 'normal')
        self.collect_wait_spin.configure(state='disabled' if playing else 'normal')
        self.loop_count_spin.configure(state='disabled' if playing else 'normal')
        self.point_wait_button.configure(state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        self.clear_points_button.configure(state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        self.copy_button.configure(state='normal' if live else 'disabled')
        self.save_point_button.configure(state='normal' if live and not playing else 'disabled')
        self.play_button.configure(state='normal' if live and self.saved_points and not playing and not self.points_error else 'disabled')
        self.stop_button.configure(state='normal' if playing else 'disabled')
        self.fields['playback'].set(self.playback_status['message'])
        self.playback_label.configure(fg=COLORS['red' if self.playback_status['state'] in ('error', 'blocked') else 'green' if playing else 'muted'])
        self.fields['saved_count'].set(f'已保存 {len(self.saved_points)} 个点')
        if self.points_error:
            summary = self.points_error
        elif self.saved_points:
            point = self.saved_points[-1]
            coords = '   '.join(f'{axis} {value:.3f}' for axis, value in zip('XYZ', point['position']))
            summary = f'最近：{point["name"]}   {coords}'
        else:
            summary = '点击按钮，将当前 XYZ 添加到当前路线'
        self.fields['saved_summary'].set(summary)
        self.saved_summary.configure(fg=COLORS['red' if self.points_error else 'muted'])
        for i, axis in enumerate('XYZ'):
            self.fields[axis].set(f'{view.position[i]:.4f}' if view.position is not None else '—')
        self.fields['points'].set(f'轨迹点 {len(view.trail)}')
        self.fields['speed'].set(f'平面速度 {view.speed:.2f} 单位/秒' if view.speed is not None else '平面速度 — 单位/秒')
        if view.paused:
            badge, color, message = '● 已暂停', 'muted', '显示已暂停，当前为最后一次读数。'
            self.fields['freshness'].set('已暂停 · 最后读数')
        elif live:
            badge, color = '● 实时连接', 'green'
            message = view.notice or '角色坐标读取正常。切回游戏走动即可观察变化。'
            self.fields['freshness'].set('实时读取中')
        else:
            names = {'waiting': '等待游戏', 'connecting': '正在连接', 'error': '无法连接', 'disconnected': '连接中断'}
            badge = '● ' + names.get(status.get('state'), '等待连接')
            color = 'muted' if status.get('state') in ('waiting', 'connecting') else 'red'
            message = status.get('message', '没有实时坐标，请重新连接。')
            self.fields['freshness'].set('没有实时数据')
        self.badge.configure(text=badge, fg=COLORS[color])
        self.message.configure(text=message, fg=COLORS[color])
        if view.position is not None:
            self.fields['pid'].set(f'xdt.exe / {status.get("pid", "—")}')
            path_text, validation = locator_details(status)
            self.fields['valid'].set(validation)
            stamp = status.get('sample_at')
            self.fields['updated'].set(datetime.fromisoformat(stamp).strftime('%H:%M:%S') if stamp else '—')
            self.fields['base'].set(hexadecimal(status.get('module_base')))
            address = status.get('address')
            self.fields['address'].set(hexadecimal(address))
            for i, axis in enumerate('XYZ'):
                self.fields['a' + axis].set(hexadecimal(address + i * 4) if address is not None else '—')
        else:
            for key in ['pid', 'valid', 'updated', 'base', 'address', 'aX', 'aY', 'aZ']:
                self.fields[key].set('—')
            path_text = '当前没有可用的实时指针入口。'
        if path_text != self.last_path_text:
            self.last_path_text = path_text
            self.pointer_box.configure(state='normal')
            self.pointer_box.delete('1.0', 'end')
            self.pointer_box.insert('1.0', path_text)
            self.pointer_box.configure(state='disabled')
        self.draw()

    def draw(self):
        canvas, view = self.canvas, self.view
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 2 or h < 2:
            return
        canvas.delete('all')
        points = [(p['x'], p['z']) for p in view.trail]
        points.extend((p['position'][0], p['position'][2]) for p in self.saved_points)
        if view.position is not None:
            points.append((view.position[0], view.position[2]))
        xs, zs = zip(*points) if points else ([-5., 5.], [-5., 5.])
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        extent = max(10., max(xs) - min(xs), max(zs) - min(zs)) * 1.25 / self.zoom
        scale = max(1., min(w - 80, h - 80)) / extent
        px, pz = lambda x: w / 2 + (x - cx) * scale, lambda z: h / 2 - (z - cz) * scale
        raw = 60. / scale
        power = 10 ** math.floor(math.log10(raw))
        step = next(v * power for v in (1, 2, 5, 10) if v * power >= raw)
        self.fields['scale'].set(f'刻度 {step:g} 单位/格')
        left, right = cx - w / (2 * scale), cx + w / (2 * scale)
        low, high = cz - h / (2 * scale), cz + h / (2 * scale)
        # Bounded range iteration, never a waiting loop.
        for i in range(math.ceil(left / step), math.floor(right / step) + 1):
            x = i * step
            canvas.create_line(px(x), 0, px(x), h, fill=COLORS['line'])
            canvas.create_text(px(x) + 5, h - 15, text=f'{x:g}', fill='#718273', font=('Consolas', -10), anchor='w')
        for i in range(math.ceil(low / step), math.floor(high / step) + 1):
            z = i * step
            canvas.create_line(0, pz(z), w, pz(z), fill=COLORS['line'])
            if 32 < pz(z) < h - 30:
                canvas.create_text(10, pz(z) - 7, text=f'{z:g}', fill='#718273', font=('Consolas', -10), anchor='w')
        canvas.create_text(w - 12, 18, text='+Z ↑', fill=COLORS['muted'], font=('Consolas', -10), anchor='e')
        canvas.create_text(w - 12, h - 15, text='+X →', fill=COLORS['muted'], font=('Consolas', -10), anchor='e')
        canvas.create_text(16, 18, text='● 当前位置    ◇ 保存点位', fill=COLORS['muted'], font=('Microsoft YaHei UI', -10), anchor='w')
        saved_line = [value for point in self.saved_points
                      for value in (px(point['position'][0]), pz(point['position'][2]))]
        if len(saved_line) >= 4:
            canvas.create_line(*saved_line, fill=COLORS['blue'], width=1, dash=(4, 4))
        for point in self.saved_points:
            x, y = px(point['position'][0]), pz(point['position'][2])
            canvas.create_rectangle(x - 4, y - 4, x + 4, y + 4, outline=COLORS['blue'], width=2)
            canvas.create_text(x + 7, y + 10, text=str(point['id']), fill=COLORS['blue'],
                               font=('Consolas', -12, 'bold'), anchor='w')
        segment, coords = None, []
        for point in view.trail:
            if segment is not None and segment != point['segment']:
                if len(coords) >= 4:
                    canvas.create_line(*coords, fill='#a9d878', width=2)
                coords = []
            coords.extend((px(point['x']), pz(point['z'])))
            segment = point['segment']
        if len(coords) >= 4:
            canvas.create_line(*coords, fill='#a9d878', width=2)
        if view.position is not None:
            x, y = px(view.position[0]), pz(view.position[2])
            canvas.create_oval(x - 13, y - 13, x + 13, y + 13, fill='#2e4127', outline='')
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=COLORS['muted' if view.paused else 'green'], outline='')
        elif not view.trail and not self.saved_points:
            canvas.create_text(w / 2, h / 2, text='等待读取角色位置\n进入游戏后自动连接', fill=COLORS['muted'],
                               font=('Microsoft YaHei UI', -13), justify='center')

    def pause(self):
        paused = not self.view.paused
        if paused and self.playback.active:
            self.stop_playback()
        self.worker.set_paused(paused)
        self.view.set_paused(paused)
        self.pause_button.configure(text='继续显示' if paused else '暂停显示')
        self.render()

    def copy_position(self):
        if self.view.position is None or self.view.paused:
            return
        text = '\n'.join(f'{axis} = {value:.6f}' for axis, value in zip('XYZ', self.view.position))
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.hint.configure(text='XYZ 坐标已复制')

    def clear_trail(self):
        self.view.clear_trail()
        self.render()
        self.hint.configure(text='移动轨迹已清空 · 路线点位保留', fg=COLORS['muted'])

    def clear_saved_points(self):
        try:
            self.require_route_change()
            route = self.route_name
            data = self.point_store.load()
            count = len(data['points'])
            if count:
                if not messagebox.askyesno('清空点位', f'清空路线「{route}」的全部 {count} 个点位？\n'
                                          '路线名称保留，其他路线不变。\n'
                                          '清空前会备份到「点位/路线回收站」。', parent=self.root):
                    return
                self.require_route_change()
                if self.route_name != route:
                    raise ValueError('当前路线已切换，请重新核对后再清空。')
                data, _ = self.routes.clear_points(route, expected_data=data)
            self.saved_points, self.points_error = data['points'], ''
            self.view.clear_trail()
            self.playback_status = {'state': 'idle', 'message': f'「{route}」点位为空 · 添加点位后可回放'}
            self.render()
            self.hint.configure(text=f'已清空「{route}」的 {count} 个点位 · 已备份' if count else f'「{route}」没有点位可清空',
                                fg=COLORS['green'])
        except (OSError, ValueError) as exc:
            self.hint.configure(text='点位清空失败，原点位保留', fg=COLORS['red'])
            messagebox.showerror('清空点位失败', str(exc), parent=self.root)

    def save_point(self):
        try:
            if self.playback.active:
                raise ValueError('请先停止回放，再添加新点位。')
            snapshot = self.view.point_snapshot(self.clock())
            data = self.point_store.append(snapshot, self.wall_clock().isoformat(timespec='milliseconds'),
                                           collect_wait=self.collection_wait())
        except (OSError, ValueError) as exc:
            self.hint.configure(text='点位添加失败', fg=COLORS['red'])
            self.render()
            messagebox.showerror('点位添加失败', f'{exc}\n\n文件位置：{self.point_store.path}', parent=self.root)
            return
        self.saved_points, self.points_error = data['points'], ''
        self.point_store.require_existing = True
        self.render()
        self.hint.configure(text=f'已添加到「{self.route_name}」：{self.saved_points[-1]["name"]}', fg=COLORS['green'])

    def refresh_route_menu(self):
        self.route_menu.configure(values=self.routes.names())

    def require_route_change(self):
        if self.playback.active:
            raise ValueError('请先停止回放，再切换或管理路线。')
        if self.wait_editor is not None and self.wait_editor.root.winfo_exists():
            raise ValueError('请先关闭逐点等待窗口，再切换或管理路线。')

    def activate_route(self, name):
        self.require_route_change()
        store = self.routes.store(name)
        data = store.load()  # Validate before replacing the current route/view.
        self.point_store, self.route_name = store, name
        self.route_var.set(name)
        self.saved_points, self.points_error = data['points'], ''
        self.view.clear_trail()
        self.refresh_route_menu()
        self.playback_status = {'state': 'idle', 'message': f'当前路线「{name}」 · 按保存顺序回放 · F6开始 · F9停止'}
        self.render()
        try:
            self.routes.remember(name)
        except OSError:
            self.hint.configure(text=f'已切换到「{name}」，但未能记住本次选择', fg=COLORS['red'])
        else:
            self.hint.configure(text=f'当前路线：{name} · {len(self.saved_points)} 个点位', fg=COLORS['green'])

    def switch_route(self, name):
        try:
            self.activate_route(name)
        except (OSError, ValueError) as exc:
            self.route_var.set(self.route_name)
            messagebox.showerror('无法切换路线', str(exc), parent=self.root)

    def manage_route(self, action):
        titles = {'new': '新建路线', 'copy': '路线另存为', 'rename': '路线改名', 'delete': '删除路线'}
        try:
            self.require_route_change()
            if action == 'delete':
                if not messagebox.askyesno('删除路线', f'删除路线「{self.route_name}」？\n'
                                          '文件会移入「点位/路线回收站」，其他路线不受影响。', parent=self.root):
                    return
                self.routes.archive(self.route_name)
                name = self.routes.selected_name()
            else:
                initial = self.route_name if action == 'rename' else self.route_name + '副本' if action == 'copy' else ''
                name = simpledialog.askstring(titles[action], '请输入路线名称：',
                                              initialvalue=initial, parent=self.root)
                if name is None:
                    return
                if action == 'rename':
                    if name.strip() == self.route_name:
                        return
                    name = self.routes.rename(self.route_name, name)
                else:
                    data = self.point_store.load() if action == 'copy' else None
                    name = self.routes.create(name, data)
            self.activate_route(name)
        except (OSError, ValueError) as exc:
            self.route_var.set(self.route_name)
            messagebox.showerror(titles[action] + '失败', str(exc), parent=self.root)

    def start_playback(self):
        try:
            self.view.point_snapshot(self.clock())
            data = self.point_store.load()
            ordered_waypoints(data)
            mode = 'nearest' if self.start_mode_var.get() == '最近点接入' else 'first'
            count, jump = int(self.recovery_count_var.get()), self.recovery_jump_var.get()
            collect_wait = self.collection_wait()
            loop_count = validate_loop_count(int(self.loop_count_var.get()))
            self.playback.start(data, self.view.status.get('pid'), start_mode=mode,
                                recovery_attempts=count, jump_on_recovery=jump, collect_wait_seconds=collect_wait,
                                loop_count=loop_count)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始点位回放', str(exc), parent=self.root)
            return
        self.saved_points, self.points_error = data['points'], ''
        self.playback_status = {'state': 'playback_wait',
                                'message': f'「{self.route_name}」 · {self.start_mode_var.get()} · 循环 {loop_count} 次'
                                           f' · 连续失败上限 {count} 次 · 切回游戏按 F6 · F9 停止'}
        self.render()

    def collection_wait(self):
        return validate_collect_wait(float(self.collect_wait_var.get()))

    def edit_point_waits(self):
        try:
            if self.playback.active:
                raise ValueError('请先停止回放，再设置点位等待时间。')
            if self.wait_editor is not None and self.wait_editor.root.winfo_exists():
                self.wait_editor.root.lift()
                return self.wait_editor
            self.wait_editor = PointWaitEditor(self.root, self.point_store, self.collection_wait(),
                                               self.refresh_saved_points, COLORS)
            return self.wait_editor
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法设置等待时间', str(exc), parent=self.root)

    def refresh_saved_points(self, data):
        self.saved_points, self.points_error = data['points'], ''
        self.render()

    def stop_playback(self):
        self.playback.stop()
        self.playback_status = {'state': 'stopping', 'message': '正在停止回放并释放按键…'}
        self.render()

    def set_zoom(self, zoom):
        self.zoom = max(.2, min(10., zoom))
        self.draw()

    def reconnect(self):
        if self.playback.active:
            self.stop_playback()
        self.worker.set_paused(False)
        self.worker.take_latest()
        self.view.set_paused(False)
        self.view.invalidate('正在重新连接游戏…')
        self.pause_button.configure(text='暂停显示')
        self.worker.reconnect()
        self.render()

    def export_ct(self):
        filename = filedialog.asksaveasfilename(parent=self.root, title='导出 CE 坐标表',
                                              defaultextension='.CT', initialfile='心动小镇-角色坐标.CT',
                                              filetypes=[('Cheat Engine 坐标表', '*.CT')])
        if filename:
            try:
                self.view.point_snapshot(self.clock())
                table = current_position_table(self.view.status)
                Path(filename).write_text(table, encoding='utf-8')
                self.hint.configure(text='已导出当前会话 CE 坐标表；游戏重启后请重新导出')
            except (OSError, ValueError) as exc:
                messagebox.showerror('导出失败', str(exc), parent=self.root)

    def toggle_details(self):
        self.details_visible = not self.details_visible
        self.detail_button.configure(text=('▼' if self.details_visible else '▶') + ' 指针入口详情')
        extra = self.pointer_box.winfo_reqheight() + 6
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if self.details_visible:
            self.pointer_box.pack(fill='x', pady=(6, 0))
            self.root.minsize(1000, 840 + extra)
            self.root.geometry(f'{width}x{height + extra}')
        else:
            self.pointer_box.pack_forget()
            self.root.minsize(1000, 840)
            self.root.geometry(f'{width}x{max(840, height - extra)}')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.root.after_cancel(self.timer)
        self.playback.close()
        self.worker.close()
        self.root.destroy()


def main(playback_runner=None):
    parser = argparse.ArgumentParser(description='心动小镇原生坐标监视窗口')
    parser.add_argument('--check-output', type=Path)
    args = parser.parse_args()
    resources = Path(sys._MEIPASS) / 'monitor' if getattr(sys, 'frozen', False) \
        else Path(__file__).resolve().parents[2] / 'webui' / 'coordinate-monitor'
    monitor = CoordinateMonitor(resources / 'profile.json')
    if args.check_output:
        try:
            args.check_output.write_text(json.dumps(monitor.status(), ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            monitor.close()
        return
    root = tk.Tk()
    CoordinateWindow(root, monitor, resources, playback_runner=playback_runner)
    root.mainloop()
