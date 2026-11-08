"""P0-only entry acceptance. No live factories, screenshots or start-pot action.

The caller must provide a verified read-only menu-state reader. Cached recipe
details are never evidence that the menu is open. Not wired to the public UI.
"""
import json
import math
from copy import deepcopy
from pathlib import Path

from utils.core.cooking import bind_default_recipe, inventory_valid
from utils.drivers.coordinate_point_store import atomic_write_json


def run_stove_entry(*, window, controls, device, read_menu, read_recipe, inventory,
                    clock, report, input_gate, journal, binding, stove_ready=False,
                    reserve=0, max_star=5, timeout=5., validate_default=True):
    """Open once with top-row 1, validate default slots, then STOP before cooking.

    read_menu -> {state: 'ready', open: bool, scope: str}. Its producer must
    verify actual panel state/ownership from memory; no guessed field adapter
    is supplied here. binding identifies the caller's game/session, not a stove.
    """
    if not callable(read_menu):
        raise ValueError('食谱面板的只读状态入口尚未确认，禁止发送灶台输入')
    if not isinstance(binding, str) or not binding or type(stove_ready) is not bool:
        raise ValueError('灶台入口任务归属或人工就位确认无效')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('P0 等待预算无效')
    if type(reserve) is not int or reserve < 0 or type(max_star) is not int or not 0 <= max_star <= 5:
        raise ValueError('材料确认选项无效')
    path=Path(journal)
    record={'schema':'cooking-entry-p0-v1','binding':binding,'state':'IDLE',
            'entry_dispatched':False,'input_key':'1','input_vk':0x31,
            'started_pots':0,'manual_stove_ready':stove_ready}
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(saved,dict) or saved.get('schema')!=record['schema'] \
                or saved.get('binding')!=binding or type(saved.get('entry_dispatched')) is not bool:
            raise ValueError('灶台入口记录不完整或归属已变化，不能重复发送交互')
        record=saved

    def save(state, reason, **extra):
        record.update(state=state,reason=reason,**extra)
        atomic_write_json(path,record)
        report(state,reason,**{k:v for k,v in record.items() if k not in ('state','reason')})

    with input_gate.claim('烹饪入口 P0'):
        size=tuple(window.size())
        def guard():
            running,stopped=controls.snapshot()
            if stopped:
                save('STOPPED','F9 停止，保留入口待确认记录')
                return False
            if not running or not window.foreground() or tuple(window.size())!=size:
                controls.pause()
                save('PAUSED','输入已释放，恢复时重新读取；不重发待确认交互')
                return False
            return True
        def wait(duration):
            end=clock.monotonic()+duration
            while clock.monotonic()<end:
                if not guard():return False
                clock.sleep(min(.02,max(0.,end-clock.monotonic())))
            return guard()

        deadline=clock.monotonic()+timeout
        stable=None
        try:
            while clock.monotonic()<deadline:
                if not guard():return dict(record)
                try:
                    view=read_recipe()
                    menu=read_menu()
                    if not isinstance(view,dict) or not isinstance(view.get('scope'),str) or not view['scope']:
                        raise ValueError('默认食谱的场景归属未确认')
                    if not isinstance(menu,dict) or menu.get('state')!='ready' \
                            or type(menu.get('open')) is not bool:
                        raise ValueError('食谱面板打开状态未确认')
                    if menu.get('scope')!=view['scope'] or record.get('recipe_scope',view['scope'])!=view['scope']:
                        save('INVALID','面板或食谱场景归属已变化；未重发交互')
                        return dict(record)
                    record.setdefault('recipe_scope',view['scope'])
                    if not menu['open']:
                        stable=None
                        if not record['entry_dispatched']:
                            if not stove_ready:
                                save('STOVE_NOT_READY','请先手动站到灶台可交互位置')
                                return dict(record)
                            # Persist BEFORE input. A crash/exception cannot cause
                            # a second toggle on restart, even if dispatch is ambiguous.
                            record['entry_dispatched']=True
                            save('ENTRY_DISPATCHED','主键区数字 1 交互待确认',recipe_scope=view['scope'])
                            if not guard():return dict(record)
                            try:
                                device.execute({'type':'key_down','key':'1','vk':0x31,'extended':False})
                                if not wait(.04):return dict(record)
                            except (OSError,ValueError) as exc:
                                save('ERROR','入口按键派发未确认；不重发',
                                     last_action_error={'type':type(exc).__name__,'message':str(exc)})
                                return dict(record)
                            finally:
                                device.release_all()
                        if not wait(.1):return dict(record)
                        continue
                    if not validate_default:
                        evidence=(menu,view.get('selected'))
                        if stable==evidence:
                            save('MENU_OPEN','灶台食谱面板已确认；尚未选择料理或开始制作')
                            return dict(record)
                        stable=deepcopy(evidence)
                        if not wait(.1):return dict(record)
                        continue
                    bag=inventory()
                    if not inventory_valid(bag):
                        raise ValueError('当前背包归属或数量未确认')
                    if view.get('signature') is None:
                        raise ValueError('默认食谱签名未确认')
                    recipe=bind_default_recipe(view,bag,reserve,max_star)
                    usable=bag.get('selectable_counts',{})
                    if any(type(usable.get(i)) is not int or usable[i]-n<reserve
                           for i,n in recipe.ingredients.items()):
                        raise ValueError('实际可用材料不足或达到保留量')
                    evidence=(view,bag)
                    if stable==evidence:
                        save('RECIPE_VALIDATED','默认食谱与实际材料槽已确认；P0 到此停止，未开始烹饪',
                             recipe_scope=view['scope'],recipe_id=recipe.recipe_id,recipe_name=recipe.name,
                             material_slots=view['selected']['slots'],ingredients=recipe.ingredients,
                             inventory_scope=bag['scope'],inventory_before=bag['counts'])
                        return dict(record)
                    stable=deepcopy(evidence)
                except (OSError,ValueError) as exc:
                    stable=None
                    record['last_read_error']={'type':type(exc).__name__,'message':str(exc)}
                if not wait(.1):return dict(record)
            save('UNCONFIRMED','入口或默认材料尚未确认；不重复交互、不开始烹饪')
            return dict(record)
        finally:
            device.release_all()
