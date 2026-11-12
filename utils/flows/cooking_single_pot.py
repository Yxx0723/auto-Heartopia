"""Exactly one native-state pot. No animation recognition or scheduled stirring."""
import math
import json
from contextlib import nullcontext
from collections import Counter
from copy import deepcopy
from uuid import uuid4

from utils.core.cooking_single_pot import SinglePotEvents, material_plan, slots_match
from utils.core.scene_read import SceneReadIssue
from utils.core.map_targeting import MapLayoutUpdating
from utils.drivers.coordinate_point_store import atomic_write_json
from utils.drivers.cooking_single_pot import CookingObservationUpdating
from utils.drivers.cooking_mouse_diagnostics import begin_move, execute_move, finish_move, attempt
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


def read_variant_slot_target(source, *, window, size, guard, wait, report, context):
    """Require consecutive observations of one logical slot before moving."""
    previous = None
    sequence = 0

    def sample(stage, reference=None, pointer=None):
        nonlocal sequence
        guard()
        sequence += 1
        diagnostic = dict(context, read_sequence=sequence, stage=stage, captured_at=now(),
                          object_address=None, binding_identity=None, local_rect=None,
                          screen_rect=None, canvas_scale=None, window_rect=None,
                          binding_stable=None, rect_stable=None, stability_failure_reason=None)
        try:
            window_rect = tuple(window.get_rect())
            diagnostic['window_rect'] = window_rect
            current = source(diagnostic)
            if tuple(window.get_rect()) != window_rect:
                raise ValueError('读取变种材料槽期间游戏窗口移动')
            l,t,r,b = current['rect']
            x,y,right,bottom = window_rect
            diagnostic['screen_rect'] = (x+l,y+t,x+r,y+b)
            if (right-x,bottom-y) != size or not (0<=l<r<=size[0] and 0<=t<b<=size[1]):
                raise ValueError('变种材料槽不在有效游戏客户区内')
            binding = {key: value for key,value in current.items() if key != 'rect'}
            diagnostic['binding_identity'] = binding
            logical = {key: value for key,value in binding.items()
                       if key not in ('widget','component_binding')}
            if (current['recipe_id'], current['index'], current['slot'], current['stove_identity']) != (
                    context['base_recipe_id'], context['slot_index'], context['expected_slot'],
                    context['expected_stove']):
                raise ValueError('变种材料槽食谱、索引或已选材料改变')
            if reference is not None:
                old, old_binding, old_logical, old_window = reference
                changed = sorted(key for key in binding.keys() | old_binding.keys()
                                 if binding.get(key) != old_binding.get(key))
                delta = tuple(abs(a-b) for a,b in zip(current['rect'],old['rect']))
                diagnostic.update(binding_changed_fields=changed, rect_edge_delta=delta,
                                  binding_stable=not changed, rect_stable=max(delta)<=1.5)
                reasons = []
                if changed:reasons.append('binding_changed')
                if max(delta)>1.5:reasons.append('rect_changed')
                if window_rect!=old_window:reasons.append('window_rect_changed')
                diagnostic['stability_failure_reason'] = reasons or None
                if logical != old_logical:
                    raise ValueError('变种材料槽逻辑归属改变')
                if pointer is not None and (reasons or not (l<=pointer[0]<r and t<=pointer[1]<b)):
                    if not reasons:diagnostic['stability_failure_reason'] = ['pointer_outside']
                    raise ValueError('变种材料槽派发前绑定、坐标或鼠标命中未确认')
            return current,binding,logical,window_rect
        except Exception as exc:
            diagnostic['read_error'] = {'type':type(exc).__name__,'message':str(exc)}
            if not diagnostic['stability_failure_reason']:
                diagnostic['stability_failure_reason'] = ['read_or_validation_failed']
            raise
        finally:
            diagnostic['completed_at'] = now()
            report('variant_slot_observation','变种材料槽点击前读取',slot_observation=diagnostic)

    for delay in (0,.04,.02,.02):
        if delay:wait(delay)
        current = sample('before_move',previous)
        if previous is not None and current[1] == previous[1] and current[3] == previous[3] and (
                max(abs(a-b) for a,b in zip(current[0]['rect'],previous[0]['rect']))<=1.5):
            return current[0],lambda pointer: sample('before_dispatch',current,pointer)
        previous = current
    raise ValueError('变种材料槽稳定性读取次数耗尽，未发送点击')


def wait_for_variant_start(read_target, *, clock, guard, wait, report, window, recipe_snapshot,
                           same_target):
    deadline=clock.monotonic()+.5
    samples=[]
    owner=None
    previous=None

    def capture(observation):
        nonlocal owner
        guard()
        bounds=tuple(window.get_rect())
        observation['window_rect']=bounds
        target=read_target(observation)
        if tuple(window.get_rect())!=bounds:
            raise ValueError('开始按钮读取期间窗口移动')
        identity=tuple(observation[key] for key in
                       ('scene_scope','ui_manager','panel_address','stove_identity','base_recipe_id'))
        if owner is None:owner=identity
        if identity!=owner or target['stove_identity']!=recipe_snapshot['stove_identity'] \
                or identity[-1]!=recipe_snapshot['base_recipe_id']:
            raise ValueError('开始按钮的场景、面板、灶台或食谱身份变化')
        if observation['picker_state_verified'] is not True:
            raise ValueError('材料选择器关闭状态未知，未点击开始')
        enabled=observation['button_enabled']['state']
        if enabled=='disabled':
            raise ValueError('开始按钮已禁用，未发送点击')
        if enabled not in ('enabled','unknown'):
            raise ValueError('开始按钮启用状态记录无效，未发送点击')
        # Keep the original read error in the observation. The owned UI and
        # interaction checks below still govern a click when enabled is unknown.
        if not observation['button_binding_valid'] or observation['button_panel']!=identity[2] \
                or observation['button_bar_active'] is not True or observation['button_active'] is False \
                or observation['m_Interactable'] is not True \
                or observation['m_GroupsAllowInteraction'] is not True:
            raise ValueError('开始按钮归属、显示或交互状态未确认')
        button=tuple(observation[key] for key in
                     ('button_address','button_wrapper','button_native','target_graphic','rect_transform'))
        l,t,r,b=target['rect']
        x,y,_,_=bounds
        observation['screen_rect']=(x+l,y+t,x+r,y+b)
        return target,button,bounds

    error=None
    try:
        while clock.monotonic()<deadline:
            observation={'captured_at':now()}
            samples.append(observation)
            target,button,bounds=capture(observation)
            if clock.monotonic()>=deadline:break
            current=(target,button,bounds)
            if observation['picker_closed'] is True:
                if previous is not None and current[1:]==previous[1:] and same_target(target,previous[0]):
                    def verified_target(observation=None):
                        latest={} if observation is None else observation
                        fresh,fresh_button,fresh_bounds=capture(latest)
                        if latest['picker_closed'] is not True or (fresh_button,fresh_bounds)!=(button,bounds) \
                                or not same_target(fresh,target):
                            raise ValueError('开始按钮稳定后复核失败，未发送点击')
                        return fresh
                    return verified_target
                previous=current
            else:
                previous=None
            remaining=deadline-clock.monotonic()
            if remaining>0:wait(min(.02,remaining))
        raise ValueError('开始按钮布局在 500ms 内未稳定，未发送点击')
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
        raise
    finally:
        report('variant_start_stability_wait','变种料理等待选择器关闭与开始按钮稳定',
               samples=samples, deadline_ms=500, failure=error)


def run_single_pot(*, recipe, reader, window, controls, device, inventory, clock, report, journal):
    size=tuple(window.size()); events=SinglePotEvents(recipe)
    record={'schema':'cooking-single-pot-v1','pot_id':uuid4().hex,'recipe_id':recipe['recipe_id'],
            'product_id':recipe['product_id'],'recipe_name':recipe['name'],'state':'checking',
            'started_pots':0,'confirmed_products':0}
    task_deadline=clock.monotonic()+600.; stock_scope=None;alt_interaction_active=False;danger_times={}
    start_ui_before=None;start_reserved_at=None;pending_ack=None;ack_succeeded=False
    def observe_start(stage):
        nonlocal start_ui_before
        try:
            value=reader.start_diagnostics(size)
            json.dumps(value,allow_nan=False)
        except Exception as exc:
            value={'state':'unavailable','reason':type(exc).__name__+': '+str(exc)}
        if stage=='before_click':start_ui_before=value
        baseline=start_ui_before or {}
        same_button=(value.get('button')==baseline.get('button') and value.get('button') is not None
            and value.get('button_binding_stable') is True
            and isinstance(value.get('callback'),dict) and 'delegate' in value['callback']
            and value.get('callback')==baseline.get('callback'))
        old=baseline.get('last_click_time');new=value.get('last_click_time')
        value['button_handler_timestamp_updated']=(new>old if same_button and
            type(old) in (int,float) and type(new) in (int,float) else {'state':'unavailable'})
        value['timestamp_evidence_scope']='CustomButton handler timestamp only; not proof of CookPanel callback or start acceptance'
        report('start_ui_diagnostic','开始按钮只读诊断',stage=stage,start_diagnostic=value)
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
    def click(read_target, before_input=None, *, hold_alt=False, variant_slot=None, start_trace=None):
        nonlocal alt_interaction_active,pending_ack,ack_succeeded
        if not hold_alt:
            if start_trace is not None:
                return click_body(read_target,before_input,start_trace=start_trace)
            return click_body(read_target,before_input,variant_slot=variant_slot)
        pending_ack=None;ack_succeeded=False
        initial=read(read_target)
        try:
            with cooking_alt(device,window,guard,wait,report):
                alt_interaction_active=True
                return click_body(read_target,before_input,initial)
        finally:
            alt_interaction_active=False
            if pending_ack is not None:
                try:
                    held_buttons=getattr(device,'buttons',None)
                    held_keys=getattr(device,'keys',None)
                    inputs_released=(isinstance(held_buttons,(set,frozenset)) and
                        isinstance(held_keys,(set,frozenset)) and not held_buttons and not held_keys)
                    report('ack_post_diagnostic','UI 确认事后诊断；原输入清理已执行',
                        ack_diagnostic=pending_ack.finalize(failed=not ack_succeeded and inputs_released))
                except Exception:pass
    def click_body(read_target, before_input=None, expected=None, *, variant_slot=None, start_trace=None):
        nonlocal pending_ack,ack_succeeded
        def target_binding(value):
            return {key: item for key, item in value.items() if key != 'rect'}
        def same_target(a,b):
            return (target_binding(a)==target_binding(b)
                    and len(a['rect'])==len(b['rect'])==4
                    and max(abs(x-y) for x,y in zip(a['rect'],b['rect']))<=1.5)
        if start_trace is not None:
            read_target=wait_for_variant_start(read_target,clock=clock,guard=guard,wait=wait,
                report=report,window=window,recipe_snapshot=start_trace,same_target=same_target)
            target=observe_start_comparison(read_target,same_target,target_binding,start_trace)
        elif variant_slot is None:
            target=read(read_target);wait(.04);fresh=read(read_target)
            if expected is not None and target_binding(target)!=target_binding(expected):
                raise ValueError('按下 ALT 后翻锅事件或按钮身份变化，未移动或点击')
            if not same_target(fresh,target):raise ValueError('烹饪交互目标在点击前变化，未发送点击')
            target=fresh
        else:
            target,verify_slot=read_variant_slot_target(read_target,window=window,size=size,
                guard=guard,wait=wait,report=report,context=variant_slot)
        is_start=target.get('kind')=='start'
        if is_start:observe_start('before_click')
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
        if variant_slot is not None:
            verify_slot(pointer)
            if window.pointer()!=pointer:
                raise ValueError('变种材料槽派发前鼠标位置变化，未发送点击')
        elif not same_target(read_target() if start_trace is not None else read(read_target),target):
            raise ValueError('烹饪控件绑定在派发前改变')
        guard()
        if before_input:before_input()
        guard()
        if expected is not None:
            ui_ack=CookingUiClickAck(reader,target)
            diagnostic=ClickDiagnostics(device,window,danger_times.get(target.get('event_id')))
            diagnostic.data['ui_click_before']=ui_ack.before
            pending_ack=ui_ack;ack_succeeded=False
            try:
                with diagnostic:
                    try:
                        device.execute({'type':'mouse_down','button':'left'});wait(.04)
                        device.execute({'type':'mouse_up','button':'left'})
                        diagnostic.data['mouse_up_returned_at']=now()
                        ack_until=clock.monotonic()+1.
                        ui_ack.begin_confirmation(ack_until,clock.monotonic,guard)
                        while True:
                            guard();sample=ui_ack.observe()
                            diagnostic.data['ui_click_after']=sample
                            if clock.monotonic()>=ack_until:
                                diagnostic.data['ui_click_confirmed']=False
                                raise ValueError('左键已释放但 UI 在限定时间内未确认点击，已停止且不重复点击')
                            if sample['last_click_time']>ui_ack.before['last_click_time']:
                                diagnostic.data['ui_first_update_observed_at']=sample['observed_at']
                                diagnostic.data['ui_click_confirmed']=True
                                ack_succeeded=True
                                break
                            if sample['last_click_time']<ui_ack.before['last_click_time']:
                                raise ValueError('原按钮点击时钟回退，未重试点击')
                            wait(.01)
                    finally:release_cooking_inputs(device)
            finally:report('stir_left_diagnostic','翻锅左键原始派发诊断；游戏确认另行核对',left_diagnostic=diagnostic.data)
        else:
            diagnostic=ClickDiagnostics(device,window) if is_start else None
            try:
                with diagnostic if diagnostic is not None else nullcontext():
                    try:
                        if diagnostic is not None:
                            import win32gui
                            diagnostic.data['foreground_before_down']=attempt(win32gui.GetForegroundWindow)
                        device.execute({'type':'mouse_down','button':'left'});wait(.04)
                    finally:
                        try:release_cooking_inputs(device)
                        finally:
                            if diagnostic is not None:
                                diagnostic.data['foreground_after_up']=attempt(win32gui.GetForegroundWindow)
            finally:
                if diagnostic is not None:
                    report('start_left_diagnostic','开始左键派发诊断；不代表 UI 接受或实际开锅',
                           start_reserved_at=start_reserved_at,left_diagnostic=diagnostic.data)
    def await_slots(predicate,seconds=2.):
        end=clock.monotonic()+seconds
        while clock.monotonic()<end:
            view=read(reader.materials)
            if predicate(view):return view
            wait(.05)
        raise ValueError('材料选择输入后没有读回确认，未重复点击或开始制作')

    def observe_start_comparison(read_target, same_target, target_binding, recipe_snapshot):
        diagnostic={'first_snapshot':None, 'second_snapshot':None, 'changed_fields':None,
                    'changed_target_fields':None, 'rect_edge_delta':None,
                    'comparison_passed':None, 'failure_reason':None}

        def capture(phase):
            snapshot={'recipe':deepcopy(recipe_snapshot), 'captured_at':now()}
            diagnostic[phase]=snapshot
            errors=[]
            def sample():
                observation={}
                snapshot['observation']=observation
                try:
                    window_rect=tuple(window.get_rect())
                    observation.update(window_rect=window_rect, window_size=size)
                    target=read_target(observation)
                    observation['click_target']=deepcopy(target)
                    l,t,r,b=target['rect']
                    x,y,_,_=window_rect
                    observation.update(screen_rect=(x+l,y+t,x+r,y+b),
                                       mouse_target=(round((l+r)/2),round((t+b)/2)),
                                       window_rect_after=tuple(window.get_rect()))
                    return target
                except Exception as exc:
                    errors.append({'phase':phase, 'type':type(exc).__name__, 'message':str(exc),
                                   'observation':observation})
                    raise
            try:
                guard()
                return sample()
            finally:
                snapshot.update(read_errors=errors, completed_at=now())

        stage='first_snapshot'
        try:
            first=capture(stage)
            stage='between_reads'
            wait(.04)
            stage='second_snapshot'
            second=capture(stage)
            stage='comparison'
            before,after=target_binding(first),target_binding(second)
            diagnostic['changed_target_fields']=sorted(
                key for key in before.keys() | after.keys()
                if (key in before)!=(key in after) or before.get(key)!=after.get(key))
            if len(first['rect'])==len(second['rect'])==4:
                diagnostic['rect_edge_delta']=tuple(abs(a-b) for a,b in zip(first['rect'],second['rect']))
            diagnostic['comparison_passed']=same_target(second,first)
            if not diagnostic['comparison_passed']:
                diagnostic['failure_reason']='target_comparison_failed'
                raise ValueError('烹饪交互目标在点击前变化，未发送点击')
            return second
        except Exception as exc:
            diagnostic['failure_reason']=diagnostic['failure_reason'] or stage
            diagnostic['error']={'stage':stage, 'type':type(exc).__name__, 'message':str(exc)}
            raise
        finally:
            left=(diagnostic['first_snapshot'] or {}).get('observation')
            right=(diagnostic['second_snapshot'] or {}).get('observation')
            if left is not None and right is not None:
                # On a failed read, omitted fields are unavailable, not changed.
                keys=left.keys() & right.keys() if diagnostic['comparison_passed'] is None \
                    else left.keys() | right.keys()
                diagnostic['changed_fields']=sorted(
                    key for key in keys if key!='click_target'
                    and ((key in left)!=(key in right) or left.get(key)!=right.get(key)))
            report('variant_start_comparison','变种料理开始按钮点击前比较',start_comparison=diagnostic)
    try:
        if recipe.get('status')!='confirmed' or not recipe.get('product_id'):
            raise ValueError('指定料理或产物编号尚未确认')
        view=read(reader.materials);reader.bind(view['stove_identity'])
        initial=read(reader.state);events.observe(initial)
        attempts=getattr(reader,'binding_metrics',None)
        binding_timing={'binding_attempts':attempts} if isinstance(attempts,list) else {}
        save('preparing_materials','已核对实际料理，正在逐槽核对用料',
             stove_identity=initial['stove_identity'],**binding_timing)
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
            item=plan[i]
            if view['picker_open'] and view['selected_slot']!=i:
                click(lambda:reader.menu_target(size,'close_picker'))
                await_slots(lambda v:not v['picker_open'])
            if not view['picker_open'] or view['selected_slot']!=i:
                if 'base_recipe_id' in recipe:
                    click(lambda observation: reader.menu_target(size,'slot',index=i,slot_observation=observation),
                          variant_slot=dict(slot_index=i,slot_identity=(recipe['base_recipe_id'],i),
                              base_recipe_id=recipe['base_recipe_id'],target_recipe_id=recipe['recipe_id'],
                              material_id=item['item_id'],expected_slot=view['slots'][i],
                              expected_stove=initial['stove_identity'],
                              remaining_slots=changes))
                else:
                    click(lambda:reader.menu_target(size,'slot',index=i))
                await_slots(lambda v:v['picker_open'] and v['selected_slot']==i)
            def material_target(observation=None):
                kwargs = {} if observation is None else {'material_observation':observation}
                target = reader.menu_target(size, 'material', index=i, net_id=item['net_id'], **kwargs)
                if 'base_recipe_id' in recipe and target['item_id'] != item['item_id']:
                    raise ValueError('变种食材编号与指定用料不符，未点击')
                return target
            for _ in range(10):
                target=read(material_target)
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
            if 'base_recipe_id' in recipe:
                previous_candidate = None
                candidate_sequence = 0

                def observed_material_target():
                    nonlocal previous_candidate,candidate_sequence
                    candidate_sequence += 1
                    observation = {}
                    entry = dict(slot_index=i, target_recipe_id=recipe['recipe_id'],
                                 expected_item_id=item['item_id'], expected_net_id=item['net_id'],
                                 sequence=candidate_sequence, captured_at=now(), candidate=observation)
                    try:
                        observation['window_rect_before'] = tuple(window.get_rect())
                        candidate = material_target(observation)
                        observation['click_target'] = candidate
                        observation['window_rect_after'] = tuple(window.get_rect())
                        l,t,r,b = candidate['rect']
                        x,y,_,_ = observation['window_rect_before']
                        observation['screen_rect'] = (x+l,y+t,x+r,y+b)
                        if previous_candidate is not None:
                            prior, prior_target = previous_candidate
                            entry['changed_fields'] = sorted(
                                key for key in observation.keys() | prior.keys()
                                if observation.get(key)!=prior.get(key) and key!='click_target')
                            entry['changed_target_fields'] = sorted(
                                key for key in candidate.keys() | prior_target.keys()
                                if candidate.get(key)!=prior_target.get(key) and key!='rect')
                            entry['rect_edge_delta'] = tuple(
                                abs(a-b) for a,b in zip(candidate['rect'],prior_target['rect']))
                            entry['rect_stable'] = max(entry['rect_edge_delta'])<=1.5
                        previous_candidate = observation,candidate
                        return candidate
                    except Exception as exc:
                        entry['read_error'] = {'type':type(exc).__name__,'message':str(exc)}
                        raise
                    finally:
                        entry['completed_at'] = now()
                        report('variant_material_candidate',f'变种材料候选槽 {i} 第 {candidate_sequence} 次读取',
                               material_candidate=entry)

                click(observed_material_target)
            else:
                click(material_target)
            verified=await_slots(lambda v:v['slots'][i]['filled'] and
                        (v['slots'][i]['net_id'],v['slots'][i]['item_id'])==(item['net_id'],item['item_id']))
            if 'base_recipe_id' in recipe:
                report('variant_slot_replaced','变种材料槽替换已读回确认',slot_index=i,
                       base_recipe_id=recipe['base_recipe_id'],target_recipe_id=recipe['recipe_id'],
                       material_id=item['item_id'],selected_slot=verified['slots'][i],
                       remaining_slots=[index for index in changes if index!=i])
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
            nonlocal start_reserved_at
            current=read(reader.state);events.observe(current)
            latest=stock();now_view=read(reader.materials)
            if now_view!=view or not slots_match(now_view['slots'],material_plan(recipe,now_view,latest)):
                raise ValueError('派发开始前材料或料理变化，未投料')
            events.reserve('start')
            start_reserved_at=now()
            save('start_reserved','启动单锅操作已登记，等待游戏确认，不重发',
                 inventory_scope=latest['scope'],inventory_before=deepcopy(latest['counts']),material_slots=now_view['slots'],
                 start_reserved_at=start_reserved_at)
        if recipe['recipe_id']==45160 and recipe.get('base_recipe_id')==45153:
            def start_target(observation=None):
                if observation is None:
                    return reader.menu_target(size,'start')
                return reader.menu_target(size,'start',start_observation=observation)
            click(start_target,reserve_start,start_trace={
                'base_recipe_id':view['selected']['recipe_id'], 'target_recipe_id':recipe['recipe_id'],
                'stove_identity':view['stove_identity'],
                'material_slots':view['slots'], 'recipe_confirmed':True,
                'material_snapshot_source':'pre_start_recipe_and_slot_stability_check'})
        else:
            click(lambda:reader.menu_target(size,'start'),reserve_start)
        start_at=clock.monotonic();collect_at=None;last_signature=None;observed_phase=None
        next_start_observation=start_at
        start_transition_observed=False
        while True:
            state=read(reader.state)
            if state['status']==3 and str(state['status_started']) not in danger_times:
                danger_times[str(state['status_started'])]=danger_timing(state)
            if (state['status'],state['status_started'])!=observed_phase:
                report('native_observation','烹饪原始状态观测',native_state=state)
                observed_phase=(state['status'],state['status_started'])
            action=events.observe(state)
            if not events.cooking_seen and clock.monotonic()>=next_start_observation:
                observe_start('waiting_start')
                next_start_observation=clock.monotonic()+.5
            elif events.cooking_seen and not start_transition_observed:
                observe_start('start_native_confirmed')
                start_transition_observed=True
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
