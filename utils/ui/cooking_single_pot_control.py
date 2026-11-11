"""Single-pot task alongside the unchanged P0 and P1 entry points."""
from copy import deepcopy
from datetime import datetime
import threading
from uuid import uuid4

from utils.drivers.cooking_single_pot import SinglePotReader
from utils.ui.cooking_selection_control import CookingSelectionControl


class CookingSinglePotControl(CookingSelectionControl):
    def __init__(self,*args,single_pot_runner=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.single_pot_runner=single_pot_runner

    def start_pot(self,pid,recipe):
        if self.active or self.busy() or self.inventory_thread and self.inventory_thread.is_alive():
            raise ValueError('请先停止其他任务并等待读取完成')
        if type(pid) is not int or pid<=0 or self.single_pot_runner is None:
            raise ValueError('当前游戏或单锅入口未就绪')
        if recipe.get('status')!='confirmed' or not recipe.get('product_id'):
            raise ValueError('所选料理或产物编号尚未确认')
        window=self.window_factory(pid)
        self.stop_event.clear();self.signature=None;self.pots={};self.path=None
        self.thread=threading.Thread(target=self._run,args=(pid,window,deepcopy(recipe),True),
                                     name='cooking-single-pot-p2',daemon=True)
        self.thread.start()

    def start_batch(self,pid,recipe,target):
        if type(target) is not int or not 1<=target<=99:
            raise ValueError('连续制作锅数必须为 1～99')
        if self.active or self.busy() or self.inventory_thread and self.inventory_thread.is_alive():
            raise ValueError('请先停止其他任务并等待读取完成')
        if type(pid) is not int or pid<=0 or not all((self.runner,self.selection_runner,self.single_pot_runner)):
            raise ValueError('游戏连接或烹饪入口尚未就绪')
        if recipe.get('status')!='confirmed' or not recipe.get('product_id'):
            raise ValueError('料理或产物编号尚未确认')
        window=self.window_factory(pid)
        self.stop_event.clear();self.signature=None;self.pots={};self.path=None
        self.thread=threading.Thread(target=self._run,args=(pid,window,deepcopy(recipe),True,target),
                                     name='cooking-batch-p3',daemon=True)
        self.thread.start()

    def _execute(self,pid,window,recipe=None,single_pot=False,target=None):
        if target is not None:
            return self._execute_batch(pid,window,recipe,target)
        if not single_pot:
            return super()._execute(pid,window,recipe)
        reader=device=controls=None
        try:
            self.path=self.root/('单锅-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
            self.path.mkdir(parents=True,exist_ok=False)
            with self.lock:self.stream=(self.path/'events.jsonl').open('a',encoding='utf-8')
            device=self.device_factory(window)
            controls=self.controls_factory(window,window.size(),self,self.stop_event.is_set)
            reader=self.reader_factory(pid,self.profile,hwnd=window.hwnd)
            native=SinglePotReader(reader)
            controls.start()
            self.update('single_pot_wait','单锅已就绪：保持 P1 所选食谱打开，切回游戏按 F6；F9停止')
            while not self.stop_event.is_set():
                running,stopped=controls.snapshot()
                if stopped:break
                if not running:
                    self.clock.sleep(.02);continue
                self.single_pot_runner(recipe=recipe,reader=native,window=window,controls=controls,
                    device=device,inventory=lambda:reader.cooking_inventory_snapshot(5),clock=self.clock,
                    report=self.update,journal=self.path/'pot.json')
                return
            self.update('stopped','单锅任务已停止，未重新投料')
        except Exception as exc:
            self.update('error','单锅任务停止：'+type(exc).__name__+': '+str(exc))
        finally:
            for obj,method in ((device,'release_all'),(controls,'close'),(reader,'close')):
                if obj is not None:
                    try:getattr(obj,method)()
                    except Exception:pass
            with self.lock:
                if self.stream:self.stream.close();self.stream=None

    def _execute_batch(self,pid,window,recipe,target):
        from utils.flows.cooking_batch import run_batch
        from utils.drivers.coordinate_point_store import atomic_write_json
        reader=device=controls=None
        initial={'target_pots':target,'current_pot':0,'completed_pots':0,'total_products':0}
        try:
            self.path=self.root/('连续制作-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
            self.path.mkdir(parents=True,exist_ok=False)
            with self.lock:self.stream=(self.path/'events.jsonl').open('a',encoding='utf-8')
            device=self.device_factory(window)
            controls=self.controls_factory(window,window.size(),self,self.stop_event.is_set)
            reader=self.reader_factory(pid,self.profile,hwnd=window.hwnd)
            controls.start()
            atomic_write_json(self.path/'batch.json',{**initial,'state':'batch_wait','recipe_id':recipe['recipe_id']})
            self.update('batch_wait','连续制作已就绪：站在灶台旁，切回游戏按 F6；F9停止',batch=initial)
            while True:
                running,stopped=controls.snapshot()
                if stopped or self.stop_event.is_set():
                    raise ValueError('开始前已停止，未制作任何锅次')
                if running:break
                self.clock.sleep(.02)
            run_batch(target=target,recipe=recipe,memory=reader,window=window,controls=controls,
                device=device,clock=self.clock,report=self.update,directory=self.path,
                entry_runner=self.runner,selection_runner=self.selection_runner,pot_runner=self.single_pot_runner)
        except Exception as exc:
            # run_batch persists confirmed counters before propagating failure.
            if self.path and (self.path/'batch.json').exists():
                import json
                saved=json.loads((self.path/'batch.json').read_text(encoding='utf-8-sig'))
                saved.update(state='batch_stopped',message=str(exc))
                atomic_write_json(self.path/'batch.json',saved)
                self.update('batch_stopped','连续制作停止：'+str(exc),batch=saved)
            else:self.update('batch_stopped','连续制作停止：'+str(exc),batch=initial)
        finally:
            for obj,method in ((device,'release_all'),(controls,'close'),(reader,'close')):
                if obj is not None:
                    try:getattr(obj,method)()
                    except Exception:pass
            with self.lock:
                if self.stream:self.stream.close();self.stream=None
