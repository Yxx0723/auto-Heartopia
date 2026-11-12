"""P1 task lifecycle, sharing P0 input ownership and cleanup."""
from copy import deepcopy
from datetime import datetime
import threading
from uuid import uuid4

from utils.drivers.cooking_selection import CookingSelectionReader
from utils.drivers.input_task_gate import InputTaskGate
from utils.ui.cooking_entry_control import CookingEntryControl


class CookingSelectionControl(CookingEntryControl):
    def __init__(self, *args, selection_runner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.selection_runner = selection_runner
        self.inventory_thread = None

    def read_inventory(self, pid):
        if type(pid) is not int or pid <= 0:
            return False

        def read():
            try:
                reader = self.reader_factory(pid, self.profile)
                try:
                    inventory = reader.cooking_inventory_snapshot(5)
                finally:
                    reader.close()
            except Exception as exc:
                self.update('inventory_error', '背包材料未确认：'+str(exc), inventory=None)
            else:
                self.update('inventory_ready', '已读取当前随身背包材料', inventory=inventory)

        with self.lock:
            if self.active or (self.inventory_thread and self.inventory_thread.is_alive()):
                return False
            self.inventory_thread = threading.Thread(target=read, name='cooking-stock-read', daemon=True)
            try:
                self.inventory_thread.start()
            except RuntimeError as exc:
                self.inventory_thread = None
                self.update('inventory_error', '背包材料未确认：'+str(exc), inventory=None)
                return False
            return True

    def start_selection(self, pid, recipe):
        if self.active or self.busy():
            raise ValueError('请先停止当前回放或烹饪验证')
        if self.inventory_thread and self.inventory_thread.is_alive():
            raise ValueError('正在读取背包材料，请读完后确认料理')
        if type(pid) is not int or pid <= 0 or self.runner is None or self.selection_runner is None:
            raise ValueError('游戏连接或 P1 选菜入口尚未就绪')
        if recipe.get('status') != 'confirmed':
            raise ValueError('料理数据尚未确认，不能自动选菜')
        window = self.window_factory(pid)
        self.stop_event.clear(); self.signature = None; self.pots = {}; self.path = None
        self.thread = threading.Thread(target=self._run, args=(pid, window, deepcopy(recipe)),
                                       name='cooking-selection-p1', daemon=True)
        self.thread.start()

    def _execute(self, pid, window, recipe=None):
        if recipe is None:
            return super()._execute(pid, window)
        device = controls = reader = None
        try:
            self.path = self.root/('选菜-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
            self.path.mkdir(parents=True, exist_ok=False)
            with self.lock:
                self.stream = (self.path/'events.jsonl').open('a', encoding='utf-8')
            device = self.device_factory(window)
            controls = self.controls_factory(window, window.size(), self, self.stop_event.is_set)
            reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
            menu = CookingSelectionReader(reader)
            inventory = lambda: reader.cooking_inventory_snapshot(5)
            controls.start()
            self.update('selection_wait', '已确认选择：'+recipe['name']+'；站到灶台旁后切回游戏按 F6，F9停止')
            while not self.stop_event.is_set():
                running, stopped = controls.snapshot()
                if stopped:
                    self.update('stopped', '选菜已停止，未开始制作')
                    return
                if not running:
                    self.clock.sleep(.02)
                    continue
                from utils.core.cooking_catalog import material_status
                stock = inventory()
                self.update('stock_check', '正在核对指定料理材料', inventory=stock)
                if not material_status(recipe, stock)['sufficient']:
                    raise ValueError('所选料理的当前背包可用材料不足，未打开灶台或更换料理')
                result = self.runner(window=window, controls=controls, device=device,
                    read_menu=menu.snapshot, read_recipe=menu.recipe.snapshot, inventory=inventory,
                    clock=self.clock, report=self.update, input_gate=InputTaskGate(),
                    journal=self.path/'entry.json', binding='p1-game-'+str(pid),
                    stove_ready=True, timeout=8., validate_default=False)
                if result['state'] == 'PAUSED':
                    continue
                if result['state'] != 'MENU_OPEN':
                    raise ValueError('灶台面板尚未确认：'+result.get('reason', result['state']))
                self.selection_runner(recipe=recipe, reader=menu, window=window, controls=controls,
                    device=device, inventory=inventory, clock=self.clock, report=self.update)
                return
            self.update('stopped', '选菜已停止，未开始制作')
        except Exception as exc:
            self.update('error', '选菜停止：'+type(exc).__name__+': '+str(exc))
        finally:
            for obj, method in ((device, 'release_all'), (controls, 'close'), (reader, 'close')):
                if obj is not None:
                    try: getattr(obj, method)()
                    except Exception: pass
            with self.lock:
                if self.stream:
                    self.stream.close(); self.stream = None
