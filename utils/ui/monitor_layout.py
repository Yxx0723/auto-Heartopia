"""Native widget layout only; session ownership stays in CoordinateWindow."""
import tkinter as tk
from tkinter import ttk
from utils.core import thresholds as policy
from utils.ui.map_draw_cache import MapDrawCache
from utils.ui.map_labels import MapHover
from utils.core.map_water_reference import MAP_WATER_AREAS, MAP_WATER_ISLANDS
from utils.core.map_reference_roads import REFERENCE_ROADS
from utils.ui.monitor_style import COLORS, SCENE_CATEGORIES, SCENE_RANGE_OPTIONS, SCENE_RANKS
from utils.ui.monitor_widgets import MonitorWidgets


class MonitorLayout(MonitorWidgets):
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
        workspace.grid_columnconfigure(1, minsize=300)
        workspace.grid_rowconfigure(0, weight=1)
        plotcard = self.frame(workspace, card=True)
        self.plotcard = plotcard
        plotcard.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
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
                                      ('重置缩放', self.reset_map_view), ('路线全览', self.show_route_overview),
                                      ('清空轨迹', self.clear_trail),
                                      ('导出实走路段', self.export_walk_trail)]):
            button = self.button(plothead, text, action, small=True)
            button.configure(padx=4, font=('Microsoft YaHei UI', -11))
            button.pack(side='right', padx=1, pady=4)
        self.canvas = tk.Canvas(plotcard, bg=COLORS['plot'], bd=0, highlightthickness=0, height=270)
        self.map_cache = MapDrawCache(self.canvas, MAP_WATER_AREAS, SCENE_RANKS, roads=REFERENCE_ROADS,
                                     water_islands=MAP_WATER_ISLANDS)
        self.map_hover, self.map_hover_job = MapHover(self.canvas), None
        self.map_pointer = None
        self.canvas.grid(row=2, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', self.map_configured)
        self.canvas.bind('<MouseWheel>', self.zoom_map)
        self.canvas.bind('<Motion>', self.map_motion)
        self.canvas.bind('<Leave>', self.map_leave)
        self.scene_filters_frame = self.frame(self.canvas, card=True)
        self.build_scene_filters(self.scene_filters_frame)
        self.root.bind('<Button-1>', self.dismiss_scene_filters, add='+')
        self.root.bind('<Escape>', self.escape_scene_filters, add='+')
        plotfoot = self.frame(plotcard, card=True)
        plotfoot.grid(row=3, column=0, sticky='ew')
        self.field(plotfoot, 'points', '轨迹点 0', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left', padx=8, pady=5)
        self.field(plotfoot, 'speed', '平面速度 — 单位/秒', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='left', padx=8)
        self.field(plotfoot, 'scale', '刻度 —', color='muted', font=('Microsoft YaHei UI', -11)).pack(side='right', padx=8)
        self.scene_filter_hint = self.label(plotfoot, color='muted', font=('Microsoft YaHei UI', -10))
        self.scene_filter_hint.pack(side='right', padx=4)
        self.update_scene_filter_controls()
        pointcard = self.frame(workspace, card=True)
        pointcard.grid(row=1, column=0, sticky='ew', padx=(0, 10), pady=(6, 0))
        routebar = self.frame(pointcard, card=True, border=False)
        routebar.pack(fill='x', padx=8, pady=(6, 0))
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
        pointhead.pack(side='left', padx=8, pady=6)
        self.save_point_button = self.button(pointhead, '＋ 添加当前点位', self.save_point, primary=True, small=True)
        self.save_point_button.pack(side='left')
        self.delete_point_button = self.button(pointhead, '删除点位', self.edit_point_waits, small=True)
        self.delete_point_button.pack(side='left', padx=(5, 0))
        self.optimize_route_button = self.button(pointhead, '优化另存', self.optimize_current_route, small=True)
        self.optimize_route_button.pack(side='left', padx=(5, 0))
        pointinfo = self.frame(pointrow, card=True, border=False)
        pointinfo.pack(side='left', fill='x', expand=True, padx=(0, 8), pady=5)
        self.field(pointinfo, 'saved_count', '已保存 0 个点', color='green',
                   font=('Microsoft YaHei UI', -12)).pack(anchor='w')
        self.saved_summary = self.field(pointinfo, 'saved_summary', '点击按钮，将当前 XYZ 添加到当前路线',
                                        color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.saved_summary.pack(fill='x', pady=(3, 0))
        sidebar = self.frame(workspace, card=True)
        sidebar.grid(row=0, column=1, rowspan=2, sticky='nsew')
        self.build_sidebar(sidebar)
        bottom = self.frame(body)
        bottom.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        toolbar = self.frame(bottom)
        toolbar.pack(fill='x')
        self.label(toolbar, '起点方式', color='muted', font=('Microsoft YaHei UI', -12)).pack(
            side='left', padx=(0, 6))
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
        self.recovery_jump_check = tk.Checkbutton(recoverybar, text='绕行失败后尝试大跳',
            variable=self.recovery_jump_var, bg=COLORS['bg'], fg=COLORS['text'],
            activebackground=COLORS['bg'], activeforeground=COLORS['text'],
            selectcolor=COLORS['button'], font=('Microsoft YaHei UI', -12), bd=0,
            highlightthickness=0)
        self.recovery_jump_check.pack(side='left', padx=(12, 16))
        self.navigation_var = tk.BooleanVar(self.root, value=True)
        self.navigation_check = tk.Checkbutton(recoverybar, text='自动寻路', variable=self.navigation_var,
            bg=COLORS['bg'], fg=COLORS['text'], activebackground=COLORS['bg'],
            activeforeground=COLORS['text'], selectcolor=COLORS['button'],
            font=('Microsoft YaHei UI', -12), bd=0, highlightthickness=0)
        self.navigation_check.pack(side='left', padx=(0, 12))
        self.path_memory_var = tk.BooleanVar(self.root, value=False)
        self.path_memory_check = tk.Checkbutton(recoverybar, text='记忆走法', variable=self.path_memory_var,
            bg=COLORS['bg'], fg=COLORS['text'], activebackground=COLORS['bg'],
            activeforeground=COLORS['text'], selectcolor=COLORS['button'],
            font=('Microsoft YaHei UI', -12), bd=0, highlightthickness=0)
        self.path_memory_label = self.label(recoverybar, '0 段', color='muted', font=('Microsoft YaHei UI', -11))
        self.clear_path_memory_button = self.button(recoverybar, '清除记忆', self.clear_path_memory, small=True)
        self.refresh_path_memory_info()
        self.playback_label = self.field(bottom, 'playback', self.playback_status['message'],
                                         color='muted', font=('Microsoft YaHei UI', -11), anchor='w', width=1)
        self.playback_label.pack(fill='x', pady=(6, 0))
        footer = self.frame(body)
        footer.grid(row=2, column=0, sticky='ew', pady=(5, 0))
        self.label(footer, '点位自动保存到当前路线；各条路线保存在程序旁的「点位」文件夹。', color='muted', font=('Microsoft YaHei UI', -10)).pack(side='left')
        self.hint = self.label(footer, '切换场景后请等待重新连接', color='muted', font=('Microsoft YaHei UI', -10))
        self.hint.pack(side='right')
        self.save_point_button.configure(state='disabled')
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='disabled')
        self.map_expand_widgets = (sidebar, pointcard, bottom, footer)
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
        self.message.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(4, 4))
        controls = self.frame(inner, card=True, border=False)
        controls.grid(row=9, column=0, columnspan=2, sticky='ew')
        self.button(controls, '重新连接', self.reconnect, small=True).pack(fill='x')
        specialbar = self.frame(controls, card=True, border=False)
        specialbar.pack(fill='x', pady=(5, 0))
        self.button(specialbar, '采集萤石', lambda: self.start_special_gather('fluorite'), small=True).pack(
            side='left', fill='x', expand=True, padx=(0, 4))
        self.button(specialbar, '采集橡木', lambda: self.start_special_gather('roaming_oak'), small=True).pack(
            side='left', fill='x', expand=True)
        pressbar = self.frame(controls, card=True, border=False)
        pressbar.pack(fill='x', pady=(3, 0))
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
        self.scene_status = self.label(inner, '进入角色后自动读取场景', color='muted',
            font=('Microsoft YaHei UI', -11), wraplength=250, justify='left', anchor='w')
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
        toolcard = self.frame(inner, card=True, border=False)
        toolcard.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(9, 4))
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
        self.food_auto_check = tk.Checkbutton(toolcard, text='体力低于30自动吃水果',
            variable=self.food_auto_var, bg=COLORS['card'], fg=COLORS['text'],
            selectcolor=COLORS['button'], activebackground=COLORS['card'], activeforeground=COLORS['text'],
            bd=0, highlightthickness=0, font=('Microsoft YaHei UI', -10))
        self.food_auto_check.grid(row=5, column=0, columnspan=2, sticky='w', pady=(3, 0))

        def scroll_sidebar(event):
            if inner.winfo_height() > self.sidebar_canvas.winfo_height():
                self.sidebar_canvas.yview_scroll(-int(event.delta/120), 'units')
            return 'break'

        def bind_scroll(widget):
            widget.bind('<MouseWheel>', scroll_sidebar, add='+')
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(self.sidebar_canvas)

    def build_scene_filters(self, parent):
        self.scene_objects_var, self.scene_colliders_var = tk.BooleanVar(self.root, True), tk.BooleanVar(self.root, True)
        self.scene_water_var = tk.BooleanVar(self.root, True)
        self.scene_roads_var = tk.BooleanVar(self.root, True)
        self.scene_barriers_var = tk.BooleanVar(self.root, True)
        for column in range(7):
            parent.grid_columnconfigure(column, weight=1, uniform='category')
        self.scene_all_button = self.button(parent, '全部分类', lambda: self.set_scene_categories(True), small=True)
        self.scene_all_button.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
        self.scene_none_button = self.button(parent, '清空显示', lambda: self.set_scene_categories(False), small=True)
        self.scene_none_button.grid(row=1, column=0, sticky='nsew', padx=2, pady=2)
        for button in (self.scene_all_button, self.scene_none_button):
            button.configure(pady=2, font=('Microsoft YaHei UI', -11))
        self.scene_filter_buttons = {}
        for index, (category, (label, _)) in enumerate(SCENE_CATEGORIES.items()):
            button = self.button(parent, label, lambda category=category: self.toggle_scene_category(category), small=True)
            button.configure(padx=3, pady=2, font=('Microsoft YaHei UI', -11))
            button.grid(row=index // 6, column=index % 6 + 1, sticky='nsew', padx=2, pady=2)
            self.scene_filter_buttons[category] = button
        self.global_minerals_button = self.button(parent, '全图资源总览', self.show_mineral_overview, small=True)
        self.global_minerals_button.grid(row=(len(SCENE_CATEGORIES)+5)//6, column=0, columnspan=7, sticky='ew', padx=2, pady=2)
