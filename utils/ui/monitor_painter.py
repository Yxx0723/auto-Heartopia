"""Map/status painting, viewport interaction and repaint scheduling."""
from datetime import datetime
import math
from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy
from utils.ui.map_draw_cache import Viewport, object_key, muted_color
from utils.ui.map_presentation import MAP_STYLE, draw_grid, draw_chrome, draw_player, chrome_bounds, draw_waypoint_labels, draw_target_indicator
from utils.ui.map_labels import layout_waypoints, HoverTarget, target_indicator
from utils.ui.monitor_style import COLORS, FAUNA_STYLES, MAP_PIXELS_PER_UNIT, MINERAL_STYLES, PLANT_STYLES, SCENE_CATEGORIES, SCENE_RANKS
from utils.ui.monitor_widgets import configure_changed
from utils.ui.monitor_feedback import user_feedback


class MonitorPainter:
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

    def render(self):
        if self.interaction_pending():
            return
        view, status = self.view, self.view.status
        live = view.position is not None and not view.paused
        playing = self.playback.active
        optimizing = bool(getattr(self, 'route_optimizer', None) and self.route_optimizer.active)
        if hasattr(self, 'optimize_route_button'):
            configure_changed(self.optimize_route_button, state='normal' if live and self.saved_points
                              and not playing and not optimizing and not self.points_error else 'disabled')
        configure_changed(self.route_menu, state='disabled' if playing else 'readonly')
        for action, button in self.route_buttons.items():
            enabled = not playing and (action not in ('copy', 'rename') or not self.points_error)
            if action == 'delete':
                enabled = enabled and self.point_store.path.exists()
            configure_changed(button, state='normal' if enabled else 'disabled')
        configure_changed(self.start_menu, state='disabled' if playing else 'readonly')
        configure_changed(self.recovery_menu, state='disabled' if playing else 'readonly')
        configure_changed(self.recovery_jump_check, state='disabled' if playing else 'normal')
        configure_changed(self.navigation_check, state='disabled' if playing else 'normal')
        if hasattr(self, 'path_memory_check'):
            configure_changed(self.path_memory_check, state='disabled')
            configure_changed(self.clear_path_memory_button, state='disabled')
        configure_changed(self.repair_auto_check, state='disabled' if playing else 'normal')
        if hasattr(self, 'food_auto_check'):
            configure_changed(self.food_auto_check, state='disabled' if playing else 'normal')
        configure_changed(self.collect_wait_spin, state='disabled' if playing else 'normal')
        configure_changed(self.loop_count_spin, state='disabled' if playing else 'normal')
        configure_changed(self.point_wait_button, state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        if hasattr(self, 'delete_point_button'):
            configure_changed(self.delete_point_button, state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        configure_changed(self.clear_points_button, state='normal' if self.saved_points and not playing and not self.points_error else 'disabled')
        configure_changed(self.save_point_button, state='normal' if live and not playing else 'disabled')
        configure_changed(self.play_button, state='normal' if live and self.saved_points and not playing and not optimizing and not self.points_error else 'disabled')
        configure_changed(self.stop_button, state='normal' if playing else 'disabled')
        self.fields['playback'].set(user_feedback(self.playback_status['message']))
        playback_color = COLORS['red' if self.playback_status['state'] in ('error', 'blocked', 'repair_paused', 'equip_paused', 'food_paused') else 'green' if playing else 'muted']
        configure_changed(self.playback_label, fg=playback_color)
        configure_changed(self.expanded_status, fg=playback_color)
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
            text += f'\n全图树木 {counts.get("tree", 0)} · 蓝莓 {counts.get("blueberry", 0)} · 树莓 {counts.get("raspberry", 0)}'
        if scene.get('global_special_state') == 'ready':
            markers = scene.get('global_special_resources', ())
            labels = []
            for kind, name in (('roaming_oak', '橡木'), ('fluorite', '萤石')):
                count = sum(obj.get('category') == kind for obj in markers)
                labels.append(f'{name} {count}' if count else f'{name}暂未同步')
            text += '\n全图动态定位：' + ' · '.join(labels)
        if scene.get('global_map_state') == 'ready':
            resources = scene.get('global_map_resources', ())
            text += '\n已同步：' + ' · '.join(f'{label} {sum(o.get("category") == kind for o in resources)}'
                for kind, label in (('insect', '昆虫'), ('bird', '鸟'), ('fish', '鱼影')))
        configure_changed(self.scene_status, text=text)
        if hasattr(self, 'global_minerals_button'):
            configure_changed(self.global_minerals_button,
                              state='normal' if scene.get('global_resource_state') == 'ready'
                              or scene.get('global_special_state') == 'ready'
                              or scene.get('fixed_resource_cache', {}).get('state') == 'ready' else 'disabled')
        self.render_tool_durability()
        self.render_stamina()
        self.draw_if_changed()

    @staticmethod
    def draw_scene_symbol(canvas, x, z, category):
        if category in ('resource', 'crop'):
            color = '#a8e07c' if category == 'crop' else '#dfd19c'
            canvas.create_polygon(x, z-5, x+5, z, x, z+5, x-5, z,
                                  fill='#384631', outline=color, tags='scene-'+category)
            if category == 'crop':
                canvas.create_line(x, z+2, x, z-2, x+3, z-4, fill=color, tags='scene-'+category)
        elif category in MINERAL_STYLES:
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
            elif category == 'fish':
                canvas.create_oval(x-5, z-3, x+3, z+3, fill=fill, outline=outline, tags=tag)
                canvas.create_polygon(x+3, z, x+7, z-4, x+7, z+4,
                                      fill=fill, outline=outline, tags=tag)
                canvas.create_oval(x-3, z-1, x-1, z+1, fill=outline, outline='', tags=tag)
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
                mineral_points = [*mineral_points, *self.scene_state.get('global_special_resources', ())]
                mineral_points.extend(self.scene_state.get('global_map_resources', ()))
                mineral_points.extend(obj for obj in self.scene_state.get('objects', ())
                                      if obj.get('category') != 'other')
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
        for key, coords, _ in scene_boxes:
            kind = self.map_cache.geometry.barrier_kinds.get(key)
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
            'idle', 'finished', 'stopped', 'error') else None
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
            for obj in scene_objects:
                position = obj['position']
                name = SCENE_CATEGORIES.get(obj.get('category'), SCENE_CATEGORIES['other'])[0]
                detail = ('缓存参考 · ' if obj.get('map_cached') else '') + self.map_distance(position)
                if obj.get('map_definition'):
                    detail = '全图资源位置 · '+self.map_distance(position)+'\n资源定义，不表示当前可采集状态'
                elif obj.get('resource_global_validated'):
                    detail = '当前同步位置 · '+self.map_distance(position)+'\n交互/成熟状态未确认'
                    if obj.get('resource_item_type'):
                        detail += f' · 资源类型 {obj["resource_item_type"]}'
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
                id(scene.get('global_special_resources')), len(scene.get('global_special_resources', ())),
                id(scene.get('global_map_resources')), len(scene.get('global_map_resources', ())),
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
        self.scene_categories = set(SCENE_CATEGORIES)-{'other'}
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
        self.workspace.grid_columnconfigure(1, minsize=0 if self.map_expanded else 300)
        self.plotcard.grid_configure(padx=(0, 0 if self.map_expanded else 10))
        self.map_expand_button.configure(text='还原布局' if self.map_expanded else '大地图')
        self.expanded_status.grid() if self.map_expanded else self.expanded_status.grid_remove()
        self.root.update_idletasks()
        self.draw()
