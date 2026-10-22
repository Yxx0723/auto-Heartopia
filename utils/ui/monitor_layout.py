"""Native widget layout only; session ownership stays in CoordinateWindow."""
import tkinter as tk
from tkinter import ttk
from utils.core import thresholds as policy
from utils.ui.map_draw_cache import MapDrawCache
from utils.ui.map_labels import MapHover
from utils.core.water_display_reference import DISPLAY_WATER_AREAS as MAP_WATER_AREAS, DISPLAY_WATER_ISLANDS as MAP_WATER_ISLANDS, DISPLAY_WATER_DETAIL
from utils.core.map_reference_roads import REFERENCE_ROADS
from utils.ui.monitor_style import COLORS, SCENE_CATEGORIES, SCENE_RANGE_OPTIONS, SCENE_RANKS
from utils.ui.monitor_widgets import (DisplayValue, HudCard, HudReadout, MonitorWidgets, SegmentedBar,
                                     Stepper, mono_font, ui_font)
from utils.ui.ui_tokens import CONTROL, SPACE, TYPE, px

# 起点方式：默认从距人物最近的点接入，直接往末点走（不再默认走完整回放）。
START_MODE_OPTIONS = ('完整回放', '最近点接入')
START_MODE_DEFAULT = '最近点接入'
SIDEBAR_WIDTH = 322
MAP_TOOL_GROUPS = (
    (('−', 'zoom_out'), ('＋', 'zoom_in'), ('重置缩放', 'reset_view')),
    (('路线全览', 'route_overview'),),
    (('清空轨迹', 'clear_trail'), ('导出实走路段', 'export_trail')),
)
MAP_TOOL_ACTIONS = ('zoom_out', 'zoom_in', 'reset_view', 'route_overview', 'clear_trail', 'export_trail')


class MonitorLayout(MonitorWidgets):
    def build(self):
        self.configure_dropdowns()
        body = self.frame(self.root)
        body.pack(fill='both', expand=True, padx=px(SPACE['sp4']),
                  pady=(px(SPACE['sp2']), px(SPACE['sp3'])))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        workspace = self.frame(body)
        self.workspace = workspace
        workspace.grid(row=0, column=0, sticky='nsew')
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_columnconfigure(1, minsize=SIDEBAR_WIDTH)
        workspace.grid_rowconfigure(0, weight=1)
        plotcard = self.frame(workspace, card=True)
        self.plotcard = plotcard
        plotcard.grid(row=0, column=0, sticky='nsew', padx=(0, px(SPACE['sp3'])))
        plotcard.grid_columnconfigure(0, weight=1)
        plotcard.grid_rowconfigure(2, weight=1)
        self.build_map_toolbar(plotcard)
        self.build_map(plotcard)
        self.build_map_legend(plotcard)
        pointcard = self.build_pointcard(workspace)
        sidebar = self.frame(workspace, card=True)
        sidebar.grid(row=0, column=1, rowspan=2, sticky='nsew')
        self.build_sidebar(sidebar)
        bottom = self.frame(body)
        bottom.grid(row=1, column=0, sticky='ew', pady=(px(SPACE['sp3']), 0))
        self.build_playback_toolbar(bottom)
        self.build_status_line(bottom)
        footer = self.frame(body)
        footer.grid(row=2, column=0, sticky='ew', pady=(px(SPACE['sp2']), 0))
        self.label(footer, '点位自动保存到当前路线；各条路线保存在程序旁的「点位」文件夹。',
                   color='faint', font=ui_font(TYPE['hint'])).pack(side='left')
        self.hint = self.label(footer, '切换场景后请等待重新连接', color='faint', font=ui_font(TYPE['hint']))
        self.hint.pack(side='right')
        self.save_point_button.configure(state='disabled')
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='disabled')
        self.map_expand_widgets = (sidebar, pointcard, bottom, footer)
        self.expanded_status = self.label(plotcard, textvariable=self.fields['playback'],
                                          color='faint', font=ui_font(TYPE['label']), anchor='w', width=1)
        self.expanded_status.grid(row=4, column=0, sticky='ew', padx=px(SPACE['sp3']), pady=px(SPACE['sp2']))
        self.expanded_status.grid_remove()
        if self.points_error:
            self.hint.configure(text='点位文件读取失败，请检查原文件', fg=COLORS['red'])

    # ---------- 地图 ----------

    def build_map_toolbar(self, plotcard):
        """Map actions in three groups: zoom / view / trail."""
        plothead = tk.Frame(plotcard, bg=COLORS['card'])
        plothead.grid(row=0, column=0, sticky='ew', padx=px(SPACE['sp1']), pady=(px(SPACE['sp2']), 0))
        title = tk.Frame(plothead, bg=COLORS['card'])
        title.pack(side='left', pady=px(SPACE['sp1']))
        self.label(title, '内存地图', color='green', font=ui_font(TYPE['section'], True)).pack(side='left')
        self.scene_filters_toggle = self.button(title, '分类筛选 ▾', self.toggle_scene_filters, small=True)
        self.scene_filters_toggle.pack(side='left', padx=(px(SPACE['sp3']), 0))
        self.separator(title).pack(side='left', fill='y', padx=px(SPACE['sp2']), pady=px(SPACE['sp1']))
        self.map_expand_button = self.button(title, '大地图', self.toggle_map_size, small=True)
        self.map_expand_button.pack(side='left')
        tools = tk.Frame(plothead, bg=COLORS['card'])
        tools.pack(side='right', pady=px(SPACE['sp1']))
        actions = {'zoom_out': lambda: self.set_zoom(self.zoom / 1.5),
                   'zoom_in': lambda: self.set_zoom(self.zoom * 1.5),
                   'reset_view': self.reset_map_view,
                   'route_overview': self.show_route_overview,
                   'clear_trail': self.clear_trail,
                   'export_trail': self.export_walk_trail}
        self.map_tool_buttons = {}
        for index, group in enumerate(MAP_TOOL_GROUPS):
            if index:
                self.separator(tools).pack(side='left', fill='y', padx=px(SPACE['sp2']), pady=px(SPACE['sp1']))
            for text, key in group:
                if key in ('zoom_out', 'zoom_in'):
                    holder = self.icon_button(tools, text, actions[key],
                                              bg=COLORS['card_hi'], fg=COLORS['text'])
                    holder.pack(side='left', padx=(0, px(SPACE['sp1'])))
                    self.map_tool_buttons[key] = holder.button
                else:
                    button = self.button(tools, text, actions[key], small=True)
                    button.pack(side='left', padx=(0, px(SPACE['sp1'])))
                    self.map_tool_buttons[key] = button
        return plothead

    def build_map(self, plotcard):
        """Map sits inside a cut-corner panel so it reads as HUD content, not chrome."""
        mapframe = HudCard(plotcard, highlightbackground=COLORS['line_strong'])
        self.mapframe = mapframe
        mapframe.grid(row=2, column=0, sticky='nsew',
                      padx=px(CONTROL['canvas_inset']), pady=(px(SPACE['sp1']), px(CONTROL['canvas_inset'])))
        mapframe.grid_rowconfigure(0, weight=1)
        mapframe.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(mapframe, bg=COLORS['plot'], bd=0, highlightthickness=0,
                                height=px(270))
        self.map_cache = MapDrawCache(self.canvas, MAP_WATER_AREAS, SCENE_RANKS, roads=REFERENCE_ROADS,
                                      water_islands=MAP_WATER_ISLANDS, water_detail=DISPLAY_WATER_DETAIL)
        self.map_hover, self.map_hover_job = MapHover(self.canvas), None
        self.map_pointer = None
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', self.map_configured)
        self.canvas.bind('<MouseWheel>', self.zoom_map)
        self.canvas.bind('<Motion>', self.map_motion)
        self.canvas.bind('<Leave>', self.map_leave)
        self.scene_filters_frame = self.frame(self.canvas, card=True)
        self.build_scene_filters(self.scene_filters_frame)
        self.root.bind('<Button-1>', self.dismiss_scene_filters, add='+')
        self.root.bind('<Escape>', self.escape_scene_filters, add='+')
        return mapframe

    def build_map_legend(self, plotcard):
        plotfoot = tk.Frame(plotcard, bg=COLORS['card'])
        self.plotfoot = plotfoot
        plotfoot.grid(row=3, column=0, sticky='ew', padx=px(SPACE['sp1']), pady=(0, px(SPACE['sp2'])))
        self.legend_item(plotfoot, 'points', '轨迹点 0', 'green')
        self.label(plotfoot, '·', color='line_strong', font=ui_font(TYPE['label'])).pack(
            side='left', padx=px(SPACE['sp2']))
        self.legend_item(plotfoot, 'speed', '平面速度 — 单位/秒', 'green')
        self.field(plotfoot, 'scale', '刻度 —', color='faint', font=ui_font(TYPE['label'])).pack(
            side='right', padx=(0, px(SPACE['sp2'])))
        self.label(plotfoot, '·', color='line_strong', font=ui_font(TYPE['label'])).pack(
            side='right', padx=px(SPACE['sp2']))
        self.scene_filter_hint = self.button(plotfoot, '', self.toggle_scene_filters, small=True)
        self.scene_filter_hint.configure(font=ui_font(TYPE['micro']), padx=px(SPACE['sp2']), pady=px(1),
                                         bg=COLORS['card'], fg=COLORS['faint'],
                                         activebackground=COLORS['button_hover'],
                                         activeforeground=COLORS['text'],
                                         highlightbackground=COLORS['card'], highlightthickness=0)
        self.scene_filter_hint.pack(side='right')
        self.update_scene_filter_controls()

    def legend_item(self, parent, key, text, color):
        dot = tk.Canvas(parent, width=px(9), height=px(9), bg=parent.cget('bg'),
                        highlightthickness=0, bd=0)
        dot.create_polygon(px(4.5), 0, px(9), px(4.5), px(4.5), px(9), 0, px(4.5),
                           fill='', outline=COLORS[color])
        dot.pack(side='left', padx=(0, px(SPACE['sp1'])))
        self.field(parent, key, text, color='faint', font=ui_font(TYPE['label'])).pack(side='left')

    # ---------- 路线与点位 ----------

    def build_pointcard(self, workspace):
        pointcard = self.frame(workspace, card=True)
        pointcard.grid(row=1, column=0, sticky='ew', padx=(0, px(SPACE['sp3'])), pady=(px(SPACE['sp2']), 0))
        routebar = tk.Frame(pointcard, bg=COLORS['card'])
        routebar.pack(fill='x', padx=px(SPACE['sp3']), pady=(px(SPACE['sp2']), 0))
        self.label(routebar, '当前路线', color='faint', font=ui_font(TYPE['label'])).pack(
            side='left', padx=(0, px(SPACE['sp2'])))
        self.route_var = tk.StringVar(self.root, value=self.route_name)
        self.route_menu = self.dropdown(routebar, self.route_var, [self.route_name], width=14,
                                        command=self.switch_route, postcommand=self.refresh_route_menu)
        self.route_menu.pack(side='left')
        self.clear_points_button = self.button(routebar, '清空点位', self.clear_saved_points, small=True)
        self.clear_points_button.pack(side='right')
        self.route_buttons = {}
        for action, text in [('delete', '删除'), ('rename', '改名'), ('copy', '另存为'), ('new', '新建')]:
            button = self.button(routebar, text, lambda action=action: self.manage_route(action), small=True)
            button.pack(side='right', padx=(0, px(SPACE['sp1'])))
            self.route_buttons[action] = button
        self.refresh_route_menu()
        pointrow = tk.Frame(pointcard, bg=COLORS['card'])
        pointrow.pack(fill='x', padx=px(SPACE['sp3']), pady=(px(SPACE['sp2']), px(SPACE['sp2'])))
        self.save_point_button = self.button(pointrow, '＋ 添加当前点位', self.save_point, primary=True, small=True)
        self.save_point_button.pack(side='left')
        self.delete_point_button = self.button(pointrow, '删除点位', self.edit_point_waits, small=True)
        self.delete_point_button.pack(side='left', padx=(px(SPACE['sp1']), 0))
        self.optimize_route_button = self.button(pointrow, '优化另存', self.optimize_current_route, small=True)
        self.optimize_route_button.pack(side='left', padx=(px(SPACE['sp1']), 0))
        pointinfo = tk.Frame(pointrow, bg=COLORS['card'])
        pointinfo.pack(side='left', fill='x', expand=True, padx=(px(SPACE['sp3']), 0))
        self.field(pointinfo, 'saved_count', '已保存 0 个点', color='green',
                   font=ui_font(TYPE['body'], True)).pack(anchor='w')
        self.saved_summary = self.field(pointinfo, 'saved_summary', '还没有点位 · 点「＋ 添加当前点位」开始',
                                        color='faint', font=ui_font(TYPE['label']), anchor='w', width=1)
        self.saved_summary.pack(fill='x', pady=(px(2), 0))
        return pointcard

    # ---------- 底部控制 ----------

    def build_playback_toolbar(self, bottom):
        toolbar = self.frame(bottom)
        toolbar.pack(fill='x')
        playhat = tk.Frame(toolbar, bg=COLORS['bg'])
        playhat.pack(side='left')
        self.label(playhat, '起点方式', color='faint', font=ui_font(TYPE['body'])).pack(
            side='left', padx=(0, px(SPACE['sp2'])))
        self.start_mode_var = tk.StringVar(self.root, value=START_MODE_DEFAULT)
        self.start_menu = self.dropdown(playhat, self.start_mode_var, list(START_MODE_OPTIONS), width=10)
        self.start_menu.pack(side='left')
        self.play_button = self.button(playhat, '▶ 按顺序回放', self.start_playback, primary=True, small=True)
        self.play_button.pack(side='left', padx=(px(SPACE['sp3']), px(SPACE['sp1'])))
        self.stop_button = self.button(playhat, '■ 停止', self.stop_playback, small=True)
        self.stop_button.pack(side='left')
        self.separator(toolbar).pack(side='left', fill='y', padx=px(SPACE['sp3']), pady=px(SPACE['sp1']))
        self.label(toolbar, '循环', color='faint', font=ui_font(TYPE['body'])).pack(side='left')
        self.loop_count_var = tk.StringVar(self.root, value=str(policy.COORD_PLAYBACK_DEFAULT_LOOPS))
        self.loop_count_spin = Stepper(toolbar, self.loop_count_var, 1, policy.COORD_PLAYBACK_MAX_LOOPS,
                                       increment=1, width=54, integer=True)
        self.loop_count_spin.pack(side='left', padx=(px(SPACE['sp2']), px(SPACE['sp1'])))
        self.label(toolbar, '次', color='faint', font=ui_font(TYPE['body'])).pack(side='left')
        self.separator(toolbar).pack(side='left', fill='y', padx=px(SPACE['sp3']), pady=px(SPACE['sp1']))
        self.label(toolbar, '按 F 后等待', color='faint', font=ui_font(TYPE['body'])).pack(side='left')
        self.collect_wait_var = tk.StringVar(self.root, value=f'{policy.COORD_COLLECTION_DEFAULT_WAIT:.1f}')
        self.collect_wait_spin = Stepper(toolbar, self.collect_wait_var, 0, policy.COORD_COLLECTION_MAX_WAIT,
                                         increment=0.1, width=62, integer=False)
        self.collect_wait_spin.pack(side='left', padx=(px(SPACE['sp2']), px(SPACE['sp1'])))
        self.label(toolbar, '秒', color='faint', font=ui_font(TYPE['body'])).pack(side='left')
        self.point_wait_button = self.button(toolbar, '逐点等待', self.edit_point_waits, small=True)
        self.point_wait_button.pack(side='left', padx=(px(SPACE['sp3']), 0))
        recoverybar = self.frame(bottom)
        recoverybar.pack(fill='x', pady=(px(SPACE['sp2']), 0))
        self.recoverybar = recoverybar
        self.label(recoverybar, '每轮脱困次数', color='faint', font=ui_font(TYPE['body'])).pack(
            side='left', padx=(0, px(SPACE['sp2'])))
        self.recovery_count_var = tk.StringVar(self.root, value=str(policy.COORD_RECOVERY_DEFAULT_ATTEMPTS))
        self.recovery_menu = self.dropdown(recoverybar, self.recovery_count_var,
                                           [str(i) for i in range(1, policy.COORD_RECOVERY_MAX_ATTEMPTS + 1)],
                                           width=4)
        self.recovery_menu.pack(side='left')
        self.label(recoverybar, '次', color='faint', font=ui_font(TYPE['body'])).pack(
            side='left', padx=(px(SPACE['sp1']), 0))
        self.separator(recoverybar).pack(side='left', fill='y', padx=px(SPACE['sp3']), pady=px(SPACE['sp1']))
        self.recovery_jump_var = tk.BooleanVar(self.root, value=True)
        self.recovery_jump_check = self.check(recoverybar, '遇到障碍优先大跳', self.recovery_jump_var)
        self.recovery_jump_check.pack(side='left', padx=(0, px(SPACE['sp4'])))
        self.navigation_var = tk.BooleanVar(self.root, value=True)
        self.navigation_check = self.check(recoverybar, '自动寻路', self.navigation_var)
        self.navigation_check.pack(side='left')
        self.path_memory_var = tk.BooleanVar(self.root, value=False)
        self.path_memory_check = self.check(recoverybar, '记忆走法', self.path_memory_var)
        self.path_memory_label = self.label(recoverybar, '0 段', color='faint', font=ui_font(TYPE['label']))
        self.clear_path_memory_button = self.button(recoverybar, '清除记忆', self.clear_path_memory, small=True)
        self.refresh_path_memory_info()
        return toolbar

    def build_status_line(self, bottom):
        status = tk.Frame(bottom, bg=COLORS['bg'])
        status.pack(fill='x', pady=(px(SPACE['sp2']), 0))
        self.status_bar = tk.Frame(status, bg=COLORS['card_hi'], width=px(4), height=px(16))
        self.status_bar.pack(side='left', padx=(0, px(SPACE['sp2'])))
        self.playback_label = self.field(status, 'playback', self.playback_status['message'],
                                         color='faint', font=ui_font(TYPE['label']), anchor='w', width=1)
        self.playback_label.pack(side='left', fill='x', expand=True)
        return status

    # ---------- 侧栏 ----------

    def build_sidebar(self, sidebar):
        # Keep short content at the top; scrolling is only for actual overflow.
        self.sidebar_canvas = tk.Canvas(sidebar, width=1, height=1, bg=COLORS['card'],
                                        bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(sidebar, orient='vertical', command=self.sidebar_canvas.yview)
        scrollbar.pack(side='right', fill='y', pady=px(SPACE['sp2']), padx=(0, px(SPACE['sp1'])))
        self.sidebar_canvas.pack(side='left', fill='both', expand=True,
                                 padx=(px(SPACE['sp3']), 0), pady=px(SPACE['sp2']))
        self.sidebar_canvas.configure(yscrollcommand=scrollbar.set)
        inner = tk.Frame(self.sidebar_canvas, bg=COLORS['card'])
        self.sidebar_inner = inner
        sidebar_window = self.sidebar_canvas.create_window(0, 0, window=inner, anchor='nw')

        def resize_sidebar(event=None):
            width = self.sidebar_canvas.winfo_width()
            if float(self.sidebar_canvas.itemcget(sidebar_window, 'width')) != width:
                self.sidebar_canvas.itemconfigure(sidebar_window, width=width)
            height = self.sidebar_canvas.winfo_height()
            content_height = inner.winfo_reqheight()
            # A changing notice/viewport must not leave a negative canvas
            # origin and a blank band above the first section.
            region = (0, 0, width, max(height, content_height))
            if region != getattr(self, 'sidebar_region', None):
                self.sidebar_region = region
                self.sidebar_canvas.configure(scrollregion=region)
            if content_height <= height:
                self.sidebar_canvas.yview_moveto(0)

        inner.bind('<Configure>', resize_sidebar)
        self.sidebar_canvas.bind('<Configure>', resize_sidebar)
        inner.grid_columnconfigure(1, weight=1)
        head = self.section_header(inner, '游戏连接', tag='LINK')
        head.grid(row=0, column=0, sticky='w', pady=(0, px(SPACE['sp1'])))
        liveinfo = tk.Frame(inner, bg=COLORS['card'])
        liveinfo.grid(row=0, column=1, columnspan=2, sticky='e', pady=(0, px(SPACE['sp1'])))
        self.status_dot = tk.Canvas(liveinfo, width=px(14), height=px(14), bg=COLORS['card'], bd=0,
                                    highlightthickness=0)
        self.status_dot.pack(side='left', padx=(0, px(SPACE['sp1'])))
        self.status_dot_id = self.status_dot.create_oval(px(4), px(4), px(10), px(10),
                                                         fill=COLORS['muted'], outline='')
        self.field(liveinfo, 'updated', '—', color='faint', font=mono_font(TYPE['micro'])).pack(side='left')
        self.label(inner, '连接状态', color='faint', font=ui_font(TYPE['label'])).grid(
            row=1, column=0, sticky='w', padx=(0, px(SPACE['sp3'])))
        self.field(inner, 'freshness', '—', font=mono_font(TYPE['value']), anchor='w').grid(
            row=1, column=1, sticky='w')
        self.reconnect_button = self.button(inner, '重新连接', self.reconnect, small=True)
        self.reconnect_button.grid(row=1, column=2, sticky='e')
        self.build_xyz_block(inner, row=2)
        self.notice_bar = tk.Frame(inner, bg=COLORS['card'], height=px(CONTROL['notice']),
                                   highlightbackground=COLORS['card'], highlightthickness=CONTROL['border'])
        self.notice_bar.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(px(6), 0))
        self.notice_bar.pack_propagate(False)
        self.message = tk.Label(self.notice_bar, text='', bg=COLORS['card'], fg=COLORS['muted'],
                                anchor='w', justify='left', font=ui_font(TYPE['hint']), padx=px(6))
        self.message.pack(fill='both', expand=True)
        self.notice_bar.grid_remove()
        self.rule(inner).grid(row=4, column=0, columnspan=3, sticky='ew', pady=px(4))
        self.section_header(inner, '角色状态', tag='STATUS').grid(row=5, column=0, columnspan=3, sticky='w')
        self.build_tool_card(inner, row=6)
        self.rule(inner).grid(row=7, column=0, columnspan=3, sticky='ew', pady=px(4))
        self.section_header(inner, '采集', tag='GATHER').grid(row=8, column=0, sticky='w')
        specialbar = tk.Frame(inner, bg=COLORS['card'])
        specialbar.grid(row=9, column=0, columnspan=3, sticky='ew', pady=(px(SPACE['sp1']), 0))
        self.button(specialbar, '采集萤石', lambda: self.start_special_gather('fluorite'), small=True).pack(
            side='left', fill='x', expand=True, padx=(0, px(SPACE['sp1'])))
        self.button(specialbar, '采集橡木', lambda: self.start_special_gather('roaming_oak'), small=True).pack(
            side='left', fill='x', expand=True)
        pressbar = tk.Frame(inner, bg=COLORS['card'])
        pressbar.grid(row=8, column=1, columnspan=2, sticky='e')
        self.label(pressbar, '连按 F', color='faint', font=ui_font(TYPE['label'])).pack(side='left')
        self.special_press_var = tk.StringVar(self.root, value='3')
        self.dropdown(pressbar, self.special_press_var, ['3', '5', '10', '20', '30'], width=4).pack(
            side='left', padx=px(SPACE['sp2']))
        self.label(pressbar, '次', color='faint', font=ui_font(TYPE['label'])).pack(side='left')
        self.rule(inner).grid(row=11, column=0, columnspan=3, sticky='ew', pady=px(4))
        maphead = tk.Frame(inner, bg=COLORS['card'])
        maphead.grid(row=12, column=0, columnspan=3, sticky='ew')
        maphead.grid_columnconfigure(1, weight=1)
        self.section_header(maphead, '内存地图', tag='MAP').grid(row=0, column=0, sticky='w')
        self.scene_range_var = tk.StringVar(self.root, value=f'{self.scene_radius:g} 单位')
        self.scene_range_menu = self.dropdown(maphead, self.scene_range_var,
                                              [f'{r} 单位' for r in SCENE_RANGE_OPTIONS], width=8,
                                              command=self.set_scene_radius)
        self.scene_range_menu.grid(row=0, column=1, sticky='e')
        switches = tk.Frame(inner, bg=COLORS['card'])
        switches.grid(row=13, column=0, columnspan=3, sticky='ew', pady=(px(SPACE['sp1']), 0))
        for index, (text, variable) in enumerate([
                ('场景物体', self.scene_objects_var), ('山体与碰撞', self.scene_colliders_var),
                ('河道与湖泊', self.scene_water_var), ('参考道路', self.scene_roads_var),
                ('围栏／护栏', self.scene_barriers_var)]):
            self.check(switches, text, variable, command=self.scene_layer_changed,
                       font=ui_font(TYPE['label'])).grid(row=index // 2, column=index % 2,
                                                         sticky='w', padx=(0, px(SPACE['sp3'])), pady=0)
        # 场景统计文本块（物体/碰撞/缓存、围栏/护栏、全图、动态）已移除，不再占侧栏高度

        def scroll_sidebar(event):
            if inner.winfo_reqheight() > self.sidebar_canvas.winfo_height():
                self.sidebar_canvas.yview_scroll(-int(event.delta/120), 'units')
            return 'break'

        def bind_scroll(widget):
            widget.bind('<MouseWheel>', scroll_sidebar, add='+')
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(self.sidebar_canvas)

    def build_xyz_block(self, inner, row):
        """Three HUD gauges instead of three look-alike input boxes."""
        self.xyz_row = tk.Frame(inner, bg=COLORS['card'])
        self.xyz_row.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(px(SPACE['sp1']), 0))
        self.xyz_row.grid_columnconfigure((0, 1, 2), weight=1, uniform='coordinates')
        self.xyz_readouts = {}
        for column, (axis, color) in enumerate(zip('XYZ', ['red', 'green', 'blue'])):
            variable = DisplayValue(self.root, value='—')
            self.fields[axis] = variable
            readout = HudReadout(self.xyz_row, axis, accent=color, variable=variable, height=46)
            readout.grid(row=0, column=column, sticky='nsew',
                         padx=(0, px(SPACE['sp1']) if column < 2 else 0))
            self.xyz_readouts[axis] = readout
        return self.xyz_row

    def build_tool_card(self, inner, row):
        toolcard = tk.Frame(inner, bg=COLORS['card'])
        toolcard.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(px(SPACE['sp1']), 0))
        toolcard.grid_columnconfigure(1, weight=1)
        self.tool_title = self.label(toolcard, '手持工具耐久', font=ui_font(TYPE['label'], True))
        self.tool_title.grid(row=0, column=0, sticky='w', padx=(0, px(SPACE['sp2'])))
        self.tool_value = self.field(toolcard, 'tool_durability', '未读取', color='faint',
                                     font=mono_font(TYPE['value']), anchor='e')
        self.tool_value.grid(row=0, column=1, sticky='ew')
        self.tool_progress = SegmentedBar(toolcard, segments=24, height=9)
        self.tool_progress.grid(row=1, column=0, columnspan=2, sticky='ew',
                                pady=(px(SPACE['sp1']), px(2)))
        self.tool_hint = self.label(toolcard, '进入角色后自动检测当前手持工具', color='faint',
                                    font=ui_font(TYPE['hint']), anchor='w', wraplength=px(120), justify='left')
        self.tool_hint.grid(row=2, column=0, sticky='ew')
        self.repair_auto_var = tk.BooleanVar(self.root, True)
        self.repair_auto_check = self.check(toolcard, '低于20%自动维修', self.repair_auto_var,
                                            font=ui_font(TYPE['hint']), wraplength=px(150), justify='left')
        self.repair_auto_check.grid(row=2, column=1, sticky='e')
        self.label(toolcard, '角色体力', font=ui_font(TYPE['label'], True)).grid(
            row=4, column=0, sticky='w', pady=(px(SPACE['sp2']), 0))
        self.stamina_value = self.field(toolcard, 'stamina', '未读取', color='faint',
                                        font=mono_font(TYPE['value']), anchor='e')
        self.stamina_value.grid(row=4, column=1, sticky='ew', pady=(px(SPACE['sp2']), 0))
        self.stamina_progress = SegmentedBar(toolcard, segments=24, height=9)
        self.stamina_progress.grid(row=5, column=0, columnspan=2, sticky='ew',
                                   pady=(px(SPACE['sp1']), px(2)))
        self.food_auto_var = tk.BooleanVar(self.root, True)
        self.food_auto_check = self.check(toolcard, '体力低于30，进食至100', self.food_auto_var,
                                          font=ui_font(TYPE['hint']))
        self.food_auto_check.grid(row=6, column=0, columnspan=2, sticky='w', pady=(px(SPACE['sp1']), 0))
        return toolcard

    def build_scene_filters(self, parent):
        self.scene_objects_var, self.scene_colliders_var = tk.BooleanVar(self.root, True), tk.BooleanVar(self.root, True)
        self.scene_water_var = tk.BooleanVar(self.root, True)
        self.scene_roads_var = tk.BooleanVar(self.root, True)
        self.scene_barriers_var = tk.BooleanVar(self.root, True)
        for column in range(7):
            parent.grid_columnconfigure(column, weight=1, uniform='category')
        self.scene_all_button = self.button(parent, '全部分类', lambda: self.set_scene_categories(True), small=True)
        self.scene_all_button.grid(row=0, column=0, sticky='nsew', padx=px(2), pady=px(2))
        self.scene_none_button = self.button(parent, '清空显示', lambda: self.set_scene_categories(False), small=True)
        self.scene_none_button.grid(row=1, column=0, sticky='nsew', padx=px(2), pady=px(2))
        for button in (self.scene_all_button, self.scene_none_button):
            button.configure(padx=px(SPACE['sp1']), font=ui_font(TYPE['label']))
        self.scene_filter_buttons = {}
        for index, (category, (label, _)) in enumerate(SCENE_CATEGORIES.items()):
            button = self.button(parent, label,
                                 lambda category=category: self.toggle_scene_category(category), small=True)
            button.configure(padx=px(SPACE['sp1']), font=ui_font(TYPE['label']))
            button.grid(row=index // 6, column=index % 6 + 1, sticky='nsew', padx=px(2), pady=px(2))
            self.scene_filter_buttons[category] = button
        self.global_minerals_button = self.button(parent, '全图资源总览', self.show_mineral_overview, small=True)
        self.global_minerals_button.grid(row=(len(SCENE_CATEGORIES)+5)//6, column=0, columnspan=7,
                                         sticky='ew', padx=px(2), pady=px(2))
