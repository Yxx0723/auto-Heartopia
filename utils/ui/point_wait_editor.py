"""Native editor for per-point collection waits; writes through PointStore."""
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from uuid import uuid4

from utils.core.coordinate_points import validate_collect_wait
from utils.core import thresholds as policy


class PointWaitEditor:
    def __init__(self, parent, store, default_wait, on_saved, colors):
        self.store, self.default_wait, self.on_saved = store, validate_collect_wait(default_wait), on_saved
        self.data = store.load()
        self.root = tk.Toplevel(parent)
        self.root.title('点位管理')
        self.root.configure(bg=colors['bg'])
        self.root.geometry('520x420')
        self.root.minsize(480, 340)
        self.root.transient(parent)
        tk.Label(self.root, text='选择一个点位，可设置采集等待时间或单独删除。\n删除前自动备份，剩余点位按原顺序重新编号。',
                 bg=colors['bg'], fg=colors['muted'], justify='left',
                 font=('Microsoft YaHei UI', -13)).pack(anchor='w', padx=18, pady=(14, 10))
        self.points = tk.Listbox(self.root, bg=colors['card'], fg=colors['text'],
            selectbackground=colors['green'], selectforeground='#1b2815',
            font=('Microsoft YaHei UI', -13), exportselection=False,
            highlightthickness=0, relief='flat')
        self.points.pack(fill='both', expand=True, padx=18)
        self.points.bind('<<ListboxSelect>>', self.select)
        form = tk.Frame(self.root, bg=colors['bg'])
        form.pack(fill='x', padx=18, pady=12)
        tk.Label(form, text='按 F 后等待', bg=colors['bg'], fg=colors['text']).pack(side='left')
        self.wait_var = tk.StringVar(self.root, value=f'{default_wait:g}')
        self.spin = tk.Spinbox(form, from_=0, to=policy.COORD_COLLECTION_MAX_WAIT, increment=.1,
            textvariable=self.wait_var, width=7, bg=colors['button'], fg=colors['text'],
            buttonbackground=colors['button'], insertbackground=colors['text'])
        self.spin.pack(side='left', padx=8)
        tk.Label(form, text='秒（0～300）', bg=colors['bg'], fg=colors['muted']).pack(side='left')
        actions = tk.Frame(self.root, bg=colors['bg'])
        actions.pack(fill='x', padx=18)
        self.save_button = tk.Button(actions, text='保存此点', command=lambda: self.save(False),
                                     bg=colors['green'], fg='#1b2815', relief='flat', padx=12, pady=6)
        self.save_button.pack(side='left')
        self.all_button = tk.Button(actions, text='全部使用此秒数', command=lambda: self.save(True),
                                    bg=colors['button'], fg=colors['text'], relief='flat', padx=12, pady=6)
        self.all_button.pack(side='left', padx=8)
        self.delete_button = tk.Button(actions, text='删除选中点位', command=self.delete_selected,
            bg=colors['button'], fg=colors['red'], relief='flat', padx=10, pady=6)
        self.delete_button.pack(side='left')
        tk.Button(actions, text='关闭', command=self.root.destroy,
                  bg=colors['button'], fg=colors['text'], relief='flat', padx=12, pady=6).pack(side='right')
        self.notice = tk.Label(self.root, text='设置保存到原点位文件，坐标与顺序保持不变。',
                               bg=colors['bg'], fg=colors['muted'], anchor='w')
        self.notice.pack(fill='x', padx=18, pady=12)
        self.refresh(self.data)
        self.root.grab_set()

    def refresh(self, data, selected_id=1):
        self.data = data
        self.points.delete(0, 'end')
        for point in data['points']:
            value = f'{point["collect_wait"]:g} 秒' if 'collect_wait' in point \
                else f'默认（{self.default_wait:g} 秒）'
            self.points.insert('end', f'{point["id"]:02}  ·  {point["name"]}      {value}')
        enabled = bool(data['points'])
        self.save_button.configure(state='normal' if enabled else 'disabled')
        self.all_button.configure(state='normal' if enabled else 'disabled')
        self.delete_button.configure(state='normal' if enabled else 'disabled')
        if enabled:
            self.points.selection_set(min(selected_id - 1, len(data['points']) - 1))
            self.select()

    def select(self, event=None):
        selection = self.points.curselection()
        if selection:
            point = self.data['points'][selection[0]]
            self.wait_var.set(f'{point.get("collect_wait", self.default_wait):g}')

    def delete_selected(self):
        try:
            selection = self.points.curselection()
            if not selection:
                raise ValueError('请先选择要删除的点位。')
            point = self.data['points'][selection[0]]
            xyz = ' / '.join(f'{v:.3f}' for v in point['position'])
            if not messagebox.askyesno('删除单个点位',
                    f'删除路线「{self.store.path.stem}」的第 {point["id"]} 个点位「{point["name"]}」？\n'
                    f'XYZ：{xyz}\n其他点位保留，删除前会备份到「点位/路线回收站」。',
                    parent=self.root, default='no'):
                return
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = self.store.path.parent/'路线回收站'/f'{self.store.path.stem}-删点前-{stamp}-{uuid4().hex[:8]}.json'
            data, _ = self.store.delete_point(point['id'], backup, expected_data=self.data)
        except (OSError, ValueError) as exc:
            self.notice.configure(text='删除失败，原点位保持完整。')
            messagebox.showerror('删除点位失败', str(exc), parent=self.root)
            return
        self.on_saved(data)
        self.refresh(data, min(point['id'], max(1, len(data['points']))))
        self.notice.configure(text=f'已删除「{point["name"]}」，剩余 {len(data["points"])} 个点位；原路线已备份。')

    def save(self, all_points):
        try:
            selection = self.points.curselection()
            if not selection:
                raise ValueError('请先选择点位。')
            point_id = self.data['points'][selection[0]]['id']
            seconds = validate_collect_wait(float(self.wait_var.get()))
            data = self.store.set_collect_wait(None if all_points else point_id, seconds, expected_data=self.data)
        except (OSError, ValueError) as exc:
            self.notice.configure(text='等待时间保存失败，原点位保持完整。')
            messagebox.showerror('等待时间保存失败', str(exc), parent=self.root)
            return
        self.on_saved(data)
        self.refresh(data, point_id)
        target = '全部点位' if all_points else f'点位 {point_id}'
        self.notice.configure(text=f'已保存：{target}按 F 后等待 {seconds:g} 秒。')
