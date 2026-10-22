"""Native Tk lifecycle and application assembly. No browser or HTTP server."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from utils.core.coordinate_monitor_view import MonitorView
from utils.core import thresholds as policy
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.ui.coordinate_monitor import CoordinateMonitor
from utils.ui.point_playback import PointPlayback
from utils.ui.scene_map_worker import SceneMapWorker
from utils.drivers.mono_scene import MemorySceneMap
from utils.ui.monitor_style import COLORS, SCENE_CATEGORIES
from utils.ui.monitor_worker import MonitorWorker
from utils.ui.monitor_layout import MonitorLayout
from utils.ui.monitor_painter import MonitorPainter
from utils.ui.monitor_controller import MonitorController
from utils.ui.route_optimization import RouteOptimizationJob


class CoordinateWindow(MonitorLayout, MonitorPainter, MonitorController):
    """Own one Tk session; role classes share this state and never start readers."""

    def __init__(self, root, monitor, resources, clock=time.monotonic, point_path=None, wall_clock=None,
                 playback_runner=None, scene_factory=None):
        self.root, self.resources, self.clock = root, Path(resources), clock
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        data_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) \
            else Path(__file__).resolve().parents[2]
        self.trail_export_dir = (data_dir / '道路数据' / '实走轨迹' if getattr(sys, 'frozen', False)
                                 else Path(__file__).resolve().parents[4] / 'outputs' / '道路数据' / '实走轨迹')
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
        diagnostics_dir = Path(point_path).parent if point_path else data_dir
        self.playback = PointPlayback(self.resources / 'profile.json', runner=playback_runner,
                                      scene_provider=self.navigation_scene, scene_refresh=self.refresh_scene,
                                      diagnostics_path=diagnostics_dir / '日志' / '点位回放.jsonl')
        self.playback_status = {'state': 'idle', 'message': '按保存顺序回放 · F6开始/暂停 · F9停止'}
        self.scene_state = {'state': 'waiting', 'objects': [], 'colliders': []}
        # None means the explicit "all" mode; an empty set hides every object category.
        self.scene_categories = set(SCENE_CATEGORIES)-{'other'}
        self.scene_filters_expanded = False
        self.scene_worker = SceneMapWorker(self.resources / 'profile.json', factory=scene_factory,
            special_locations_path=data_dir/'地图缓存'/'动态资源位置线索.json',
            fixed_resources_path=data_dir/'地图缓存'/'固定资源.json',
            diagnostics_path=diagnostics_dir/'日志'/'场景读取.jsonl') if scene_factory else None
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
        self.player_screen = None
        self.radar_job = None
        self.route_optimizer = RouteOptimizationJob()
        root.title('心动小镇 · 坐标观测台（窗口版）')
        root.configure(bg=COLORS['bg'])
        root.geometry('1060x818+40+30')
        root.minsize(1020, 640)
        # Keep the window opaque: whole-window alpha makes Windows composite the
        # game behind every map repaint, including during dragging.
        try:
            root.attributes('-alpha', 1.)
        except tk.TclError:
            pass
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
        self.start_radar()

    def tick(self):
        if self.closed:
            return
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
            if playback.get('state') in ('reached', 'finished', 'stopped', 'error'):
                self.refresh_path_memory_info()
        optimized = self.route_optimizer.take_latest()
        if optimized is not None:
            self.finish_route_optimization(optimized)
        self.render()
        self.timer = self.root.after(int(policy.COORD_MONITOR_REFRESH_SECONDS * 1000), self.tick)

    def reconnect(self):
        if self.playback.active:
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

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_radar()
        self.route_optimizer.close()
        self.root.after_cancel(self.timer)
        if getattr(self, 'map_hover_job', None) is not None:
            self.root.after_cancel(self.map_hover_job)
        if self.draw_job is not None:
            self.root.after_cancel(self.draw_job)
        self.playback.close()
        if self.scene_worker:
            self.scene_worker.close()
        self.worker.close()
        self.root.destroy()


def _dark_title_bar(root):
    """Kept for API compatibility; the v2 skin uses the system caption colour."""
    return


def main(playback_runner=None):
    parser = argparse.ArgumentParser(description='心动小镇原生坐标监视窗口')
    parser.add_argument('--check-output', type=Path)
    parser.add_argument('--check-scene-output', type=Path)
    parser.add_argument('--check-food-output', type=Path)
    args = parser.parse_args()
    resources = Path(sys._MEIPASS) / 'monitor' if getattr(sys, 'frozen', False) \
        else Path(__file__).resolve().parents[2] / 'resources' / 'monitor'
    monitor = CoordinateMonitor(resources / 'profile.json')
    if args.check_food_output:
        try:
            status = monitor.status()
            if status['state'] != 'connected':
                raise ValueError('请进入角色后读取背包')
            foods = monitor.reader.food_inventory_snapshot()
            result = {'read_only': True, 'state': 'ready', 'foods': foods,
                      'unique_foods': len({f['name'] for f in foods}), 'real_game_input': False,
                      'target_stamina': 100, 'query': monitor.reader.food_search_query()}
            args.check_food_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            monitor.close()
        return
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
                                      'target_units': 100, 'selection': 'owned-backpack-random',
                                      'fruits': list(FRUITS.values()), 'button_shape': list(fruit.eat.shape[:2]),
                                      'close_shape': list(fruit.close_icon.shape[:2])}
            args.check_output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            monitor.close()
        return
    root = tk.Tk()
    CoordinateWindow(root, monitor, resources, playback_runner=playback_runner, scene_factory=MemorySceneMap)
    _dark_title_bar(root)
    root.mainloop()
