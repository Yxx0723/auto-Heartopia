"""User-operated, read-only blueberry survey controls."""
from pathlib import Path
import sys
import json
import tkinter as tk
from tkinter import messagebox

from utils.core.coordinate_points import validate_points
from utils.drivers.blueberry_survey import BlueberrySurvey
from utils.drivers.coordinate_point_store import PointStore


class BlueberrySurveyPanel:
    def __init__(self, owner, parent):
        self.owner = owner
        self.survey = BlueberrySurvey(owner.routes.root)
        self.enabled = False
        self.last_snapshot = None
        self.version = None
        self.frame = owner.frame(parent, card=True, border=False)
        self.count = owner.label(self.frame, '蓝莓资源调查', color='muted')
        self.count.pack(fill='x')
        for text, command in (('扫描附近蓝莓', self.scan), ('查看发现结果', self.show),
                              ('更新蓝莓路线', self.update_route)):
            owner.button(self.frame, text, command, small=True).pack(side='left', expand=True)
        self.refresh_count()

    def route_store(self):
        return PointStore(self.owner.routes.path('蓝莓'))

    def refresh_count(self):
        try:
            rows = self.survey.load()
            points = self.route_store().load()['points']
            checked = sum(self.survey.verified(row, self.version) for row in rows)
            self.count.configure(text=f'蓝莓调查 · 已发现 {len(rows)} · 已验证 {checked} · 待核查 {len(rows)-checked} · 路线 {len(points)}')
        except (OSError, ValueError):
            self.count.configure(text='蓝莓调查 · 记录或路线无法读取')

    def snapshot(self):
        worker = self.owner.scene_worker
        pid = self.owner.view.status.get('pid')
        if self.owner.view.paused or self.owner.view.position is None or worker is None:
            return None
        return worker.survey_snapshot(pid)

    def poll(self):
        if not self.enabled:
            return
        scene = self.snapshot()
        if scene is None or scene is self.last_snapshot:
            return
        self.last_snapshot = scene
        try:
            self.survey.observe(scene)
            self.version = scene['survey_version']
            self.refresh_count()
        except (OSError, ValueError):
            self.enabled = False
            self.count.configure(text='蓝莓调查 · 保存失败，已停止调查')

    def scan(self):
        if self.snapshot() is None:
            messagebox.showinfo('蓝莓调查', '等待当前主地图的有效场景快照后再扫描。', parent=self.owner.root)
            return
        self.enabled = True
        self.last_snapshot = None
        self.poll()

    def show(self):
        try:
            rows = self.survey.load()
        except (OSError, ValueError) as exc:
            messagebox.showerror('蓝莓调查', str(exc), parent=self.owner.root)
            return
        top = tk.Toplevel(self.owner.root)
        top.title('蓝莓发现结果')
        tk.Label(top, text='仅涵盖已扫描区域。手动走到站位并确认采到蓝莓后，才可登记。').pack()
        results = tk.Listbox(top, width=58, height=14, exportselection=False)
        results.pack(fill='both', expand=True)
        for row in rows:
            status = '已验证' if self.survey.verified(row, self.version) else '待核查'
            results.insert('end', f'{status} · ' + ', '.join(f'{x:.2f}' for x in row['position']))

        def verify():
            try:
                self.owner.require_route_change()
                selected = results.curselection()
                if not selected:
                    raise ValueError('请先选中已调查的蓝莓')
                if not messagebox.askyesno('确认站位',
                        '你是否已安全走到当前站位，并在此处手动交互、确认采到所选蓝莓？', parent=top):
                    return
                scene = self.snapshot()
                if scene is None:
                    raise ValueError('没有可核验的实时场景快照')
                position = self.owner.view.point_snapshot(self.owner.clock())['position']
                self.survey.verify_stand(scene, position, rows[selected[0]]['position'])
                self.refresh_count()
                top.destroy()
            except (OSError, ValueError) as exc:
                messagebox.showerror('站位未登记', str(exc), parent=top)
        tk.Button(top, text='登记已手动采到的当前站位', command=verify).pack()

    def update_route(self):
        try:
            self.owner.require_route_change()
            store = self.route_store()
            if not store.path.exists():
                base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
                seed = validate_points(json.loads((base/'data'/'routes'/'蓝莓.json').read_text(encoding='utf-8')))
                if messagebox.askyesno('导入蓝莓路线', f'导入已有的 {len(seed["points"])} 点蓝莓基线路线？',
                                       parent=self.owner.root):
                    self.owner.require_route_change()
                    store.create(seed)
                    self.owner.refresh_route_menu()
                    self.refresh_count()
                return
            scene = self.snapshot()
            if scene is None:
                raise ValueError('等待当前主地图的有效场景快照')
            original = store.load()
            additions, duplicates, pending = self.survey.preview(original, scene)
            counts = f'新增 {len(additions)} · 重复 {duplicates} · 待核查 {pending}'
            if not additions:
                messagebox.showinfo('蓝莓路线', counts, parent=self.owner.root)
                return
            if not messagebox.askyesno('更新蓝莓路线', counts+'\n保留已有点位，确认追加？', parent=self.owner.root):
                return
            self.owner.require_route_change()
            current = self.snapshot()
            if current is None or self.survey.preview(original, current)[0] != additions:
                raise ValueError('现场证据已变化，请重新预览')
            self.survey.update_route(store, original, additions)
            if self.owner.route_name == '蓝莓':
                self.owner.activate_route('蓝莓')
            else:
                self.owner.refresh_route_menu()
            self.refresh_count()
        except (OSError, ValueError) as exc:
            messagebox.showerror('蓝莓路线未更新', str(exc), parent=self.owner.root)
