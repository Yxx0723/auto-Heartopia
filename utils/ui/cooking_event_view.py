"""Presentation of existing cooking events; never infers a game action."""
from datetime import datetime

NATIVE={0:'Idle',1:'Preparing',2:'Cooking',3:'Danger',4:'Relief',5:'Succeed',6:'Failed'}


def event_view(event):
    state=event.get('state','unknown');result=event.get('message','')
    batch=event.get('batch') or {};context=batch.get('diagnostic_context') or {}
    stage=context.get('current_step') or state
    status='进行中';name=state
    if state=='native_observation':
        native=(event.get('native_state') or {}).get('status')
        name='原生状态 '+NATIVE.get(native,str(native));status='失败' if native==6 else '已确认'
    elif state=='cooking':
        native=(event.get('native_state') or {}).get('status')
        name='烹饪状态 '+NATIVE.get(native,str(native))
        status='失败' if native==6 else '已确认' if native in NATIVE else '进行中'
    elif state=='batch_step':
        stage=(event.get('diagnostic_context') or context).get('current_step',stage)
        name='步骤 '+stage
        status='已确认' if event.get('step_phase')=='complete' else '进行中'
    elif state=='start_left_diagnostic':
        d=event.get('left_diagnostic') or {}
        inserted=all(d.get(k)==1 for k in ('mouse_down_inserted','mouse_up_inserted'))
        status='已派发' if inserted else '未确认'
        name='开始左键输入'
        result=('Windows 左键按下、释放各插入 1 次；UI 接收及开锅另行确认' if inserted
                else 'Windows 左键派发未全部确认；不代表游戏已开锅')
    elif state=='stir_left_diagnostic':
        d=event.get('left_diagnostic') or {};ack=d.get('ui_click_confirmed')
        status='已确认' if ack is True else '未确认'
        name='翻锅 UI 点击'
        result=('UI 按钮处理时间戳已确认更新；仍需 Relief 确认翻锅'
                if ack is True else '未取得 UI 点击确认；不得计为翻锅成功')
    elif state=='start_ui_diagnostic':
        d=event.get('start_diagnostic') or {};name='开始 UI 观察'
        status='已确认' if event.get('stage')=='start_native_confirmed' else '进行中'
        menu=d.get('menu') or {};native=d.get('native_state') or {}
        result=f"阶段 {event.get('stage')}；面板打开={menu.get('open','未确认')}；原生状态={NATIVE.get(native.get('status'),'未确认')}；按钮时间戳更新={d.get('button_handler_timestamp_updated','未确认')}"
    elif state in ('collect_key_dispatched','recipe_confirming'):
        status='已派发'
    elif state in ('start_reserved','stir_reserved','collect_reserved','ENTRY_DISPATCHED'):
        # Reservation is persisted before input. It is not dispatch evidence.
        status='进行中';result+='；操作登记不等于输入或游戏执行成功'
    elif state in ('MENU_OPEN','RECIPE_SELECTED','RECIPE_VALIDATED','inventory_ready'):
        status='已确认'
    elif state in ('finished','batch_pot_finished','batch_finished'):
        status='已完成'
    elif state in ('unconfirmed','UNCONFIRMED','stopped','STOPPED','paused','PAUSED'):
        status='未确认'
    elif state in ('batch_stopped','error','ERROR','inventory_error','INVALID'):
        status='失败'
    stamp=event.get('at','')
    try:stamp=datetime.fromisoformat(stamp).isoformat(sep=' ',timespec='milliseconds')
    except (ValueError,TypeError):stamp=str(stamp or '时间未提供')
    return {'time':stamp,'pot':batch.get('current_pot','—'),'stage':stage,
            'event':name,'result':str(result).replace('\n',' / '),'status':status}
