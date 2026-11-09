"""Exactly one native-state pot. No animation recognition or scheduled stirring."""
import math
from collections import Counter
from copy import deepcopy
from uuid import uuid4

from utils.core.cooking_single_pot import SinglePotEvents, material_plan, slots_match
from utils.core.scene_read import SceneReadIssue
from utils.core.map_targeting import MapLayoutUpdating
from utils.drivers.coordinate_point_store import atomic_write_json
from utils.drivers.cooking_single_pot import CookingObservationUpdating
from utils.drivers.cooking_mouse_diagnostics import begin_move, execute_move, finish_move
from utils.drivers.cooking_alt_interaction import cooking_alt, release_cooking_inputs
from utils.drivers.cooking_click_diagnostics import ClickDiagnostics, danger_timing, now
from utils.drivers.cooking_ui_click_ack import CookingUiClickAck


def collect_with_key(*, reader, state, guard, device, wait, reserve):
    """One main-row 1 input for the same owned Succeed phase; never retry."""
    guard()
    fresh=reader.state()
    if fresh!=state or fresh['status']!=5 or fresh['owner_net_id']!=fresh['local_net_id']:
        raise ValueError('收菜锅次或归属改变，未发送数字1')
    reserve()
    guard()
    try:
        device.execute({'type':'key_down','key':'1'})
        wait(.04)
    finally:
        # Explicit key-up before general cleanup, including F9/focus failures.
        try:device.execute({'type':'key_up','key':'1'})
        finally:release_cooking_inputs(device)


def run_single_pot(*, recipe, reader, window, controls, device, inventory, clock, report, journal):
    size=tuple(window.size()); events=SinglePotEvents(recipe)
    record={'schema':'cooking-single-pot-v1','pot_id':uuid4().hex,'recipe_id':recipe['recipe_id'],
            'product_id':recipe['product_id'],'recipe_name':recipe['name'],'state':'checking',
            'started_pots':0,'confirmed_products':0}
    task_deadline=clock.monotonic()+600.; stock_scope=None;alt_interaction_active=False;danger_times={}
    def save(state,message,**extra):
        record.update(state=state,message=message,**events.record(),**extra)
        atomic_write_json(journal,record)
        report(state,message,**{k:v for k,v in record.items() if k not in ('state','message')})
    def guard():
        running,stopped=controls.snapshot()
        if stopped:raise ValueError('F9 已停止，本锅未确认结果保留，未重启制作')
        if not running or not window.foreground() or tuple(window.size())!=size:
            controls.pause()
            raise ValueError('单锅任务暂停或失焦，已释放输入；未自动重启或忽略可能遗漏的翻锅事件')
        if clock.monotonic()>=task_deadline:raise ValueError('单锅任务等待超时，未重启制作')
    def wait(seconds):
        end=clock.monotonic()+seconds
        while clock.monotonic()<end:
            guard()
            remaining=end-clock.monotonic()
            if remaining<=0:
                break
            clock.sleep(min(.02,remaining))
        guard()
    def read(source):
        until=clock.monotonic()+1.
        while True:
            guard()
            try:return source()
            except (SceneReadIssue,MapLayoutUpdating,CookingObservationUpdating,OSError) as exc:
                release_cooking_inputs(device)
                if alt_interaction_active:raise
                if isinstance(exc,SceneReadIssue) and exc.kind not in ('invalid_reference','mutation'):
                    raise
                if clock.monotonic()>=until:raise
                wait(.02)
    def stock():
        nonlocal stock_scope
        value=read(inventory)
        if stock_scope is None:stock_scope=value['scope']
        if value['scope']!=stock_scope:raise ValueError('本锅背包归属改变')
        return value
    def click(read_target, before_input=None, *, hold_alt=False):
        nonlocal alt_interaction_active
        if not hold_alt:
            return click_body(read_target,before_input)
        initial=read(read_target)
        try:
            with cooking_alt(device,window,guard,wait,report):
                alt_interaction_active=True
                return click_body(read_target,before_input,initial)
        finally:alt_interaction_active=False
    def click_body(read_target, before_input=None, expected=None):
        def same_target(a,b):
            return ({k:v for k,v in a.items() if k!='rect'}=={k:v for k,v in b.items() if k!='rect'}
                    and len(a['rect'])==len(b['rect'])==4
                    and max(abs(x-y) for x,y in zip(a['rect'],b['rect']))<=1.5)
        target=read(read_target);wait(.04);fresh=read(read_target)
        if expected is not None and {k:v for k,v in target.items() if k!='rect'}!={k:v for k,v in expected.items() if k!='rect'}:
            raise ValueError('按下 ALT 后翻锅事件或按钮身份变化，未移动或点击')
        if not same_target(fresh,target):raise ValueError('烹饪交互目标在点击前变化，未发送点击')
        target=fresh
        rect=target['rect'];l,t,r,b=rect
        if not 0<=l<r<=size[0] or not 0<=t<b<=size[1]:
            raise ValueError('烹饪交互控件不在可见客户区内')
        point=(round((l+r)/2),round((t+b)/2))
        guard();mouse_diagnostic=begin_move(window,point)
        try:execute_move(device,{'type':'cursor_move','x':point[0],'y':point[1]},mouse_diagnostic)
        except Exception:
            report('mouse_diagnostic','鼠标移动调用异常，未派发本次点击',mouse_diagnostic=mouse_diagnostic)
            raise
        wait(.02);pointer=window.pointer()
        mouse_diagnostic=finish_move(window,point,pointer,mouse_diagnostic,size)
        report('mouse_diagnostic','烹饪鼠标到位诊断',mouse_diagnostic=mouse_diagnostic)
        if len(pointer)!=3 or not pointer[2] or math.dist(pointer[:2],point)>2:
            raise ValueError('鼠标未到达实际烹饪控件，未点击')
        if not same_target(read(read_target),target):raise ValueError('烹饪控件绑定在派发前改变')
        guard()
        if before_input:before_input()
        guard()
        if expected is not None:
            ui_ack=CookingUiClickAck(reader,target)
            diagnostic=ClickDiagnostics(device,window,danger_times.get(target.get('event_id')))
            diagnostic.data['ui_click_before']=ui_ack.before
            try:
                with diagnostic:
                    try:
                        device.execute({'type':'mouse_down','button':'left'});wait(.04)
                        device.execute({'type':'mouse_up','button':'left'})
                        diagnostic.data['mouse_up_returned_at']=now()
                        ack_until=clock.monotonic()+1.
                        while True:
                            guard();sample=ui_ack.observe()
                            diagnostic.data['ui_click_after']=sample
                            if sample['last_click_time']>ui_ack.before['last_click_time']:
                                diagnostic.data['ui_first_update_observed_at']=sample['observed_at']
                                diagnostic.data['ui_click_confirmed']=True
                                break
                            if sample['last_click_time']<ui_ack.before['last_click_time']:
                                raise ValueError('原按钮点击时钟回退，未重试点击')
                            if clock.monotonic()>=ack_until:
                                diagnostic.data['ui_click_confirmed']=False
                                raise ValueError('左键已释放但 UI 在限定时间内未确认点击，已停止且不重复点击')
                            wait(.01)
                    finally:release_cooking_inputs(device)
            finally:report('stir_left_diagnostic','翻锅左键原始派发诊断；游戏确认另行核对',left_diagnostic=diagnostic.data)
        else:
            try:
                device.execute({'type':'mouse_down','button':'left'});wait(.04)
            finally:release_cooking_inputs(device)
    def await_slots(predicate,seconds=2.):
        end=clock.monotonic()+seconds
        while clock.monotonic()<end:
            view=read(reader.materials)
            if predicate(view):return view
            wait(.05)
        raise ValueError('材料选择输入后没有读回确认，未重复点击或开始制作')
    try:
        if recipe.get('status')!='confirmed' or not recipe.get('product_id'):
            raise ValueError('指定料理或产物编号尚未确认')
        view=read(reader.materials);reader.bind(view['stove_identity'])
        initial=read(reader.state);events.observe(initial)
        save('preparing_materials','已核对实际料理，正在逐槽核对用料',stove_identity=initial['stove_identity'])
        # Native AutoFill already runs on recipe selection. Keep every valid
        # stack; fill/change only slots whose actual stack is not in the plan.
        for _ in range(5):
            view=read(reader.materials);bag=stock();plan=material_plan(recipe,view,bag)
            if slots_match(view['slots'],plan):break
            used=Counter(s['net_id'] for s in view['slots'] if s['filled'])
            counts={r['net_id']:r['count'] for r in bag['items']}
            changes=[i for i,(s,p) in enumerate(zip(view['slots'],plan))
                     if not s['filled'] or (s['net_id'],s['item_id'])!=(p['net_id'],p['item_id'])]
            fillable=[i for i in changes if counts.get(plan[i]['net_id'],0)>used[plan[i]['net_id']]]
            if not fillable:raise ValueError('材料分堆仍被其他槽位占用，未重复分配或开始制作')
            i=fillable[0]
            if view['picker_open'] and view['selected_slot']!=i:
                click(lambda:reader.menu_target(size,'close_picker'))
                await_slots(lambda v:not v['picker_open'])
            if not view['picker_open'] or view['selected_slot']!=i:
                click(lambda:reader.menu_target(size,'slot',index=i))
                await_slots(lambda v:v['picker_open'] and v['selected_slot']==i)
            item=plan[i]
            for _ in range(10):
                target=read(lambda:reader.menu_target(size,'material',index=i,net_id=item['net_id']))
                if target['visible']:break
                if not target['can_scroll']:raise ValueError('材料分堆不在可见列表且滚动入口未确认')
                l,t,r,b=target['viewport'];x,y=round((l+r)/2),round((t+b)/2)
                if not 0<x<size[0] or not 0<y<size[1]:raise ValueError('材料滚动区域不在游戏客户区内')
                guard();device.execute({'type':'cursor_move','x':x,'y':y});wait(.02)
                pointer=window.pointer()
                if not pointer[2] or math.dist(pointer[:2],(x,y))>2:raise ValueError('鼠标未到材料列表，未滚动')
                guard();device.execute({'type':'scroll','dx':0,'dy':2 if target['rect'][1]<t else -2})
                wait(.2)
            else:raise ValueError('材料列表滚动后仍未找到可见的指定分堆')
            click(lambda:reader.menu_target(size,'material',index=i,net_id=item['net_id']))
            await_slots(lambda v:v['slots'][i]['filled'] and
                        (v['slots'][i]['net_id'],v['slots'][i]['item_id'])==(item['net_id'],item['item_id']))
        else:raise ValueError('全部材料槽未确认，未开始制作')
        view=read(reader.materials)
        if view['picker_open']:
            click(lambda:reader.menu_target(size,'close_picker'))
            view=await_slots(lambda v:not v['picker_open'])
        before=stock();plan=material_plan(recipe,view,before)
        if not slots_match(view['slots'],plan):raise ValueError('开始前材料槽变化，未投料')
        wait(.1)
        if read(reader.materials)!=view:raise ValueError('材料槽尚未稳定，未投料')
        def reserve_start():
            current=read(reader.state);events.observe(current)
            latest=stock();now_view=read(reader.materials)
            if now_view!=view or not slots_match(now_view['slots'],material_plan(recipe,now_view,latest)):
                raise ValueError('派发开始前材料或料理变化，未投料')
            events.reserve('start')
            save('start_reserved','启动单锅操作已登记，等待游戏确认，不重发',
                 inventory_scope=latest['scope'],inventory_before=deepcopy(latest['counts']),material_slots=now_view['slots'])
        click(lambda:reader.menu_target(size,'start'),reserve_start)
        start_at=clock.monotonic();collect_at=None;last_signature=None;observed_phase=None
        while True:
            state=read(reader.state)
            if state['status']==3 and str(state['status_started']) not in danger_times:
                danger_times[str(state['status_started'])]=danger_timing(state)
            if (state['status'],state['status_started'])!=observed_phase:
                report('native_observation','烹饪原始状态观测',native_state=state)
                observed_phase=(state['status'],state['status_started'])
            action=events.observe(state)
            if events.cooking_seen:
                record['started_pots']=1
            signature=(state['status'],state['status_started'],events.pending)
            if signature!=last_signature:
                save('cooking','读取实际烹饪状态：'+str(state['status']),native_state=state)
                last_signature=signature
            if not events.cooking_seen and clock.monotonic()-start_at>8.:
                raise ValueError('开始输入未取得实际开锅确认，不重复启动')
            if action:
                kind,key=action
                def reserve_action():
                    fresh=read(reader.state)
                    if (fresh['status'],fresh['status_started'])!=(state['status'],state['status_started']):
                        raise ValueError('翻锅或收菜事件在派发前改变，未误点')
                    events.reserve(kind,key)
                    save(kind+'_reserved','翻锅事件已登记，等待解除确认' if kind=='stir' else '游戏已成功完成，收菜操作已登记',
                         native_state=fresh,hud_diagnostics=getattr(reader,'hud_diagnostics',None))
                if kind=='collect':
                    collect_with_key(reader=reader,state=state,guard=guard,device=device,
                                     wait=wait,reserve=reserve_action)
                    report('collect_key_dispatched','主键区数字1已派发一次，等待灶台复位及背包增量',
                           native_state=state,key='1',repeat=False)
                else:
                    click(lambda:reader.hud_target(size,kind,state),reserve_action,hold_alt=kind=='stir')
                if kind=='collect':collect_at=clock.monotonic()
            if collect_at is not None:
                if state['status']==0:
                    after=stock();delta=after['counts'].get(recipe['product_id'],0)-record['inventory_before'].get(recipe['product_id'],0)
                    if delta>0:
                        save('finished','单锅已收取，实际背包产物增加 '+str(delta),confirmed_products=delta,
                             inventory_after=after['counts'],product_delta=delta)
                        return deepcopy(record)
                if clock.monotonic()-collect_at>15.:
                    raise ValueError('收菜后未同时取得灶台复位和指定产物增量，未判成功或重复收菜')
            wait(.03)
    except Exception as exc:
        save('unconfirmed','单锅停止：'+type(exc).__name__+': '+str(exc),
             hud_diagnostics=getattr(exc,'diagnostics',None) or getattr(reader,'hud_diagnostics',None))
        raise
    finally:
        release_cooking_inputs(device)
