"""Route, capture, filter and playback commands for the native window."""
import json
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from utils.core.coordinate_ce_table import current_position_table
from utils.core.coordinate_trail_export import trail_capture_payload
from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.drivers.route_path_memory import RoutePathMemory
from utils.ui.point_wait_editor import PointWaitEditor
from utils.ui.monitor_style import COLORS, SCENE_CATEGORIES, SCENE_RANGE_OPTIONS
from utils.ui.monitor_widgets import configure_changed


class MonitorController:
    def toggle_scene_filters(self):
        self.scene_filters_expanded = not self.scene_filters_expanded
        if self.scene_filters_expanded:
            self.map_leave()
            self.scene_filters_frame.place(x=10, y=10, relwidth=1., width=-20)
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
                             fg=COLORS['primary_text'] if selected else COLORS['text'])
        for category, button in self.scene_filter_buttons.items():
            label, color = SCENE_CATEGORIES[category]
            selected = visible and self.scene_categories is not None and category in self.scene_categories
            button.configure(text=('✓ ' if selected else '  ') + label,
                             bg=COLORS['button_pressed'] if selected else COLORS['button'],
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

    def refresh_scene(self, pid=None):
        if self.scene_worker and not getattr(self, 'closed', False):
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
        self.route_menu.configure(values=self.routes.names())

    def require_route_change(self):
        if getattr(self, 'route_optimizer', None) and self.route_optimizer.active:
            raise ValueError('路线正在优化，请完成后再切换或管理。')
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

    def start_special_gather(self, category):
        try:
            if getattr(self, 'route_optimizer', None) and self.route_optimizer.active:
                raise ValueError('路线正在优化，请完成后再开始巡查。')
            snapshot = self.view.point_snapshot(self.clock())
            if self.view.status.get('attached_to_parent'):
                raise ValueError('载具位置读取正常；自动巡查使用步行寻路，请先下车后再启动')
            scene = self.navigation_scene(self.view.status.get('pid')) or {}
            name = SCENE_CATEGORIES[category][0]
            from utils.core.special_patrol import make_patrol_data
            stamp = self.wall_clock().isoformat(timespec='milliseconds')
            data = make_patrol_data(scene, snapshot['position'], category, name,
                                    int(self.special_press_var.get()), stamp)
            self.playback.start(data, self.view.status.get('pid'),
                recovery_attempts=int(self.recovery_count_var.get()), jump_on_recovery=self.recovery_jump_var.get(),
                collect_wait_seconds=0., loop_count=1, auto_navigate=True,
                auto_repair=self.repair_auto_var.get(), auto_eat=self.food_auto_var.get(),
                route_path=self.point_store.path.parent/f'动态采集-{name}.json', remember_paths=False,
                stable_navigation=True)
            count = len(data['special_patrol']['checkpoints'])
            self.playback_status = {'state': 'playback_wait', 'message':
                f'{name}巡查采集已就绪 · {count} 个房门巡查点 · 到点确认后采集 · 切回游戏按 F6 · F9停止'}
            self.render()
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始动态采集', str(exc), parent=self.root)

    def start_playback(self):
        try:
            if getattr(self, 'route_optimizer', None) and self.route_optimizer.active:
                raise ValueError('路线正在优化，请完成后再回放。')
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
                                route_path=self.point_store.path,
                                stable_navigation=True, skip_unreachable=True,
                                remember_paths=False)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始点位回放', str(exc), parent=self.root)
            return
        self.saved_points, self.points_error = data['points'], ''
        self.playback_status = {'state': 'playback_wait',
                                'message': f'「{self.route_name}」 · {self.start_mode_var.get()} · 循环 {loop_count} 次'
                                           f' · {"自动寻路" if self.navigation_var.get() else "直接前往"}'
                                           f' · 稳定绕行优先，失败点稍后补采 · 切回游戏按 F6 · F9 停止'}
        self.render()

    def optimize_current_route(self):
        try:
            self.require_route_change()
            snapshot = self.view.point_snapshot(self.clock())
            pid = self.view.status.get('pid')
            scene = self.navigation_scene(pid) or {}
            data = self.point_store.load()
            loops = validate_loop_count(int(self.loop_count_var.get()))
            context = {'name': self.route_name, 'data': data, 'pid': pid,
                       'scene': scene.get('cache_context')}
            self.route_optimizer.start(data, snapshot['position'], scene, loops, context)
            self.hint.configure(text='正在估算绕行代价并优化顺序，原路线保留…', fg=COLORS['green'])
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法优化路线', str(exc), parent=self.root)

    def finish_route_optimization(self, result):
        try:
            if 'error' in result:
                raise ValueError(result['error'])
            context = result['context']
            scene = self.navigation_scene(self.view.status.get('pid')) or {}
            if self.playback.active or context['name'] != self.route_name \
                    or context['pid'] != self.view.status.get('pid') \
                    or context['scene'] != scene.get('cache_context') \
                    or context['data'] != self.point_store.load():
                raise ValueError('路线或游戏场景已变化，未保存过期优化结果，请重新优化。')
            # Short, unique filename; never overwrite the user's source route.
            stamp = self.wall_clock().strftime('%H%M%S-%f')
            name = f'{self.route_name[:24]}-优化-{stamp}'
            self.routes.create(name, result['data'])
            self.activate_route(name)
            detail = result['data']['route_optimization']
            note = '部分路段待实走确认' if detail['unknown_edges'] else '仍需实时核对通行'
            self.hint.configure(text=f'已另存优化路线 · 原路线保留 · {note}', fg=COLORS['green'])
        except (OSError, ValueError) as exc:
            messagebox.showerror('路线优化未保存', str(exc), parent=self.root)

    def collection_wait(self):
        return validate_collect_wait(float(self.collect_wait_var.get()))

    def refresh_path_memory_info(self):
        if not hasattr(self, 'path_memory_label'):
            return
        configure_changed(self.path_memory_label, text='已禁用')

    def clear_path_memory(self):
        try:
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
            if self.playback.active:
                raise ValueError('请先停止回放，再管理点位。')
            if self.wait_editor is not None and self.wait_editor.root.winfo_exists():
                self.wait_editor.root.lift()
                return self.wait_editor
            self.wait_editor = PointWaitEditor(self.root, self.point_store, self.collection_wait(),
                                               self.refresh_saved_points, COLORS)
            return self.wait_editor
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法管理点位', str(exc), parent=self.root)

    def refresh_saved_points(self, data):
        if len(data['points']) != len(self.saved_points) and not self.playback.active:
            self.playback_status = {'state': 'idle', 'message': '路线点位已更新 · 按剩余点位顺序回放'}
        self.saved_points, self.points_error = data['points'], ''
        self.refresh_path_memory_info()
        self.render()

    def stop_playback(self):
        self.playback.stop()
        self.playback_status = {'state': 'stopping', 'message': '正在停止回放并释放按键…'}
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
