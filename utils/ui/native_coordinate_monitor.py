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

from utils.core.coordinate_monitor_view import MonitorView
from utils.core.coordinate_route import valid_position
from utils.core.coordinate_ce_table import current_position_table
from utils.core.coordinate_trail_export import trail_capture_payload
from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core import thresholds as policy
from utils.core.special_patrol import make_patrol_data
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.drivers.input_task_gate import InputTaskGate
from utils.ui.coordinate_monitor import CoordinateMonitor
from utils.ui.point_playback import PointPlayback
from utils.ui.cooking_control import CookingControl
from utils.drivers.route_path_memory import RoutePathMemory
from utils.ui.point_wait_editor import PointWaitEditor
from utils.ui.scene_map_worker import SceneMapWorker
from utils.ui.map_draw_cache import MapDrawCache, Viewport, object_key, muted_color, nearby_model_key
from utils.ui.collision_display import collision_faces
from utils.ui.map_presentation import (MAP_STYLE, draw_grid, draw_chrome, draw_player, chrome_bounds,
                                      draw_waypoint_labels, draw_target_indicator)
from utils.ui.map_labels import layout_waypoints, HoverTarget, MapHover, target_indicator
from utils.drivers.mono_scene import MemorySceneMap
from utils.core.map_water_reference import MAP_DISPLAY_WATER_AREAS, MAP_DISPLAY_WATER_ISLANDS
from utils.core.map_reference_roads import REFERENCE_ROADS


COLORS = {'bg': '#101714', 'card': '#19221d', 'plot': MAP_STYLE['ground'],
          'line': '#2b382f', 'text': '#edf3e8', 'muted': '#9ba99c', 'green': '#cef18b',
          'red': '#f1a19a', 'blue': '#a8c8ee', 'amber': '#efc97b', 'button': '#202c24',
          'shrub': '#365d3e', 'shrub_outline': '#89c880',
          'blueberry': '#294a72', 'blueberry_outline': '#83b4ff',
          'raspberry': '#6a2944', 'raspberry_outline': '#ed91b3'}

PLANT_STYLES = {'blueberry': (COLORS['blueberry'], COLORS['blueberry_outline']),
                'raspberry': (COLORS['raspberry'], COLORS['raspberry_outline']),
                'bush': (COLORS['shrub'], COLORS['shrub_outline']),
                'tree': ('#326046', '#acce89'),
                'shrub': ('#3f4230', '#c3be83')}

FAUNA_STYLES = {'butterfly': ('#45345b', '#bd91f2'),
                'insect': ('#514528', '#e9c56b'),
                'bird': ('#26484e', '#6bc9d7'),
                'animal': ('#58422f', '#e8a775')}

MINERAL_STYLES = {'rock': ('#4d5b63', '#b0bec6'),
                  'ore': ('#4d5146', '#e2c17d'), 'fluorite': ('#176e77', '#80f5ed')}

MAP_PIXELS_PER_UNIT = 3.  # Default 60-pixel grid spacing represents 20 world units.
SCENE_RANGE_OPTIONS = (80, 150, 200, 300, 400)
SCENE_RANKS = {**dict.fromkeys(PLANT_STYLES, 1), **dict.fromkeys(MINERAL_STYLES, 1),
               **dict.fromkeys(FAUNA_STYLES, 2), 'roaming_oak': 3}

SCENE_CATEGORIES = {'blueberry': ('蓝莓', COLORS['blueberry_outline']),
                    'raspberry': ('树莓', COLORS['raspberry_outline']),
                    'bush': ('普通灌木', COLORS['shrub_outline']),
                    'tree': ('树木', PLANT_STYLES['tree'][1]),
                    'rock': ('岩石', MINERAL_STYLES['rock'][1]),
                    'ore': ('矿石', MINERAL_STYLES['ore'][1]),
                    'fluorite': ('无暇萤石', '#80f5ed'),
                    'roaming_oak': ('溜溜橡木', '#e8b574'),
                    'butterfly': ('蝴蝶', FAUNA_STYLES['butterfly'][1]),
                    'insect': ('其他昆虫', FAUNA_STYLES['insect'][1]),
                    'bird': ('鸟类', FAUNA_STYLES['bird'][1]),
                    'animal': ('小动物', FAUNA_STYLES['animal'][1]),
                    'shrub': ('未确认果灌', PLANT_STYLES['shrub'][1]),
                    'other': ('其他物体', '#91a69a')}


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


def user_feedback(message):
    text = str(message or '')
    folded = text.casefold()
    if '指纹' in text or '版本不匹配' in text:
        return '当前游戏版本暂不支持，请更新程序后重试。'
    if any(word in folded for word in ('权限', '拒绝访问', 'access is denied')):
        return '无法读取游戏，请以管理员身份运行程序。'
    if any(word in folded for word in ('0x', '指针', '基址', '内存', '角色对象', 'readprocessmemory',
                                       'openprocess', 'winerror', 'unityplayer.dll', 'gameassembly.dll', 'mono-2.0')):
        return '角色坐标读取中断，回放已停止；请进入角色后重新开始。' if '回放' in text \
            else '暂时无法读取角色坐标，请进入角色后重新连接。'
    return text


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
                 playback_runner=None, scene_factory=None, cooking_runner=None):
        self.root, self.resources, self.clock = root, Path(resources), clock
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        data_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) \
            else Path(__file__).resolve().parents[2]
        self.trail_export_dir = (data_dir / '道路数据' / '实走轨迹' if getattr(sys, 'frozen', False)
                                 else Path(__file__).resolve().parents[4] / 'outputs' / '道路数据' / '实走轨迹')
        self.routes = PointRoutes(point_path or data_dir / '点位' / '我的点位.json')
        self.patrol_route_selected = False
        self.route_name = self.routes.selected_name()
        self.point_store = self.routes.store(self.route_name)
        self.saved_points, self.points_error = [], ''
        try:
            self.saved_points = self.point_store.load()['points']
        except (OSError, ValueError) as exc:
            self.points_error = f'点位文件读取失败：{exc}'
        self.view = MonitorView()
        self.worker = MonitorWorker(monitor)
        diagnostics_dir = Path(point_path).parent if point_path else data_dir
        self.input_gate = InputTaskGate()
        self.playback = PointPlayback(self.resources / 'profile.json', runner=playback_runner,
                                      scene_provider=self.navigation_scene, scene_refresh=self.refresh_scene,
                                      diagnostics_path=diagnostics_dir / '日志' / '点位回放.jsonl', input_gate=self.input_gate)
        self.cooking = CookingControl(self.resources/'profile.json', data_dir/'烹饪记录',
                                      busy=lambda: self.playback.active, runner=cooking_runner, input_gate=self.input_gate)
        self.playback.input_busy = lambda: self.cooking.active
        self.cooking_dialog = None
        self.playback_status = {'state': 'idle', 'message': '按保存顺序回放 · F6开始/暂停 · F9停止'}
        self.scene_state = {'state': 'waiting', 'objects': [], 'colliders': []}
        # None means the explicit "all" mode; an empty set hides every object category.
        self.scene_categories = None
        self.scene_filters_expanded = False
        self.scene_worker = SceneMapWorker(self.resources / 'profile.json', factory=scene_factory,
                                          diagnostics_path=diagnostics_dir / '日志' / '场景读取.jsonl') if scene_factory else None
        self.zoom = 1.
        self.scene_radius = policy.COORD_SCENE_RADIUS
        self.map_view = 'nearby'
        self.map_expanded = False
        self.closed = False
        self.draw_job = None
        self.draw_signature = None
        self.window_geometry = None
        self.interacting_until = 0.
        self.map_size = None
        self.fields = {}
        self.wait_editor = None
        root.title('心动小镇 · 坐标观测台（窗口版）')
        root.configure(bg=COLORS['bg'])
        root.geometry('490x760+50+0')
        root.minsize(460, 740)
        icon = self.resources / 'monitor.ico'
        if icon.exists():
            root.iconbitmap(str(icon))
        root.option_add('*Font', ('Microsoft YaHei UI', -13))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        root.bind('<Configure>', self.window_configured, add='+')
        self.worker.start()
        if self.scene_worker:
            self.scene_worker.start()
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

    def build(self):
        self.configure_dropdowns()
        body = self.frame(self.root)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        workspace = self.frame(body)
        self.workspace = workspace
        workspace.grid(row=0, column=0, sticky='nsew')
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(0, weight=1)
        plotcard = self.frame(workspace, card=True)
        self.plotcard = plotcard
        plotcard.grid(row=0, column=0, sticky='nsew')
        plotcard.grid_columnconfigure(0, weight=1)
        plotcard.grid_rowconfigure(2, weight=1)
        plothead = self.frame(plotcard, card=True)
        plothead.grid(row=0, column=0, sticky='ew')
        self.scene_filters_toggle = self.button(plothead, '分类筛选 ▾', self.toggle_scene_filters, small=True)
        self.scene_filters_toggle.configure(padx=6, font=('Microsoft YaHei UI', -11))
        self.scene_filters_toggle.pack(side='left', padx=3, pady=4)
        self.map_expand_button = self.button(plothead, '大地图', self.toggle_map_size, small=True)
        self.map_expand_button.configure(padx=6, font=('Microsoft YaHei UI', -11))
        self.map_expand_button.pack(side='right', padx=1, pady=4)
        for text, action in reversed([('−', lambda: self.set_zoom(self.zoom / 1.5)),
                                      ('＋', lambda: self.set_zoom(self.zoom * 1.5)),
                                      ('重置缩放', self.reset_map_view)]):
            button = self.button(plothead, text, action, small=True)
            button.configure(padx=4, font=('Microsoft YaHei UI', -11))
            button.pack(side='right', padx=1, pady=4)
        map_actions = self.frame(plotcard, card=True, border=False)
        map_actions.grid(row=1, column=0, sticky='ew')
        for text, action in [('路线全览', self.show_route_overview), ('清空轨迹', self.clear_trail),
                             ('导出实走路段', self.export_walk_trail)]:
            button = self.button(map_actions, text, action, small=True)
            button.configure(padx=5, font=('Microsoft YaHei UI', -11))
            button.pack(side='left', padx=2, pady=(0, 3))
        self.canvas = tk.Canvas(plotcard, bg=COLORS['plot'], bd=0, highlightthickness=0, height=270)
        self.map_cache = MapDrawCache(self.canvas, MAP_DISPLAY_WATER_AREAS, SCENE_RANKS, roads=REFERENCE_ROADS,
                                     water_islands=MAP_DISPLAY_WATER_ISLANDS)
        self.map_hover, self.map_hover_job = MapHover(self.canvas), None
        self.map_pointer = None
        self.canvas.grid(row=2, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', self.map_configured)
        self.canvas.bind('<MouseWheel>', self.zoom_map)
        self.canvas.bind('<Motion>', self.map_motion)
        self.canvas.bind('<Leave>', self.map_leave)
        self.scene_filters_frame = self.frame(body, card=True)
        self.build_scene_filters(self.scene_filters_frame)
        self.root.bind('<Button-1>', self.dismiss_scene_filters, add='+')
        self.root.bind('<Escape>', self.escape_scene_filters, add='+')
        plotfoot = self.frame(plotcard, card=True)
        # Keep internal measurements, but do not display the map information bar.
        plotfoot.grid_columnconfigure(1, weight=1)
        self.field(plotfoot, 'points', '轨迹点 0', color='muted', font=('Microsoft YaHei UI', -11)).grid(row=0, column=0, sticky='w', padx=8, pady=2)
        self.field(plotfoot, 'speed', '平面速度 — 单位/秒', color='muted', font=('Microsoft YaHei UI', -11)).grid(row=0, column=1, sticky='e', padx=8)
        self.field(plotfoot, 'scale', '刻度 —', color='muted', font=('Microsoft YaHei UI', -11)).grid(row=1, column=0, sticky='w', padx=8, pady=2)
        self.scene_filter_hint = self.label(plotfoot, color='muted', font=('Microsoft YaHei UI', -10))
        self.scene_filter_hint.grid(row=1, column=1, sticky='e', padx=8)
        self.update_scene_filter_controls()
        lower_panel = tk.LabelFrame(body, text='分类显示', bg=COLORS['bg'], fg=COLORS['text'],
                                    font=('Microsoft YaHei UI', -12), bd=1, relief='solid', padx=4, pady=3)
        lower_panel.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        style = ttk.Style(self.root)
        style.configure('Monitor.TNotebook', background=COLORS['bg'], borderwidth=0)
        style.configure('Monitor.TNotebook.Tab', background=COLORS['button'], foreground=COLORS['text'],
                        padding=(12, 4), font=('Microsoft YaHei UI', -12))
        style.map('Monitor.TNotebook.Tab', background=[('selected', COLORS['card'])],
                  foreground=[('selected', COLORS['green'])])
        self.control_tabs = ttk.Notebook(lower_panel, style='Monitor.TNotebook', height=375)
        self.control_tabs.pack(fill='both', expand=True)
        route_page = self.frame(self.control_tabs, card=True, border=False)
        parameter_page = self.frame(self.control_tabs, card=True, border=False)
        map_page = self.frame(self.control_tabs, card=True, border=False)
        cooking_page = self.frame(self.control_tabs, card=True, border=False)
        for page, title in ((route_page, '采集设定'), (parameter_page, '参数设定'),
                            (map_page, '地图连接'), (cooking_page, '烹饪')):
            self.control_tabs.add(page, text=title)
        route_page.grid_columnconfigure(0, weight=1)
        def group(parent, title):
            return tk.LabelFrame(parent, text=title, bg=COLORS['card'], fg=COLORS['text'],
                                 font=('Microsoft YaHei UI', -12), padx=6, pady=4,
                                 bd=1, relief='solid', highlightthickness=0)
        operations = group(route_page, '采集路线操作')
        operations.grid(row=0, column=0, sticky='ew', padx=6, pady=(4, 0))
        for column in range(4):
            operations.grid_columnconfigure(column, weight=1, uniform='route-actions')
        self.route_buttons = {}
        for column, (action, text) in enumerate([('new', '新建路线'), ('copy', '另存路线'),
                                                ('rename', '路线改名'), ('delete', '删除路线')]):
            button = self.button(operations, text, lambda action=action: self.manage_route(action), small=True)
            button.grid(row=0, column=column, sticky='ew', padx=2, pady=2)
            self.route_buttons[action] = button
        self.route_preview_button = self.button(operations, '路线预览', self.show_route_overview, small=True)
        self.route_preview_button.grid(row=1, column=0, sticky='ew', padx=2, pady=2)
        self.save_point_button = self.button(operations, '添加点位', self.save_point, primary=True, small=True)
        self.save_point_button.grid(row=1, column=1, sticky='ew', padx=2, pady=2)
        self.point_manage_button = self.button(operations, '点位管理', self.edit_point_waits, small=True)
        self.point_manage_button.grid(row=1, column=2, sticky='ew', padx=2, pady=2)
        self.clear_points_button = self.button(operations, '清空点位', self.clear_saved_points, small=True)
        self.clear_points_button.grid(row=1, column=3, sticky='ew', padx=2, pady=2)
        pointcard = group(route_page, '采集路线显示')
        pointcard.grid(row=1, column=0, sticky='nsew', padx=6, pady=(4, 6))
        self.route_display = pointcard
        routebar = self.frame(pointcard, card=True, border=False)
        routebar.pack(fill='x')
        self.route_var = tk.StringVar(self.root, value=self.route_name)
        self.route_menu = self.dropdown(routebar, self.route_var, [self.route_name], width=16,
                                        command=self.switch_route, postcommand=self.refresh_route_menu)
        self.route_menu.pack(side='left')
        self.field(routebar, 'saved_count', '已保存 0 个点', color='green',
                   font=('Microsoft YaHei UI', -11)).pack(side='right')
        self.refresh_route_menu()
        self.saved_summary = self.field(pointcard, 'saved_summary', '点击添加点位记录当前坐标',
                                        color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.saved_summary.pack(fill='x', pady=(3, 0))
        sidebar = self.frame(map_page, card=True)
        sidebar.pack(fill='both', expand=True, padx=4, pady=4)
        self.build_sidebar(sidebar)
        coordinates = self.frame(pointcard, card=True, border=False)
        coordinates.pack(fill='x', pady=(4, 0))
        for axis, color in zip('XYZ', ('red', 'green', 'blue')):
            self.label(coordinates, axis, color=color, font=('Consolas', -12)).pack(side='left', padx=(0, 3))
            self.label(coordinates, textvariable=self.fields[axis], font=('Consolas', -12)).pack(
                side='left', padx=(0, 10))
        bottom = group(route_page, '回放控制')
        bottom.grid(row=2, column=0, sticky='ew', padx=6, pady=(0, 6))
        toolbar = self.frame(bottom)
        toolbar.pack(fill='x')
        self.label(toolbar, '起点', color='muted', font=('Microsoft YaHei UI', -12)).pack(
            side='left', padx=(0, 6))
        self.start_mode_var = tk.StringVar(self.root, value='完整回放')
        self.start_menu = self.dropdown(toolbar, self.start_mode_var, ['完整回放', '最近点接入'], width=10)
        self.start_menu.pack(side='left')
        self.play_button = self.button(toolbar, '▶ 回放', self.start_playback, primary=True, small=True)
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
        waitbar = self.frame(bottom)
        waitbar.pack(fill='x', pady=(5, 0))
        self.label(waitbar, '按 F 后等待', color='muted',
                   font=('Microsoft YaHei UI', -12)).pack(side='left')
        self.collect_wait_var = tk.StringVar(self.root, value=f'{policy.COORD_COLLECTION_DEFAULT_WAIT:g}')
        self.collect_wait_spin = tk.Spinbox(waitbar, from_=0, to=policy.COORD_COLLECTION_MAX_WAIT,
            increment=.1, textvariable=self.collect_wait_var, width=5,
            bg=COLORS['button'], fg=COLORS['text'], buttonbackground=COLORS['button'],
            insertbackground=COLORS['text'], relief='flat', font=('Microsoft YaHei UI', -12))
        self.collect_wait_spin.pack(side='left', padx=(6, 3))
        self.label(waitbar, '秒', color='muted', font=('Microsoft YaHei UI', -12)).pack(side='left')
        self.point_wait_button = self.button(waitbar, '逐点等待', self.edit_point_waits, small=True)
        self.point_wait_button.pack(side='left', padx=(8, 0))
        recoverybar = self.frame(parameter_page, card=True, border=False)
        recoverybar.pack(fill='x', pady=(5, 0))
        self.label(recoverybar, '每轮脱困次数', color='muted',
                   font=('Microsoft YaHei UI', -12)).pack(side='left', padx=(0, 8))
        self.recovery_count_var = tk.StringVar(self.root, value=str(policy.COORD_RECOVERY_DEFAULT_ATTEMPTS))
        self.recovery_menu = self.dropdown(recoverybar, self.recovery_count_var,
            [str(i) for i in range(1, policy.COORD_RECOVERY_MAX_ATTEMPTS + 1)], width=4)
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
        navigationbar = self.frame(parameter_page, card=True, border=False)
        navigationbar.pack(fill='x', pady=(5, 0))
        self.navigation_var = tk.BooleanVar(self.root, value=True)
        self.navigation_check = tk.Checkbutton(navigationbar, text='自动寻路', variable=self.navigation_var,
            bg=COLORS['bg'], fg=COLORS['text'], activebackground=COLORS['bg'],
            activeforeground=COLORS['text'], selectcolor=COLORS['button'],
            font=('Microsoft YaHei UI', -12), bd=0, highlightthickness=0)
        self.navigation_check.pack(side='left', padx=(0, 12))
        self.path_memory_var = tk.BooleanVar(self.root, value=True)
        self.path_memory_check = tk.Checkbutton(navigationbar, text='记忆走法', variable=self.path_memory_var,
            bg=COLORS['bg'], fg=COLORS['text'], activebackground=COLORS['bg'],
            activeforeground=COLORS['text'], selectcolor=COLORS['button'],
            font=('Microsoft YaHei UI', -12), bd=0, highlightthickness=0)
        self.path_memory_check.pack(side='left', padx=(0, 5))
        self.path_memory_label = self.label(navigationbar, '0 段', color='muted', font=('Microsoft YaHei UI', -11))
        self.path_memory_label.pack(side='left', padx=(0, 5))
        self.clear_path_memory_button = self.button(navigationbar, '清除记忆', self.clear_path_memory, small=True)
        self.clear_path_memory_button.pack(side='left')
        self.refresh_path_memory_info()
        self.playback_label = self.field(bottom, 'playback', self.playback_status['message'],
                                         color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1,
                                         wraplength=360, justify='left')
        self.playback_label.pack(fill='x', pady=(6, 0))
        footer = self.frame(body)
        footer.grid(row=2, column=0, sticky='ew', pady=(5, 0))
        self.label(footer, '路线自动保存至程序旁「点位」文件夹。', color='muted', font=('Microsoft YaHei UI', -10)).pack(anchor='w')
        self.hint = self.label(footer, '切换场景后请等待重新连接', color='muted', font=('Microsoft YaHei UI', -10))
        self.hint.pack(anchor='w')
        self.save_point_button.configure(state='disabled')
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='disabled')
        self.map_expand_widgets = (lower_panel, footer)
        self.expanded_status = self.label(plotcard, textvariable=self.fields['playback'],
            color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.expanded_status.grid(row=4, column=0, sticky='ew', padx=14, pady=6)
        self.expanded_status.grid_remove()
        if self.points_error:
            self.hint.configure(text='点位文件读取失败，请检查原文件', fg=COLORS['red'])

    def build_sidebar(self, sidebar):
        # Runtime scene statistics may grow to several lines. Keep the tool near
        # the coordinates and allow overflow instead of clipping the bottom rows.
        self.sidebar_canvas = tk.Canvas(sidebar, width=1, height=1, bg=COLORS['card'],
                                        bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(sidebar, orient='vertical', command=self.sidebar_canvas.yview)
        scrollbar.pack(side='right', fill='y', pady=8, padx=(0, 4))
        self.sidebar_canvas.pack(side='left', fill='both', expand=True, padx=(10, 4), pady=6)
        self.sidebar_canvas.configure(yscrollcommand=scrollbar.set)
        inner = self.frame(self.sidebar_canvas, card=True, border=False)
        sidebar_window = self.sidebar_canvas.create_window(0, 0, window=inner, anchor='nw')

        def resize_sidebar(event=None):
            width = self.sidebar_canvas.winfo_width()
            if float(self.sidebar_canvas.itemcget(sidebar_window, 'width')) != width:
                self.sidebar_canvas.itemconfigure(sidebar_window, width=width)
            region = self.sidebar_canvas.bbox('all')
            if region != getattr(self, 'sidebar_region', None):
                self.sidebar_region = region
                self.sidebar_canvas.configure(scrollregion=region)

        inner.bind('<Configure>', resize_sidebar)
        self.sidebar_canvas.bind('<Configure>', resize_sidebar)
        inner.grid_columnconfigure(1, weight=1)
        self.label(inner, '游戏连接', color='muted').grid(row=0, column=0, sticky='w', pady=(0, 6))
        entries = [('连接状态', 'freshness'), ('最近更新', 'updated')]
        for row, (name, key) in enumerate(entries, 1):
            self.label(inner, name, color='muted', font=('Microsoft YaHei UI', -11)).grid(row=row, column=0, sticky='w', pady=1, padx=(0, 16))
            self.field(inner, key, '—',
                       font=('Consolas', -12), anchor='w').grid(row=row, column=1, sticky='w', pady=1)
        self.frame(inner, card=True, height=1).grid(row=3, column=0, columnspan=2, sticky='ew', pady=6)
        self.xyz_row = self.frame(inner, card=True, border=False)
        self.xyz_row.grid(row=4, column=0, columnspan=2, sticky='ew', pady=2)
        self.xyz_row.grid_columnconfigure((0, 1, 2), weight=1, uniform='coordinates')
        for column, (axis, color) in enumerate(zip('XYZ', ['red', 'green', 'blue'])):
            cell = self.frame(self.xyz_row, card=True, border=False)
            cell.grid(row=0, column=column, sticky='ew', padx=(0, 4 if column < 2 else 0))
            self.label(cell, axis, color=color, font=('Microsoft YaHei UI', -11, 'bold')).pack(
                side='left', padx=(0, 2))
            self.field(cell, axis, '—', font=('Consolas', -12), width=10, anchor='w').pack(
                side='left', fill='x', expand=True)
        self.message = self.label(inner, '正在连接心动小镇…', color='muted', font=('Microsoft YaHei UI', -11),
                                  wraplength=250, justify='left', anchor='w')
        # Keep status updates available internally without showing the hint.
        controls = self.frame(inner, card=True, border=False)
        controls.grid(row=9, column=0, columnspan=2, sticky='ew')
        self.button(controls, '重新连接', self.reconnect, small=True).pack(fill='x')
        pressbar = self.frame(controls, card=True, border=False)
        # Hidden controls retain the default three presses used by collection.
        self.label(pressbar, '连按 F', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left')
        self.special_press_var = tk.StringVar(self.root, value='3')
        self.dropdown(pressbar, self.special_press_var, ['3', '5', '10', '20', '30'], width=4).pack(side='left', padx=6)
        self.label(pressbar, '次', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left')
        self.frame(inner, card=True, height=1).grid(row=10, column=0, columnspan=2, sticky='ew', pady=(14, 8))
        maphead = self.frame(inner, card=True, border=False)
        maphead.grid(row=11, column=0, columnspan=2, sticky='ew')
        self.label(maphead, '内存地图', color='green', font=('Microsoft YaHei UI', -12, 'bold')).pack(side='left')
        self.scene_range_var = tk.StringVar(self.root, value=f'{self.scene_radius:g} 单位')
        self.scene_range_menu = self.dropdown(maphead, self.scene_range_var,
            [f'{r} 单位' for r in SCENE_RANGE_OPTIONS], width=8, command=self.set_scene_radius)
        # Keep the configured loading radius internally without showing a selector.
        self.scene_status = self.label(inner, '进入角色后自动读取场景', color='muted',
            font=('Microsoft YaHei UI', -11), wraplength=250, justify='left', anchor='w')
        self.scene_status.grid(row=12, column=0, columnspan=2, sticky='ew', pady=5)
        switches = self.frame(inner, card=True, border=False)
        switches.grid(row=13, column=0, columnspan=2, sticky='ew')
        for index, (text, variable) in enumerate([
                ('场景物体', self.scene_objects_var), ('山体与碰撞', self.scene_colliders_var),
                ('河道与湖泊', self.scene_water_var), ('参考道路', self.scene_roads_var),
                ('围栏／护栏', self.scene_barriers_var)]):
            tk.Checkbutton(switches, text=text, variable=variable, command=self.scene_layer_changed, bg=COLORS['card'],
                fg=COLORS['text'], selectcolor=COLORS['button'], activebackground=COLORS['card'],
                activeforeground=COLORS['text'], bd=0, highlightthickness=0,
                font=('Microsoft YaHei UI', -11)).grid(row=index // 2, column=index % 2, sticky='w', padx=(0, 15))
        self.label(inner, '围栏：金色　护栏：浅蓝', color='muted',
                   font=('Microsoft YaHei UI', -10)).grid(row=14, column=0, columnspan=2, sticky='w', pady=(4, 0))
        cachebar = self.frame(inner, card=True, border=False)
        cachebar.grid(row=15, column=0, columnspan=2, sticky='ew', pady=(7, 0))
        cachebar.grid_columnconfigure((0, 1), weight=1, uniform='map-actions')
        self.button(cachebar, '刷新地图', self.refresh_scene, small=True).grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.button(cachebar, '清除缓存', self.clear_scene_cache, small=True).grid(row=0, column=1, sticky='ew', padx=(4, 0))
        self.label(inner, '淡色为远处缓存参考，近处实时更新。\n道路、水域及复杂地形仍需实际核对。',
            color='muted', font=('Microsoft YaHei UI', -10), justify='left').grid(
            row=16, column=0, columnspan=2, sticky='w', pady=(7, 0))
        toolcard = self.frame(inner, card=True, border=False)
        # Hidden as a group; Tk variables retain the existing maintenance settings.
        toolcard.grid_columnconfigure(1, weight=1)
        self.tool_title = self.label(toolcard, '手持工具耐久', font=('Microsoft YaHei UI', -11, 'bold'))
        self.tool_title.grid(
            row=0, column=0, sticky='w', padx=(0, 6))
        self.tool_value = self.field(toolcard, 'tool_durability', '未读取', color='muted',
                                     font=('Consolas', -12), anchor='e')
        self.tool_value.grid(row=0, column=1, sticky='ew')
        self.tool_progress = tk.Canvas(toolcard, width=1, height=4, bg=COLORS['button'], bd=0, highlightthickness=0)
        self.tool_progress.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 3))
        self.tool_fill = self.tool_progress.create_rectangle(0, 0, 0, 4, fill=COLORS['green'], outline='')
        self.tool_hint = self.label(toolcard, '进入角色后自动检测当前手持工具', color='muted',
            font=('Microsoft YaHei UI', -10), anchor='w', wraplength=250, justify='left')
        self.tool_hint.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.repair_auto_var = tk.BooleanVar(self.root, True)
        self.repair_auto_check = tk.Checkbutton(toolcard, text='低于20%自动维修',
            variable=self.repair_auto_var, bg=COLORS['card'], fg=COLORS['text'],
            selectcolor=COLORS['button'], activebackground=COLORS['card'], activeforeground=COLORS['text'],
            bd=0, highlightthickness=0, font=('Microsoft YaHei UI', -10), wraplength=250, justify='left')
        self.repair_auto_check.grid(row=3, column=0, columnspan=2, sticky='w', pady=(3, 0))
        self.tool_progress.bind('<Configure>', lambda event: self.render_tool_durability())
        self.label(toolcard, '角色体力', font=('Microsoft YaHei UI', -11, 'bold')).grid(
            row=4, column=0, sticky='w', pady=(6, 0))
        self.stamina_value = self.field(toolcard, 'stamina', '未读取', color='muted',
                                       font=('Consolas', -12), anchor='e')
        self.stamina_value.grid(row=4, column=1, sticky='ew', pady=(6, 0))
        self.food_auto_var = tk.BooleanVar(self.root, True)
        self.food_auto_check = tk.Checkbutton(toolcard, text='低于30随机进食至100',
            variable=self.food_auto_var, bg=COLORS['card'], fg=COLORS['text'],
            selectcolor=COLORS['button'], activebackground=COLORS['card'], activeforeground=COLORS['text'],
            bd=0, highlightthickness=0, font=('Microsoft YaHei UI', -10))
        self.food_auto_check.grid(row=5, column=0, columnspan=2, sticky='w', pady=(3, 0))
        self.label(toolcard,'无料理时',color='muted',font=('Microsoft YaHei UI',-10)).grid(
            row=6,column=0,sticky='w',pady=(3,0))
        self.food_fallback_var=tk.StringVar(self.root,value='仅水果')
        self.food_fallback_menu=self.dropdown(toolcard,self.food_fallback_var,
            ['仅水果','水果和菌类','全部可食用'],width=11)
        self.food_fallback_menu.grid(row=6,column=1,sticky='e',pady=(3,0))

        def scroll_sidebar(event):
            if inner.winfo_height() > self.sidebar_canvas.winfo_height():
                self.sidebar_canvas.yview_scroll(-int(event.delta/120), 'units')
            return 'break'

        def bind_scroll(widget):
            widget.bind('<MouseWheel>', scroll_sidebar, add='+')
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(self.sidebar_canvas)

    def render_tool_durability(self):
        tool = self.view.status.get('tools', {})
        color, percent = 'muted', 0.
        title = '手持工具耐久'
        if self.view.position is None:
            value, hint = '未读取', '等待进入角色后检测当前手持工具'
        elif tool.get('state') == 'empty' or (tool.get('state') == 'ready' and not tool.get('equipped')):
            value, hint = '未持工具', '拿出工具后自动显示名称和耐久'
        elif tool.get('state') == 'ready':
            title = f'{tool["name"]}耐久'
            percent = tool['percent']
            value = f'{tool["current"]} / {tool["maximum"]} · {percent:.1f}%'
            condition = tool['condition']
            color = {'normal': 'green', 'low': 'amber', 'broken': 'red'}[condition]
            hint = ('耐久耗尽，请修理 · 已持用' if condition == 'broken' else
                    '耐久偏低，建议修理 · 已持用' if condition == 'low' else
                    '正常 · 已持用')
            if self.view.paused:
                color, hint = 'muted', '显示已暂停 · 最后一次耐久读数'
        elif tool.get('state') == 'locked':
            value, hint = '未解锁', f'{tool.get("name", "当前工具")}尚未解锁'
        else:
            value, hint = '未读取', '暂未读取到手持工具耐久，稍后自动重试'
        if hasattr(self, 'tool_title'):
            configure_changed(self.tool_title, text=title)
        self.fields['tool_durability'].set(value)
        configure_changed(self.tool_value, fg=COLORS[color])
        configure_changed(self.tool_hint, text=hint, fg=COLORS[color])
        progress = (self.tool_progress.winfo_width(), percent, color)
        if progress != getattr(self, 'tool_progress_state', None):
            self.tool_progress_state = progress
            self.tool_progress.coords(self.tool_fill, 0, 0, progress[0]*percent/100, 4)
            self.tool_progress.itemconfigure(self.tool_fill, fill=COLORS[color])

    def render_stamina(self):
        if 'stamina' not in self.fields:
            return
        status = self.view.status.get('stamina', {})
        value, color = '未读取', 'muted'
        if self.view.position is not None and status.get('state') == 'ready':
            value = f'{status["current"]} / {status["maximum"]}'
            color = 'amber' if status['current'] < 30 else 'green'
            if self.view.paused:
                color, value = 'muted', value+' · 暂停'
        self.fields['stamina'].set(value)
        configure_changed(self.stamina_value, fg=COLORS[color])

    def build_scene_filters(self, parent):
        self.scene_objects_var, self.scene_colliders_var = tk.BooleanVar(self.root, True), tk.BooleanVar(self.root, True)
        self.scene_water_var = tk.BooleanVar(self.root, True)
        self.scene_roads_var = tk.BooleanVar(self.root, True)
        self.scene_barriers_var = tk.BooleanVar(self.root, True)
        for column in range(4):
            parent.grid_columnconfigure(column, weight=1, uniform='category')
        self.scene_all_button = self.button(parent, '全部分类', lambda: self.set_scene_categories(True), small=True)
        self.scene_all_button.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
        self.scene_none_button = self.button(parent, '清空显示', lambda: self.set_scene_categories(False), small=True)
        self.scene_none_button.grid(row=0, column=1, sticky='nsew', padx=2, pady=2)
        for button in (self.scene_all_button, self.scene_none_button):
            button.configure(pady=2, font=('Microsoft YaHei UI', -11))
        self.scene_filter_buttons = {}
        for index, (category, (label, _)) in enumerate(SCENE_CATEGORIES.items()):
            button = self.button(parent, label, lambda category=category: self.toggle_scene_category(category), small=True)
            button.configure(padx=3, pady=2, font=('Microsoft YaHei UI', -11))
            button.grid(row=index // 4 + 1, column=index % 4, sticky='nsew', padx=2, pady=2)
            self.scene_filter_buttons[category] = button
        self.global_minerals_button = self.button(parent, '岩石 / 矿石全览', self.show_mineral_overview, small=True)
        self.global_minerals_button.grid(row=5, column=0, columnspan=4, sticky='ew', padx=2, pady=2)

    def toggle_scene_filters(self):
        self.scene_filters_expanded = not self.scene_filters_expanded
        if self.scene_filters_expanded:
            self.map_leave()
            # Parent the flyout above the body so a short map cannot clip its rows.
            self.scene_filters_frame.place(in_=self.plotcard, x=6, y=74, relwidth=1., width=-12)
            self.scene_filters_frame.lift()
        else:
            self.scene_filters_frame.place_forget()
        self.update_scene_filter_controls()

    def dismiss_scene_filters(self, event):
        if not self.scene_filters_expanded:
            return
        widget = event.widget
        while widget is not None:
            if widget in (self.scene_filters_frame, self.scene_filters_toggle):
                return
            widget = widget.master
        self.toggle_scene_filters()

    def escape_scene_filters(self, event=None):
        if self.scene_filters_expanded:
            self.toggle_scene_filters()
            return 'break'

    def update_scene_filter_controls(self):
        visible = self.scene_objects_var.get()
        all_selected = visible and self.scene_categories is None
        none_selected = not visible or self.scene_categories == set()
        for button, selected in ((self.scene_all_button, all_selected), (self.scene_none_button, none_selected)):
            button.configure(bg=COLORS['green' if selected else 'button'],
                             fg='#1b2815' if selected else COLORS['text'])
        for category, button in self.scene_filter_buttons.items():
            label, color = SCENE_CATEGORIES[category]
            selected = visible and self.scene_categories is not None and category in self.scene_categories
            button.configure(text=('✓ ' if selected else '  ') + label,
                             bg='#2a382d' if selected else COLORS['button'],
                             fg=color if selected or all_selected else COLORS['muted'],
                             highlightbackground=color if selected else COLORS['line'])
        hint = ('物体已关闭 · 点击分类显示' if not visible else
                '全部分类 · 点击筛选' if all_selected else
                '已隐藏分类 · 点击显示' if none_selected else
                f'已选 {len(self.scene_categories)} 类 · 可多选')
        if hasattr(self, 'scene_filters_toggle'):
            self.scene_filters_toggle.configure(text='收起分类 ▴' if self.scene_filters_expanded else '分类筛选 ▾')
            if not self.scene_filters_expanded:
                hint = ('物体已关闭' if not visible else '全部分类' if all_selected else
                        '分类已隐藏' if none_selected else f'已选 {len(self.scene_categories)} 类')
        self.scene_filter_hint.configure(text=hint)

    def scene_layer_changed(self):
        self.update_scene_filter_controls()
        self.draw()

    def toggle_scene_category(self, category):
        self.scene_categories = ({category} if self.scene_categories is None or not self.scene_objects_var.get()
                                 else self.scene_categories ^ {category})
        self.scene_objects_var.set(True)
        self.update_scene_filter_controls()
        self.draw()

    def set_scene_categories(self, show_all):
        self.scene_categories = None if show_all else set()
        self.scene_objects_var.set(True)
        self.update_scene_filter_controls()
        self.draw()

    def refresh_scene(self):
        if self.scene_worker:
            self.scene_worker.refresh()

    def clear_scene_cache(self):
        if self.scene_worker:
            self.scene_worker.clear_history()

    def set_scene_radius(self, value):
        choices = {f'{r} 单位': r for r in SCENE_RANGE_OPTIONS}
        if value not in choices:
            return False
        self.scene_radius = choices[value]
        self.map_view, self.zoom = 'range', 1.
        self.render()
        return True

    def navigation_scene(self, pid):
        return self.scene_worker.navigation_snapshot(pid) if self.scene_worker else None

    def refresh_scene(self, pid):
        """Ask for one immediate read-only poll so a single press can be judged."""
        if self.scene_worker and not self.closed:
            self.scene_worker.refresh()

    def tick(self):
        if self.closed:
            return
        frame_started = self.clock()
        status = self.worker.take_latest()
        now = self.clock()
        if status is not None:
            self.view.accept(status, now)
        self.view.expire(now)
        if self.scene_worker:
            pid = self.view.status.get('pid') if self.view.position is not None else None
            if self.scene_worker.set_pid(pid):
                self.scene_state = {'state': 'loading' if pid else 'waiting', 'objects': [], 'colliders': []}
            scene = self.scene_worker.take_latest()
            if scene is not None and not self.view.paused:
                self.scene_state = scene
        playback = self.playback.take_latest()
        if playback is not None:
            self.playback_status = playback
            if playback.get('state') in ('reached', 'finished', 'stopped', 'error', 'partial'):
                self.refresh_path_memory_info()
        self.render()
        # One pending callback only; a slow frame never queues catch-up frames.
        elapsed = max(0., self.clock()-frame_started)
        delay = max(1, math.ceil((policy.COORD_UI_FRAME_SECONDS-elapsed)*1000))
        self.timer = self.root.after(delay, self.tick)

    def render(self):
        if self.interaction_pending():
            return
        view, status = self.view, self.view.status
        live = view.position is not None and not view.paused
        playing = self.playback.active or bool(getattr(self, 'cooking', None) and self.cooking.active)
        patrol = getattr(self, 'patrol_route_selected', False)
        configure_changed(self.route_menu, state='disabled' if playing else 'readonly')
        for action, button in self.route_buttons.items():
            enabled = not playing and (action not in ('copy', 'rename') or not self.points_error)
            enabled = enabled and (not patrol or action == 'new')
            if action == 'delete':
                enabled = enabled and self.point_store.path.exists()
            configure_changed(button, state='normal' if enabled else 'disabled')
        configure_changed(self.start_menu, state='disabled' if playing or patrol else 'readonly')
        configure_changed(self.recovery_menu, state='disabled' if playing else 'readonly')
        configure_changed(self.recovery_jump_check, state='disabled' if playing else 'normal')
        configure_changed(self.navigation_check, state='disabled' if playing or patrol else 'normal')
        if hasattr(self, 'path_memory_check'):
            configure_changed(self.path_memory_check, state='disabled' if playing or patrol or not self.navigation_var.get() else 'normal')
            configure_changed(self.clear_path_memory_button, state='disabled' if playing or patrol else 'normal')
        configure_changed(self.repair_auto_check, state='disabled' if playing else 'normal')
        if hasattr(self, 'food_auto_check'):
            configure_changed(self.food_auto_check, state='disabled' if playing else 'normal')
        if hasattr(self,'food_fallback_menu'):
            configure_changed(self.food_fallback_menu,state='disabled' if playing else 'readonly')
        configure_changed(self.collect_wait_spin, state='disabled' if playing or patrol else 'normal')
        configure_changed(self.loop_count_spin, state='disabled' if playing or patrol else 'normal')
        for key in ('route_preview_button', 'point_manage_button'):
            if hasattr(self, key):
                configure_changed(getattr(self, key), state='disabled' if playing or patrol else 'normal')
        configure_changed(self.point_wait_button, state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        configure_changed(self.clear_points_button, state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        configure_changed(self.save_point_button, state='normal' if live and not playing and not patrol else 'disabled')
        configure_changed(self.play_button, state='normal' if live and (self.saved_points or patrol) and not playing and not self.points_error else 'disabled')
        configure_changed(self.stop_button, state='normal' if playing else 'disabled')
        cooking = getattr(self, 'cooking', None)
        cooking_dialog = getattr(self, 'cooking_dialog', None)
        message = (cooking_dialog.status.get() if cooking and cooking.active and cooking_dialog
                   and not cooking_dialog.closed else '烹饪任务运行中 · F9停止' if cooking and cooking.active
                   else self.playback_status['message'])
        self.fields['playback'].set(user_feedback(message))
        playback_color = COLORS['red' if self.playback_status['state'] in ('error', 'blocked', 'repair_paused', 'equip_paused', 'food_paused') else 'green' if playing else 'muted']
        configure_changed(self.playback_label, fg=playback_color)
        configure_changed(self.expanded_status, fg=playback_color)
        self.fields['saved_count'].set('内置巡查路线' if patrol else f'已保存 {len(self.saved_points)} 个点')
        if patrol:
            summary = '1→12号房门 · 先发现先采集 · 启动时核对当天场景'
        elif self.points_error:
            summary = self.points_error
        elif self.saved_points:
            point = self.saved_points[-1]
            coords = '   '.join(f'{axis} {value:.3f}' for axis, value in zip('XYZ', point['position']))
            summary = f'最近：{point["name"]}   {coords}'
        else:
            summary = '点击按钮，将当前 XYZ 添加到当前路线'
        self.fields['saved_summary'].set(summary)
        configure_changed(self.saved_summary, fg=COLORS['red' if self.points_error else 'muted'])
        for i, axis in enumerate('XYZ'):
            self.fields[axis].set(f'{view.position[i]:.4f}' if view.position is not None else '—')
        self.fields['points'].set(f'轨迹点 {len(view.trail)}')
        self.fields['speed'].set(f'平面速度 {view.speed:.2f} 单位/秒' if view.speed is not None else '平面速度 — 单位/秒')
        if view.paused:
            color, message = 'muted', '显示已暂停，当前为最后一次读数。'
            self.fields['freshness'].set('已暂停 · 最后读数')
        elif live:
            color = 'green'
            message = view.notice or '角色坐标读取正常。切回游戏走动即可观察变化。'
            self.fields['freshness'].set('实时读取中')
        else:
            color = 'muted' if status.get('state') in ('waiting', 'connecting') else 'red'
            message = status.get('message', '没有实时坐标，请重新连接。')
            self.fields['freshness'].set('没有实时数据')
        configure_changed(self.message, text=user_feedback(message), fg=COLORS[color])
        if view.position is not None:
            stamp = status.get('sample_at')
            self.fields['updated'].set(datetime.fromisoformat(stamp).strftime('%H:%M:%S') if stamp else '—')
        else:
            self.fields['updated'].set('—')
        scene = self.scene_state
        stats = scene.get('statistics', {})
        barrier_text = (f'围栏 {stats.get("fences", 0)} · 护栏 {stats.get("handrails", 0)}'
                        if scene.get('barrier_state') == 'ready' else '围栏分类暂不可用，保留碰撞避障')
        world_text = '路边碰撞已接入' if scene.get('world_state') == 'ready' else '路边碰撞更新中'
        radius = getattr(self, 'scene_radius', policy.COORD_SCENE_RADIUS)
        cached = scene.get('display_cache', {}).get('cached_items', 0)
        terrain_text = (f' · 山体边界 {stats.get("terrain_walls", 0)} 段'
                        if scene.get('terrain_state') == 'ready' else '')
        text = (f'物体 {len(scene["objects"])} · 碰撞 {len(scene["colliders"])}\n{barrier_text} · {world_text}{terrain_text}\n显示 {radius:g} 单位 · 缓存参考 {cached}'
                if scene['state'] == 'ready' else '正在读取场景…' if scene['state'] == 'loading'
                else scene.get('message', '进入角色后自动读取场景'))
        if scene.get('global_resource_state') == 'ready':
            counts = scene.get('global_resource_counts', {})
            text += f'\n全图岩石 {counts.get("rock", 0)} · 矿石 {counts.get("ore", 0)}'
        configure_changed(self.scene_status, text=text)
        if hasattr(self, 'global_minerals_button'):
            configure_changed(self.global_minerals_button,
                              state='normal' if scene.get('global_resource_state') == 'ready' else 'disabled')
        self.render_tool_durability()
        self.render_stamina()
        self.draw_if_changed()

    @staticmethod
    def draw_scene_symbol(canvas, x, z, category):
        if category in MINERAL_STYLES:
            fill, outline = MINERAL_STYLES[category]
            tag = 'scene-'+category
            canvas.create_polygon(x-7, z+4, x-6, z-2, x-2, z-6, x+3, z-5,
                                  x+7, z, x+6, z+5, x-2, z+6,
                                  fill=fill, outline=outline, width=1, tags=tag)
            canvas.create_line(x-6, z-2, x, z, x+3, z-5, fill=outline, width=1, tags=tag)
            canvas.create_line(x, z, x+1, z+5, fill=outline, width=1, tags=tag)
            if category in ('ore', 'fluorite'):
                canvas.create_polygon(x+1, z-1, x+2, z-8, x+5, z-10, x+7, z-6,
                                      x+5, z, fill='#8fd2dd', outline='#d1f0df', width=1, tags=tag)
                canvas.create_line(x-4, z+3, x-2, z+1, x, z+2, fill='#f2d48a', width=1.5, tags=tag)
        elif category == 'roaming_oak':
            tag = 'scene-roaming_oak'
            canvas.create_line(x, z-2, x, z+7, fill='#d0a278', width=5, tags=tag)
            canvas.create_oval(x-8, z-12, x+8, z+1, fill='#245e47', outline='#e8b574', width=2, tags=tag)
            canvas.create_line(x-5, z+6, x-8, z+8, fill='#e8b574', width=2, tags=tag)
            canvas.create_line(x+3, z+6, x+7, z+8, fill='#e8b574', width=2, tags=tag)
        elif category == 'tree':
            fill, outline = PLANT_STYLES['tree']
            canvas.create_oval(x-6, z+3, x+7, z+7, fill='#101e17', outline='', tags='scene-tree')
            canvas.create_line(x, z-2, x, z+6, fill='#c99b6c', width=2, tags='scene-tree')
            canvas.create_polygon(x, z-11, x+4, z-10, x+5, z-7, x+7, z-5,
                                  x+6, z-1, x+3, z+1, x, z, x-3, z+1,
                                  x-6, z-1, x-7, z-5, x-5, z-7, x-4, z-10,
                                  smooth=True, fill=fill, outline=outline, width=1, tags='scene-tree')
            canvas.create_oval(x-3.5, z-8, x+1, z-4, fill='#5c8259', outline='', tags='scene-tree')
        elif category in FAUNA_STYLES:
            fill, outline = FAUNA_STYLES[category]
            tag = 'scene-' + category
            if category == 'butterfly':
                canvas.create_polygon(x, z, x-5, z-5, x-6, z-1, x-4, z+3,
                                      x-1, z+2, x, z, x+5, z-5, x+6, z-1,
                                      x+4, z+3, x+1, z+2, fill=fill, outline=outline,
                                      width=1, tags=tag)
                canvas.create_line(x, z-3, x, z+4, fill=outline, width=1, tags=tag)
            elif category == 'insect':
                for offset in (-2, 0, 2):
                    canvas.create_line(x-5, z+offset-1, x, z+offset, x+5, z+offset-1,
                                       fill=outline, width=1, tags=tag)
                canvas.create_oval(x-2.5, z-4, x+2.5, z+4, fill=fill, outline=outline,
                                   width=1, tags=tag)
            elif category == 'bird':
                canvas.create_line(x-6, z+2, x-3, z-3, x, z, x+3, z-3, x+6, z+2,
                                   smooth=True, fill=outline, width=2, tags=tag)
            else:
                canvas.create_oval(x-3, z-1, x+3, z+4, fill=fill, outline=outline, tags=tag)
                for dx, dz in ((-4, -3), (0, -5), (4, -3)):
                    canvas.create_oval(x+dx-1.5, z+dz-1.5, x+dx+1.5, z+dz+1.5,
                                       fill=outline, outline='', tags=tag)
        elif category in PLANT_STYLES:
            fill, outline = PLANT_STYLES[category]
            canvas.create_polygon(x, z-5, x+3, z-4, x+3, z-2, x+5, z-1,
                                  x+5, z+2, x+2, z+4, x, z+3, x-2, z+4,
                                  x-5, z+2, x-5, z-1, x-3, z-2, x-3, z-4,
                                  smooth=True, fill=fill, outline=outline,
                                  width=1, tags='scene-' + category)
            if category == 'blueberry':
                for dx, dz in ((-1.5, -1), (1, 1.5)):
                    canvas.create_oval(x+dx-1.5, z+dz-1.5, x+dx+1.5, z+dz+1.5, fill='#bed4ff', outline='',
                                       tags='scene-blueberry-fruit')
            elif category == 'raspberry':
                canvas.create_polygon(x, z-2, x+2, z, x, z+2, x-2, z, fill='#ffd0e0', outline='',
                                      tags='scene-raspberry-fruit')
        else:
            canvas.create_oval(x-1, z-1, x+1, z+1, fill='#52685b', outline='', tags='scene-object')

    def draw(self):
        if self.draw_job is not None:
            self.root.after_cancel(self.draw_job)
            self.draw_job = None
        canvas, view = self.canvas, self.view
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 2 or h < 2:
            return
        points = [(p['x'], p['z']) for p in view.trail]
        points.extend((p['position'][0], p['position'][2]) for p in self.saved_points)
        navigation_path = [p for p in self.playback_status.get('navigation_path', []) if valid_position(p)]
        points.extend((p[0], p[2]) for p in navigation_path)
        if view.position is not None:
            points.append((view.position[0], view.position[2]))
        xs, zs = zip(*points) if points else ([-5., 5.], [-5., 5.])
        cx, cz = (view.position[0], view.position[2]) if view.position is not None \
            else ((min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2)
        if self.map_view == 'range':
            radius = getattr(self, 'scene_radius', policy.COORD_SCENE_RADIUS)
            scale = min(max(1., w-48), max(1., h-64)) / (2*radius) * self.zoom
        elif self.map_view in ('route', 'minerals'):
            if self.map_view == 'minerals':
                mineral_points = self.scene_state.get('global_resources', []) or [
                    obj for obj in self.scene_state.get('objects', []) if obj.get('category') in ('rock', 'ore')]
                route_points = [(p['position'][0], p['position'][2]) for p in mineral_points]
            else:
                route_points = [(p['position'][0], p['position'][2]) for p in self.saved_points]
                route_points.extend((p[0], p[2]) for p in navigation_path)
            if view.position is not None:
                route_points.append((cx, cz))
            route_points = route_points or points or [(cx, cz)]
            dx = max(5., max(abs(x - cx) for x, _ in route_points))
            dz = max(5., max(abs(z - cz) for _, z in route_points))
            scale = min(max(1., w - 76) / (2 * dx), max(1., h - 76) / (2 * dz)) * self.zoom
        else:
            # Stable local detail: far routes, old trails and large colliders
            # must not keep shrinking the player's immediate surroundings.
            scale = MAP_PIXELS_PER_UNIT * self.zoom
        px, pz = lambda x: w / 2 + (x - cx) * scale, lambda z: h / 2 - (z - cz) * scale
        raw = 60. / scale
        power = 10 ** math.floor(math.log10(raw))
        step = next(v * power for v in (1, 2, 5, 10) if v * power >= raw)
        self.fields['scale'].set(f'刻度 {step:g} 单位/格')
        viewport = Viewport(w, h, cx, cz, scale)
        self.map_cache.begin(viewport)
        draw_grid(self.map_cache.live, viewport, step)
        scene_objects, scene_boxes = self.map_cache.geometry.select(
            self.scene_state, view.position, getattr(self, 'scene_radius', policy.COORD_SCENE_RADIUS), viewport)
        nearby_models, nearby_boxes = self.map_cache.geometry.nearby_details(view.position)
        if not self.scene_objects_var.get() or (self.scene_categories is not None and 'other' not in self.scene_categories):
            nearby_models = []
        if not self.scene_colliders_var.get():
            nearby_boxes = []
        if not self.scene_objects_var.get():
            scene_objects = []
        elif self.scene_categories is not None:
            scene_objects = [obj for obj in scene_objects
                             if (obj.get('category') if obj.get('category') in SCENE_CATEGORIES else 'other')
                             in self.scene_categories]
        if self.scene_roads_var.get() and view.position is not None:
            for key, coords, _ in self.map_cache.geometry.visible_roads(viewport):
                self.map_cache.polyline(key, coords, -3, [
                    {'fill': '#263b2e', 'width': 9, 'capstyle': 'round', 'joinstyle': 'round',
                     'tags': 'reference-road-edge'},
                    {'fill': MAP_STYLE['road'], 'width': 5, 'capstyle': 'round', 'joinstyle': 'round',
                     'tags': 'reference-road'},
                    {'fill': '#b5ac88', 'width': 1, 'dash': (3, 9), 'tags': 'reference-road-center'}])
        if self.scene_water_var.get() and view.position is not None:
            for key, coords, _ in self.map_cache.geometry.visible_water(viewport):
                self.map_cache.polygon(key, coords, -2,
                                       underlay={'fill': '', 'outline': MAP_STYLE['shore_outer'],
                                                 'width': 5, 'tags': 'reference-water-shore'},
                                       fill=MAP_STYLE['water'], outline=MAP_STYLE['shore'], width=1.5,
                                       tags='reference-water')
            for key, coords, _ in self.map_cache.geometry.visible_water_islands(viewport):
                self.map_cache.polygon(key, coords, -1, fill=MAP_STYLE['ground'],
                                       outline=MAP_STYLE['shore'], width=1., tags='reference-water-island')
        detail_footprints = {tuple(value for point in box['polygon'] for value in point) for _, box in nearby_boxes}
        for key, coords, _ in scene_boxes:
            kind = self.map_cache.geometry.barrier_kinds.get(key)
            if not kind and coords in detail_footprints:
                continue  # Avoid a solid old outline underneath unknown dashes.
            cached = key in self.map_cache.geometry.cached_keys
            if kind == 'mountain':
                if self.scene_colliders_var.get():
                    self.map_cache.polygon(key, coords, -1, fill='', outline='#bb9874', width=2,
                                           tags=('scene-mountain', 'scene-terrain'))
            elif kind:
                if self.scene_barriers_var.get():
                    color = muted_color(MAP_STYLE[kind]) if cached else MAP_STYLE[kind]
                    self.map_cache.barrier(key, coords, color, kind)
            elif self.scene_colliders_var.get():
                color = muted_color(MAP_STYLE['collision']) if cached else MAP_STYLE['collision']
                self.map_cache.polygon(key, coords, -1, fill='', outline=color, width=1,
                                       tags='scene-collider')
        # Keep this detail overlay separate from resource categories and from
        # navigation geometry. Coordinates are game units, not calibrated metres.
        for key, box in nearby_boxes:
            self.map_cache.collision_detail(key, box)
        for obj in nearby_models:
            self.map_cache.symbol(nearby_model_key(obj), (obj['position'][0], obj['position'][2]), 0,
                                  lambda painter, x, z: painter.create_polygon(
                                      x, z-4, x+4, z, x, z+4, x-4, z,
                                      fill='', outline='#8fd2db', width=1.5, tags='nearby-model'))
        for obj in scene_objects:
            category = obj.get('category')
            self.map_cache.symbol(object_key(obj), (obj['position'][0], obj['position'][2]),
                                  SCENE_RANKS.get(category, 0),
                                  lambda painter, x, z, category=category: self.draw_scene_symbol(painter, x, z, category))
        self.map_cache.finish()
        canvas = self.map_cache.live
        segment, coords = None, []
        for point in view.trail:
            if segment is not None and segment != point['segment']:
                if len(coords) >= 4:
                    canvas.create_line(*coords, fill=MAP_STYLE['trail'], width=2, tags='walk-trail')
                coords = []
            coords.extend((px(point['x']), pz(point['z'])))
            segment = point['segment']
        if len(coords) >= 4:
            canvas.create_line(*coords, fill=MAP_STYLE['trail'], width=2, tags='walk-trail')
            canvas.create_line(*coords[-64:], fill='#acd7a2', width=2, tags='walk-trail-recent')
        saved_line = [value for point in self.saved_points
                      for value in (px(point['position'][0]), pz(point['position'][2]))]
        if len(saved_line) >= 4:
            canvas.create_line(*saved_line, fill='#172934', width=4, tags='saved-route-edge')
            canvas.create_line(*saved_line, fill=MAP_STYLE['route_line'], width=1.5, dash=(5, 5), tags='saved-route')
        if navigation_path and view.position is not None:
            planned = [v for p in [view.position, *navigation_path] for v in (px(p[0]), pz(p[2]))]
            canvas.create_line(*planned, fill='#102a29', width=6, tags='navigation-path-edge')
            canvas.create_line(*planned, fill=MAP_STYLE['navigation'], width=2.5, dash=(7, 4),
                               arrow='last', arrowshape=(8, 10, 4), tags='navigation-path')
        active = self.playback_status.get('waypoint') if self.playback_status.get('state') not in (
            'idle', 'finished', 'stopped', 'error', 'partial') else None
        labels = layout_waypoints(self.saved_points, viewport, active, chrome_bounds(viewport, step))
        draw_waypoint_labels(canvas, viewport, self.saved_points, labels)
        draw_chrome(canvas, viewport, step, self.map_view == 'route', self.scene_roads_var.get(),
                    heading_known=view.heading is not None, resource_view=self.map_view == 'minerals')
        if view.position is not None:
            if isinstance(active, int) and 1 <= active <= len(self.saved_points):
                indicator = target_indicator(viewport, self.saved_points[active-1]['position'])
                if indicator:
                    draw_target_indicator(canvas, viewport, indicator, self.saved_points[active-1]['id'])
            x, y = px(view.position[0]), pz(view.position[2])
            draw_player(canvas, x, y, view.trail, view.paused, direction=view.heading)
        elif not view.trail and not self.saved_points:
            canvas.create_text(w / 2, h / 2, text='等待读取角色位置\n进入游戏后自动连接', fill=COLORS['muted'],
                               font=('Microsoft YaHei UI', -13), justify='center')
        if hasattr(self, 'map_hover'):
            targets = []
            for obj in nearby_models:
                position = obj['position']
                targets.append(HoverTarget(px(position[0]), pz(position[2]), '附近模型 / 场景对象',
                                           obj.get('model_type', obj.get('kind', '未知类型'))+' · '+self.map_distance(position)
                                           +'\n浅蓝菱形为位置锚点，橙线为实际读取的碰撞投影'))
            for _, box in nearby_boxes:
                polygon = box['polygon']
                x = sum(p[0] for p in polygon)/len(polygon)
                z = sum(p[1] for p in polygon)/len(polygon)
                height = box.get('height')
                detail = ('高度范围 '+f'{height[0]:.2f} ～ {height[1]:.2f}'
                          if height and len(height) == 2 else '高度信息不可用')
                explanation = ('浅色为候选顶面，橙线为侧面投影；未验证可通行'
                               if collision_faces(box) is not None else '虚线：缺少可靠三维表面，不补造顶面或通路')
                targets.append(HoverTarget(px(x), pz(z), '附近碰撞轮廓', detail+'\n'+explanation))
            for obj in scene_objects:
                position = obj['position']
                name = SCENE_CATEGORIES.get(obj.get('category'), SCENE_CATEGORIES['other'])[0]
                detail = ('缓存参考 · ' if obj.get('map_cached') else '') + self.map_distance(position)
                if obj.get('map_definition'):
                    detail = '全图资源位置 · '+self.map_distance(position)+'\n资源定义，不表示当前可采集状态'
                targets.append(HoverTarget(px(position[0]), pz(position[2]), name, detail))
            for label in labels:
                ids = '/'.join(str(self.saved_points[i-1]['id']) for i in label['indexes'])
                title = '路线点 '+(ids if len(ids) <= 36 else ids[:33]+'…')
                point = self.saved_points[(active if active in label['indexes'] else label['indexes'][0])-1]
                detail = f'{point.get("name", "采集点")} · '+self.map_distance(point['position'])
                if len(label['indexes']) > 1:
                    detail += f'\n共 {len(label["indexes"])} 个重叠点 · 按原顺序采集'
                x, y = label['anchor']
                targets.append(HoverTarget(x, y, title, detail, label['rect']))
            self.map_hover.update(viewport, targets)

    def map_distance(self, position):
        current = self.view.position
        if current is None:
            return '等待角色坐标'
        distance = math.hypot(position[0]-current[0], position[2]-current[2])
        return f'平面距离 {distance:.1f} 单位'

    def map_motion(self, event):
        self.map_pointer = (event.x, event.y)
        if self.map_hover_job is None:
            self.map_hover_job = self.root.after(70, self.show_map_hover)

    def show_map_hover(self):
        self.map_hover_job = None
        if self.map_pointer is not None and not self.closed:
            self.map_hover.show(*self.map_pointer)

    def map_leave(self, event=None):
        self.map_pointer = None
        if self.map_hover_job is not None:
            self.root.after_cancel(self.map_hover_job)
            self.map_hover_job = None
        self.map_hover.hide()

    def clear_trail(self):
        self.view.clear_trail()
        self.render()
        self.hint.configure(text='移动轨迹已清空 · 路线点位保留', fg=COLORS['muted'])

    def export_walk_trail(self):
        try:
            payload = trail_capture_payload(self.view.trail, '')
            payload['captured_at'] = self.wall_clock().isoformat(timespec='seconds')
            self.trail_export_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showinfo('没有可导出的实走路段', str(exc), parent=self.root)
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root, title='导出实走支路轨迹', initialdir=str(self.trail_export_dir),
            initialfile=f'支路轨迹-{self.wall_clock():%Y%m%d-%H%M%S}.json',
            defaultextension='.json', filetypes=[('道路轨迹 JSON', '*.json')])
        if not filename:
            return
        destination = Path(filename)
        payload['name'] = destination.stem
        temporary = destination.with_name(destination.name + '.tmp')
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            messagebox.showerror('导出失败', f'实走轨迹没有写入：{exc}', parent=self.root)
            return
        count = sum(segment['point_count'] for segment in payload['segments'])
        self.hint.configure(text=f'实走路段已导出：{len(payload["segments"])} 段 · {count} 个点',
                            fg=COLORS['green'])

    def clear_saved_points(self):
        try:
            self.require_saved_route()
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
            self.refresh_path_memory_info()
            self.playback_status = {'state': 'idle', 'message': f'「{route}」点位为空 · 添加点位后可回放'}
            self.render()
            self.hint.configure(text=f'已清空「{route}」的 {count} 个点位 · 已备份' if count else f'「{route}」没有点位可清空',
                                fg=COLORS['green'])
        except (OSError, ValueError) as exc:
            self.hint.configure(text='点位清空失败，原点位保留', fg=COLORS['red'])
            messagebox.showerror('清空点位失败', str(exc), parent=self.root)

    def save_point(self):
        try:
            self.require_saved_route()
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
        self.refresh_path_memory_info()
        self.render()
        self.hint.configure(text=f'已添加到「{self.route_name}」：{self.saved_points[-1]["name"]}', fg=COLORS['green'])

    def refresh_route_menu(self):
        names = self.routes.names()
        label = '萤石和橡木（巡查）'
        while any(name.casefold() == label.casefold() for name in names):
            label += '·内置'
        self.patrol_route_label = label
        self.route_menu.configure(values=[*names, label])
        if getattr(self, 'patrol_route_selected', False):
            self.route_var.set(label)

    def selected_route_label(self):
        return self.patrol_route_label if getattr(self, 'patrol_route_selected', False) else self.route_name

    def require_saved_route(self):
        if getattr(self, 'patrol_route_selected', False):
            raise ValueError('内置巡查路线由当天场景生成，请先选择普通路线再编辑。')

    def require_route_change(self):
        if self.playback.active:
            raise ValueError('请先停止回放，再切换或管理路线。')
        if self.wait_editor is not None and self.wait_editor.root.winfo_exists():
            raise ValueError('请先关闭逐点等待窗口，再切换或管理路线。')

    def activate_route(self, name):
        self.require_route_change()
        if name == getattr(self, 'patrol_route_label', None):
            self.patrol_route_selected = True
            self.route_var.set(name)
            self.saved_points, self.points_error = [], ''
            self.view.clear_trail()
            self.refresh_path_memory_info()
            self.playback_status = {'state': 'idle', 'message': '萤石和橡木 · 1→12号房门联合巡查 · 点击按顺序回放启动'}
            self.render()
            self.hint.configure(text='已选择内置巡查路线，启动时读取当天场景', fg=COLORS['green'])
            return
        store = self.routes.store(name)
        data = store.load()  # Validate before replacing the current route/view.
        self.patrol_route_selected = False
        self.point_store, self.route_name = store, name
        self.route_var.set(name)
        self.saved_points, self.points_error = data['points'], ''
        self.view.clear_trail()
        self.refresh_path_memory_info()
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
            self.route_var.set(self.selected_route_label())
            messagebox.showerror('无法切换路线', str(exc), parent=self.root)

    def manage_route(self, action):
        titles = {'new': '新建路线', 'copy': '路线另存为', 'rename': '路线改名', 'delete': '删除路线'}
        try:
            self.require_route_change()
            if action != 'new':
                self.require_saved_route()
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
            self.route_var.set(self.selected_route_label())
            messagebox.showerror(titles[action] + '失败', str(exc), parent=self.root)

    def food_fallback_mode(self):
        value=self.food_fallback_var.get() if hasattr(self,'food_fallback_var') else '仅水果'
        return {'仅水果':'fruit','水果和菌类':'fruit_mushroom','全部可食用':'all'}.get(value,'fruit')

    def start_special_gather(self, category):
        try:
            snapshot = self.view.point_snapshot(self.clock())
            scene = self.navigation_scene(self.view.status.get('pid')) or {}
            name = '萤石与橡木' if category=='both' else SCENE_CATEGORIES[category][0]
            stamp = self.wall_clock().isoformat(timespec='milliseconds')
            data = make_patrol_data(scene, snapshot['position'], category, name,
                                    int(self.special_press_var.get()), stamp)
            self.playback.start(data, self.view.status.get('pid'),
                recovery_attempts=int(self.recovery_count_var.get()), jump_on_recovery=self.recovery_jump_var.get(),
                collect_wait_seconds=0., loop_count=1, auto_navigate=True,
                auto_repair=self.repair_auto_var.get(), auto_eat=self.food_auto_var.get(),
                food_fallback=self.food_fallback_mode(),
                route_path=self.point_store.path.parent/f'动态采集-{name}.json', remember_paths=False)
            self.playback_status = {'state': 'playback_wait', 'message': f'{name}房门巡查已就绪 · 1→12号 · 切回游戏按 F6 · F9停止'}
            self.render()
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始动态采集', str(exc), parent=self.root)

    def start_playback(self):
        if getattr(self, 'patrol_route_selected', False):
            return self.start_special_gather('both')
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
                                loop_count=loop_count, auto_navigate=self.navigation_var.get(),
                                auto_repair=self.repair_auto_var.get(),
                                auto_eat=self.food_auto_var.get() if hasattr(self, 'food_auto_var') else False,
                                food_fallback=self.food_fallback_mode(),
                                route_path=self.point_store.path,
                                remember_paths=self.path_memory_var.get() if hasattr(self, 'path_memory_var') else True)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始点位回放', str(exc), parent=self.root)
            return
        self.saved_points, self.points_error = data['points'], ''
        self.playback_status = {'state': 'playback_wait',
                                'message': f'「{self.route_name}」 · {self.start_mode_var.get()} · 循环 {loop_count} 次'
                                           f' · {"自动寻路" if self.navigation_var.get() else "直接前往"}'
                                           f' · 每轮脱困 {count} 次，未通过自动重试 · 切回游戏按 F6 · F9 停止'}
        self.render()

    def collection_wait(self):
        return validate_collect_wait(float(self.collect_wait_var.get()))

    def refresh_path_memory_info(self):
        if not hasattr(self, 'path_memory_label'):
            return
        if getattr(self, 'patrol_route_selected', False):
            configure_changed(self.path_memory_label, text='0 段')
            return
        try:
            count = RoutePathMemory(self.point_store.path, self.point_store.load()).count
        except (OSError, ValueError):
            count = 0
        configure_changed(self.path_memory_label, text=f'{count} 段')

    def clear_path_memory(self):
        try:
            self.require_saved_route()
            self.require_route_change()
            if not messagebox.askyesno('清除路线记忆', f'清除「{self.route_name}」已记住的走法？采集点位会保留。', parent=self.root):
                return
            RoutePathMemory(self.point_store.path, self.point_store.load()).clear()
            self.refresh_path_memory_info()
            self.hint.configure(text=f'已清除「{self.route_name}」的走法，下次回放重新学习', fg=COLORS['green'])
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法清除路线记忆', str(exc), parent=self.root)

    def edit_point_waits(self):
        try:
            self.require_saved_route()
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
        self.refresh_path_memory_info()
        self.render()

    def stop_playback(self):
        if getattr(self, 'cooking', None) and self.cooking.active:
            self.cooking.stop()
        self.playback.stop()
        self.playback_status = {'state': 'stopping', 'message': '正在停止回放并释放按键…'}
        self.render()

    def set_zoom(self, zoom):
        self.zoom = max(.2, min(10., zoom))
        self.request_draw()

    def request_draw(self):
        if not self.closed and self.draw_job is None:
            self.draw_job = self.root.after(16, self.draw_if_changed)

    def interaction_pending(self):
        return self.clock() < getattr(self, 'interacting_until', 0.)

    def window_configured(self, event):
        if event.widget is not self.root:
            return
        geometry = (event.x, event.y, event.width, event.height)
        previous, self.window_geometry = self.window_geometry, geometry
        if previous is not None and geometry != previous:
            self.interacting_until = self.clock()+.15
            self.request_draw()

    def map_configured(self, event):
        size = event.width, event.height
        if size != self.map_size:
            self.map_size = size
            self.request_draw()

    def map_signature(self):
        view = self.view
        trail = view.trail
        point_key = lambda p: (p['x'], p['z'], p['segment'])
        position = None if view.position is None else (round(view.position[0], 3), round(view.position[2], 3))
        scene = self.scene_state
        return (self.canvas.winfo_width(), self.canvas.winfo_height(), position, view.paused, view.heading,
                len(trail), point_key(trail[0]) if trail else None, point_key(trail[-1]) if trail else None,
                tuple((p['id'], tuple(p['position'])) for p in self.saved_points),
                id(scene), id(scene.get('objects')), id(scene.get('colliders')),
                id(scene.get('global_resources')), len(scene.get('global_resources', ())),
                tuple(tuple(p) for p in self.playback_status.get('navigation_path', [])),
                self.playback_status.get('waypoint'), self.map_view, self.zoom, self.scene_radius,
                None if self.scene_categories is None else frozenset(self.scene_categories),
                tuple(v.get() for v in (self.scene_objects_var, self.scene_colliders_var, self.scene_water_var,
                                        self.scene_roads_var, self.scene_barriers_var)))

    def draw_if_changed(self):
        if self.draw_job is not None:
            self.root.after_cancel(self.draw_job)
            self.draw_job = None
        if self.closed:
            return
        if self.interaction_pending():
            delay = max(16, math.ceil((self.interacting_until-self.clock())*1000))
            self.draw_job = self.root.after(delay, self.draw_if_changed)
            return
        signature = self.map_signature()
        if signature != getattr(self, 'draw_signature', None):
            self.draw()
            self.draw_signature = signature

    def zoom_map(self, event):
        if event.delta:
            self.set_zoom(self.zoom * (1.2 if event.delta > 0 else 1 / 1.2))
        return 'break'

    def reset_map_view(self):
        self.map_view = 'nearby'
        self.set_zoom(1.)

    def show_route_overview(self):
        self.map_view = 'route' if self.saved_points else 'nearby'
        self.set_zoom(1.)

    def show_mineral_overview(self):
        self.scene_categories = {'rock', 'ore'}
        self.scene_objects_var.set(True)
        if getattr(self, 'scene_filters_expanded', False):
            self.toggle_scene_filters()
        self.update_scene_filter_controls()
        self.map_view = 'minerals'
        self.set_zoom(1.)

    def toggle_map_size(self):
        self.map_expanded = not self.map_expanded
        for widget in self.map_expand_widgets:
            widget.grid_remove() if self.map_expanded else widget.grid()
        self.map_expand_button.configure(text='还原布局' if self.map_expanded else '大地图')
        self.expanded_status.grid() if self.map_expanded else self.expanded_status.grid_remove()
        self.root.update_idletasks()
        self.draw()

    def reconnect(self):
        if self.playback.active or bool(getattr(self, 'cooking', None) and self.cooking.active):
            self.stop_playback()
        if self.scene_worker:
            self.scene_worker.set_pid(None)
        self.scene_state = {'state': 'loading', 'objects': [], 'colliders': []}
        self.worker.set_paused(False)
        self.worker.take_latest()
        self.view.set_paused(False)
        self.view.invalidate('正在重新连接游戏…')
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

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.root.after_cancel(self.timer)
        if getattr(self, 'map_hover_job', None) is not None:
            self.root.after_cancel(self.map_hover_job)
        if self.draw_job is not None:
            self.root.after_cancel(self.draw_job)
        if getattr(self, 'cooking', None):
            self.cooking.close()
        self.playback.close()
        if self.scene_worker:
            self.scene_worker.close()
        self.worker.close()
        self.root.destroy()


def main(playback_runner=None, cooking_runner=None):
    parser = argparse.ArgumentParser(description='心动小镇原生坐标监视窗口')
    parser.add_argument('--check-output', type=Path)
    parser.add_argument('--check-scene-output', type=Path)
    args = parser.parse_args()
    resources = Path(sys._MEIPASS) / 'monitor' if getattr(sys, 'frozen', False) \
        else Path(__file__).resolve().parents[2] / 'webui' / 'coordinate-monitor'
    monitor = CoordinateMonitor(resources / 'profile.json')
    if args.check_scene_output:
        scene = None
        try:
            status = monitor.status()
            if status['state'] != 'connected':
                raise ValueError('请进入角色后读取场景地图')
            scene = MemorySceneMap(status['pid'], resources / 'profile.json')
            args.check_scene_output.write_text(json.dumps(scene.snapshot(), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        finally:
            if scene:
                scene.close()
            monitor.close()
        return
    if args.check_output:
        try:
            from utils.vision.repair_box import RepairBoxMenu
            from utils.vision.axe_pick import AxePickMenu
            from utils.vision.food_menu import FruitMenu, FRUITS
            from utils.core.tool_repair import REPAIR_EFFECT_SECONDS, REPAIR_THRESHOLD_PERCENT
            repair = RepairBoxMenu(resources/'repair_box_menu.png')
            axe = AxePickMenu(resources/'axe_pick_icon.png')
            fruit = FruitMenu(resources/'food_bag_marker.png')
            status = monitor.status()
            status['repair_support'] = {'state': 'ready', 'normal_box_shape': list(repair.template.shape[:2]),
                                        'effect_seconds': REPAIR_EFFECT_SECONDS,
                                        'trigger_below_percent': REPAIR_THRESHOLD_PERCENT}
            status['equip_support'] = {'state': 'ready', 'tool': '斧镐', 'icon_shape': list(axe.template.shape[:2])}
            status['food_support'] = {'state': 'ready', 'threshold_units': 30,
                                      'fruits': list(FRUITS.values()), 'button_shape': list(fruit.eat.shape[:2]),
                                      'close_shape': list(fruit.close_icon.shape[:2])}
            args.check_output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            monitor.close()
        return
    root = tk.Tk()
    CoordinateWindow(root, monitor, resources, playback_runner=playback_runner, scene_factory=MemorySceneMap, cooking_runner=cooking_runner)
    root.mainloop()
