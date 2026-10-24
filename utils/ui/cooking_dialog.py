"""Single-stove controls and user-driven local template calibration."""
import base64
import io
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import ImageTk

from utils.drivers.point_game_window import PointGameWindow
from utils.vision.cooking_menu import DEFAULT_REQUIRED


LABELS = {'menu': '选菜标题', 'recipe': '咖啡名称', 'materials': '四格咖啡豆',
          'start': '开始烹饪按钮', 'cooking': '烹饪中标记', 'stir': '翻炒提示',
          'collect': '收菜提示', 'interact': '灶台入口'}


class CookingDialog:
    def __init__(self, parent, control, pid, config_path, bundled_profile):
        self.control, self.pid = control, pid
        self.config_path = Path(config_path)
        self.top = tk.Toplevel(parent)
        self.top.title('单灶自动烹饪 · 默认食谱')
        self.top.geometry('470x550')
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        self.closed = False
        self.capture_pending = False
        self.status = tk.StringVar(value='站到灶台旁，开始后自动打开并使用默认食谱；F9停止')
        self.count = tk.StringVar(value='1')
        self.reserve = tk.StringVar(value='20')
        self.max_star = tk.StringVar(value='1')
        self.visual = {'schema': 'cooking-profile-v1', 'templates': {}}
        for path in (self.config_path, Path(bundled_profile)):
            if path.exists():
                try:
                    self.visual = json.loads(path.read_text('utf-8'))
                    break
                except (OSError, ValueError):
                    pass
        body = ttk.Frame(self.top, padding=12); body.pack(fill='both', expand=True)
        ttk.Label(body, text='使用灶台默认食谱 · 单灶', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(body, text='自动核对默认填料；材料未填满或受保护时停止，不自动换料。', wraplength=430).pack(anchor='w', pady=8)
        for text, var, high in (('成功锅数', self.count, 10000), ('每种食材至少保留', self.reserve, 1000000),
                                 ('允许原料最高星级', self.max_star, 5)):
            row = ttk.Frame(body); row.pack(fill='x', pady=4)
            ttk.Label(row, text=text, width=24).pack(side='left')
            ttk.Spinbox(row, from_=0 if var != self.count else 1, to=high, width=8,
                        textvariable=var).pack(side='left')
        buttons = ttk.Frame(body); buttons.pack(fill='x')
        ttk.Button(buttons, text='开始任务', command=self.start).pack(side='left', expand=True, fill='x')
        ttk.Button(buttons, text='停止 F9', command=control.stop).pack(side='left', expand=True, fill='x', padx=(8, 0))
        ttk.Label(body, textvariable=self.status, wraplength=425).pack(fill='x', pady=12)
        ttk.Label(body, text='画面标定：点击后3秒内切回游戏，随后在截图中框选对应图标。\n'
                  '标定只截图，不会自动投料；翻炒/收菜须分别采样。', wraplength=430).pack(anchor='w')
        self.calibration_buttons = {}
        grid = ttk.Frame(body); grid.pack(fill='x', pady=8)
        for i, name in enumerate(DEFAULT_REQUIRED):
            button = ttk.Button(grid, text=LABELS[name], command=lambda n=name: self.capture(n))
            button.grid(row=i//2, column=i%2, sticky='ew', padx=3, pady=3)
            self.calibration_buttons[name] = button
        grid.columnconfigure(0, weight=1); grid.columnconfigure(1, weight=1)
        self.job = self.top.after(200, self.poll)

    def poll(self):
        if self.closed:
            return
        for name, button in self.calibration_buttons.items():
            button.configure(text=('✓ ' if name in self.visual.get('templates', {}) else '')+LABELS[name],
                             state='disabled' if self.control.active or self.capture_pending else 'normal')
        result = self.control.take_latest()
        if result:
            self.status.set(result['message'])
        self.job = self.top.after(200, self.poll)

    def start(self):
        try:
            if self.capture_pending:
                raise ValueError('画面标定尚未完成')
            self.control.start(self.pid, self.visual, int(self.count.get()),
                               int(self.reserve.get()), int(self.max_star.get()))
            self.status.set('正在切回游戏并核对默认食谱；F6暂停，F9停止')
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始烹饪', str(exc), parent=self.top)

    def capture(self, name):
        if self.control.active or self.capture_pending:
            return
        self.capture_pending = True
        self.status.set('请在3秒内切回游戏，保持需要标定的提示可见')
        self.top.after(3000, lambda: self.pick(name))

    def pick(self, name):
        if self.closed:
            return
        try:
            window = PointGameWindow(self.pid)
            if self.control.active or self.control.busy() or not window.foreground():
                raise ValueError('请先停止其他任务，并切回游戏再截图')
            picture = window.snapshot()
            size = list(picture.size)
            existing = self.visual.get('client_size')
            if existing and existing != size:
                raise ValueError('截图尺寸与已有标定不同；请恢复原游戏尺寸')
            picker = tk.Toplevel(self.top); picker.title('框选：'+LABELS[name])
            scale = min(1., 1050/picture.width, 680/picture.height)
            preview = picture.resize((round(picture.width*scale), round(picture.height*scale)))
            image = ImageTk.PhotoImage(preview)
            canvas = tk.Canvas(picker, width=preview.width, height=preview.height, highlightthickness=0)
            canvas.pack(); canvas.create_image(0, 0, anchor='nw', image=image)
            canvas.image = image
            origin = []
            shape = []
            def down(event):
                origin[:] = [event.x, event.y]
                if shape: canvas.delete(shape[0])
                shape[:] = [canvas.create_rectangle(event.x, event.y, event.x, event.y, outline='red', width=2)]
            def move(event):
                if origin: canvas.coords(shape[0], *origin, event.x, event.y)
            def up(event):
                if not origin: return
                x1, x2 = sorted((round(origin[0]/scale), round(event.x/scale)))
                y1, y2 = sorted((round(origin[1]/scale), round(event.y/scale)))
                x1, x2 = max(0, x1), min(picture.width, x2)
                y1, y2 = max(0, y1), min(picture.height, y2)
                if min(x2-x1, y2-y1) < 8: return
                buffer = io.BytesIO(); picture.crop((x1, y1, x2, y2)).save(buffer, format='PNG')
                self.visual['client_size'] = size
                self.visual.setdefault('templates', {})[name] = {
                    'rect': [x1, y1, x2-x1, y2-y1], 'png': base64.b64encode(buffer.getvalue()).decode('ascii')}
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.config_path.with_suffix('.tmp')
                tmp.write_text(json.dumps(self.visual, ensure_ascii=False, indent=2), 'utf-8')
                tmp.replace(self.config_path)
                picker.destroy(); self.status.set(LABELS[name]+'已保存；开始任务前请确认当前食谱')
            canvas.bind('<ButtonPress-1>', down); canvas.bind('<B1-Motion>', move); canvas.bind('<ButtonRelease-1>', up)
            picker.transient(self.top)
            picker.lift()
            picker.grab_set()
            self.top.wait_window(picker)
        except (OSError, ValueError) as exc:
            self.status.set('标定未完成：'+str(exc))
        finally:
            self.capture_pending = False

    def close(self):
        self.closed = True
        self.top.after_cancel(self.job)
        self.control.stop()
        self.top.destroy()
