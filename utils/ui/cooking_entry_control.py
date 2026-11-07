"""Visible P0 task only. Reuses cooking lifecycle/gate; never starts a pot."""
from datetime import datetime
import json
import threading
from uuid import uuid4

from utils.drivers.cooking_menu_state import CookingMenuStateReader
from utils.drivers.input_task_gate import InputTaskGate
from utils.ui.cooking_control import CookingControl


class CookingEntryControl(CookingControl):
    def __init__(self,*args,menu_factory=CookingMenuStateReader,**kwargs):
        super().__init__(*args,**kwargs)
        self.menu_factory=menu_factory

    def start(self,pid):
        if self.active or self.busy():
            raise ValueError('已有回放或烹饪任务，请先停止')
        if type(pid) is not int or pid<=0:
            raise ValueError('没有可用的游戏进程，请重新连接')
        if self.runner is None:
            raise ValueError('P0 入口未装配，请打开新版程序')
        window=self.window_factory(pid)
        self.stop_event.clear();self.signature=None;self.pots={}
        self.thread=threading.Thread(target=self._run,args=(pid,window),
                                     name='cooking-entry-p0',daemon=True)
        self.thread.start()

    def entry_journal(self,binding):
        # Recover ambiguous input across application restarts, never bypass it
        # by silently allocating a new task journal.
        for path in sorted(self.root.glob('入口验证-*/entry.json'),reverse=True):
            saved=json.loads(path.read_text(encoding='utf-8-sig'))
            if saved.get('entry_dispatched') and saved.get('state')!='RECIPE_VALIDATED':
                if saved.get('binding')!=binding:
                    raise ValueError('存在其他游戏会话的未确认入口记录，请先核对灶台状态')
                return path
        return self.path/'entry.json'

    def _execute(self,pid,window):
        reader=device=controls=None
        result={}
        try:
            self.path=self.root/('入口验证-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
            self.path.mkdir(parents=True,exist_ok=False)
            with self.lock:
                self.stream=(self.path/'events.jsonl').open('a',encoding='utf-8')
            binding='p0-game-'+str(pid)
            journal=self.entry_journal(binding)
            device=self.device_factory(window)
            controls=self.controls_factory(window,window.size(),self,self.stop_event.is_set)
            reader=self.reader_factory(pid,self.profile,hwnd=window.hwnd)
            menu=self.menu_factory(reader)
            controls.start()
            self.update('cooking_entry_wait','P0 已就绪：切回游戏按 F6，只打开默认食谱，不开始制作；F9停止')
            while not self.stop_event.is_set():
                running,stopped=controls.snapshot()
                if stopped:
                    self.update('stopped','入口验证已停止，按键已释放')
                    break
                if not running:
                    self.clock.sleep(.02)
                    continue
                # CookingControl._run holds the shared application input gate
                # for this task, including F6 wait. Do not recursively claim it.
                result=self.runner(window=window,controls=controls,device=device,
                    read_menu=menu.snapshot,read_recipe=menu.recipe.snapshot,
                    inventory=lambda:reader.cooking_inventory_snapshot(5),clock=self.clock,
                    report=self.update,input_gate=InputTaskGate(),journal=journal,
                    binding=binding,stove_ready=True,timeout=8.)
                if result['state']!='PAUSED':
                    break
        except Exception as exc:
            self.update('error','灶台入口验证失败：'+type(exc).__name__+': '+str(exc))
        finally:
            for obj,method in ((device,'release_all'),(controls,'close'),(reader,'close')):
                if obj is not None:
                    try:getattr(obj,method)()
                    except Exception:pass
            with self.lock:
                if self.stream:
                    self.stream.close();self.stream=None
            if self.path:
                (self.path/'summary.json').write_text(json.dumps(
                    {'task_state':self.task_state,'started_pots':0,'entry':result},
                    ensure_ascii=False,indent=2),'utf-8')
