"""Finite P3 scheduling; P2 alone owns each pot's inputs and completion."""
from copy import deepcopy
import traceback

from utils.core.cooking_catalog import material_status
from utils.drivers.coordinate_point_store import atomic_write_json
from utils.drivers.cooking_first_recipe_notice import observe_first_recipe_notice
from utils.drivers.cooking_single_pot import SinglePotReader
from utils.drivers.input_task_gate import InputTaskGate


def run_batch(*, target, recipe, memory, window, controls, device, clock,
              report, directory, entry_runner, selection_runner, pot_runner):
    if type(target) is not int or not 1 <= target <= 99:
        raise ValueError('目标锅数必须为 1～99 的整数')
    recipe=deepcopy(recipe);size=tuple(window.size());identity=None
    summary={'schema':'cooking-batch-p3-v1','target_pots':target,'current_pot':0,
             'completed_pots':0,'total_products':0,'recipe_id':recipe['recipe_id'],
             'product_id':recipe['product_id'],'pots':[],'state':'preparing'}
    context={'current_step':None,'last_successful_step':None,'current_pot':0,
             'confirmed_identity':None}
    summary['diagnostic_context']=context
    native=None
    def step(name,call,*,binding=False):
        context.update(current_step=name,current_pot=summary['current_pot'],
            confirmed_identity=deepcopy(identity))
        report('batch_step','执行批次步骤：'+name,batch=deepcopy(summary),
               diagnostic_context=deepcopy(context),step_phase='begin')
        value=call()
        context.update(last_successful_step=name,confirmed_identity=deepcopy(identity))
        attempts=getattr(native,'binding_metrics',None) if binding else None
        extra={'binding_attempts':attempts} if isinstance(attempts,list) else {}
        report('batch_step','完成批次步骤：'+name,batch=deepcopy(summary),
               diagnostic_context=deepcopy(context),step_phase='complete',**extra)
        return value
    def publish(state,message,**extra):
        summary.update(state=state,message=message)
        atomic_write_json(directory/'batch.json',summary)
        report(state,message,batch=deepcopy(summary),**extra)
    def guard():
        running,stopped=controls.snapshot()
        if stopped or not running or not window.foreground() or tuple(window.size())!=size:
            raise ValueError('批次已停止、暂停或游戏失焦，未继续下一锅')
    def inventory():
        guard();return memory.cooking_inventory_snapshot(5)
    def owned(native):
        nonlocal identity
        guard();state=native.state()
        current=(tuple(state['stove_identity']),state['scope'],state['local_net_id'])
        if identity is None:identity=current
        if current!=identity or state['status']!=0:
            raise ValueError('同一灶台、人物、场景或 Idle 未确认，批次停止')
        return state
    try:
        for number in range(1,target+1):
            guard();summary['current_pot']=number
            potdir=directory/('锅次-'+str(number).zfill(2));potdir.mkdir(exist_ok=False)
            publish('batch_preparing','正在准备第 '+str(number)+' / '+str(target)+' 锅')
            if native is None:
                native=step('create_pot_reader',lambda:SinglePotReader(memory))
            if identity is not None:
                step('bind_previous_confirmed_stove',lambda:native.bind(identity[0]),binding=True)
                step('confirm_idle_before_entry',lambda:owned(native))
            if not material_status(recipe,step('read_material_inventory',inventory))['sufficient']:
                raise ValueError('当前锅材料不足，批次停止，未开锅')
            result=step('open_recipe_panel',lambda:entry_runner(window=window,controls=controls,device=device,
                read_menu=native.snapshot,read_recipe=native.recipe.snapshot,inventory=inventory,
                clock=clock,report=lambda s,m,**x:publish(s,m,**x),input_gate=InputTaskGate(),
                journal=potdir/'entry.json',binding='p3-'+directory.name+'-'+str(number),
                stove_ready=True,timeout=8.,validate_default=False))
            guard()
            if result['state']!='MENU_OPEN':
                raise ValueError('本锅食谱入口未确认，批次停止：'+result.get('reason',result['state']))
            menu=step('reread_recipe_panel',native.snapshot)
            step('bind_open_panel_stove',lambda:native.bind(menu['stove_identity']),binding=True)
            step('confirm_idle_after_panel',lambda:owned(native))
            step('p1_select_recipe',lambda:selection_runner(recipe=deepcopy(recipe),reader=native,window=window,controls=controls,
                device=device,inventory=inventory,clock=clock,report=lambda s,m,**x:publish(s,m,**x)))
            step('revalidate_stove_and_scene',lambda:owned(native))
            before=step('read_inventory_before_p2',inventory)['counts'].get(recipe['product_id'],0)
            # Handoff marker only; existing checks above are not repeated.
            step('enter_p2',lambda:None)
            context['current_step']='p2_single_pot'
            result=pot_runner(recipe=deepcopy(recipe),reader=native,window=window,controls=controls,
                device=device,inventory=inventory,clock=clock,
                report=lambda s,m,**x:publish(s,m,**x),journal=potdir/'pot.json')
            guard();owned(native)
            after=inventory()['counts'].get(recipe['product_id'],0)
            delta=result.get('product_delta')
            if (result.get('state')!='finished' or not result.get('collect_reserved')
                    or tuple(result.get('stove_identity',()))!=identity[0]
                    or result.get('recipe_id')!=recipe['recipe_id']
                    or result.get('product_id')!=recipe['product_id']
                    or type(delta) is not int or delta<=0
                    or result.get('confirmed_products')!=delta or after-before<delta):
                raise ValueError('P2 收菜、灶台复位或指定成品增量未全部确认，未计入完成锅数')
            summary['completed_pots']+=1;summary['total_products']+=delta
            summary['pots'].append({'number':number,'pot_id':result['pot_id'],
                                    'product_delta':delta,'record':str(potdir/'pot.json')})
            publish('batch_pot_finished','第 '+str(number)+' 锅已确认完成')
            if number < target:
                guard()
                notice = step('check_first_recipe_notice',
                              lambda: observe_first_recipe_notice(native, window, recipe))
                summary['pots'][-1]['first_recipe_notice'] = notice
                atomic_write_json(directory/'batch.json', summary)
                report('batch_first_recipe_notice', '首次制作提示检测：' + notice['state'],
                       batch=deepcopy(summary), notice=deepcopy(notice))
                guard()
                if notice['state'] != 'absent':
                    if notice['state'] == 'confirmed':
                        raise ValueError('检测到首次制作提示，请手动关闭后重新开始批次')
                    raise ValueError('锅间底部对话状态未确认，未开始下一锅')
        publish('batch_finished','连续制作完成：'+str(target)+' 锅，成品增加 '+str(summary['total_products']))
        return deepcopy(summary)
    except Exception as exc:
        summary['failure_diagnostic']={**deepcopy(context),
            'exception_type':type(exc).__name__,'message':str(exc),
            'traceback':traceback.format_exc(),
            'object_domain':deepcopy(getattr(exc,'object_domain_diagnostic',
                {'state':'unavailable','reason':'exception has no object-domain witness'}))}
        attempts=getattr(native,'binding_metrics',None)
        if isinstance(attempts,list) and attempts and attempts[-1]['outcome']=='failed':
            summary['failure_diagnostic']['binding_attempts']=attempts
        publish('batch_stopped','连续制作停止：'+type(exc).__name__+': '+str(exc))
        raise
    finally:
        device.release_all()
