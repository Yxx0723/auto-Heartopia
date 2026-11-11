"""Production click/ACK/ALT chain with fake input only; no game access."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_cooking_callback_revalidation import Scene
from utils.drivers.cooking_ui_click_ack import CookingUiClickAck
from utils.drivers.cooking_alt_interaction import cooking_alt, release_cooking_inputs
from utils.drivers.cooking_click_diagnostics import ClickDiagnostics


class PostScene(Scene):
    def __init__(self, case):
        super().__init__('unchanged')
        self.post_case=case;self.changed=False;self.clicked=False
        self.old_reads=0;self.new_parses=0;self.post_refs=0
        self.clock=SimpleNamespace(t=0.,up=None)
        self.clock.monotonic=lambda:self.clock.t
        self.clock.sleep=lambda seconds:setattr(self.clock,'t',self.clock.t+seconds)
        self.reader._hud_callback_method=self.method

    def method(self, *args):
        if self.clicked:
            self.new_parses+=1
            if self.post_case in ('slow','relief_timeoutcost'):self.clock.t+=1.01
            if self.post_case=='tail_slow':self.clock.t+=.99
        return super_method(self.reader,*args)

    def obj(self,obj):
        if self.changed and obj==101:
            self.old_reads+=1
            raise OSError('old delegate invalid')
        if self.clicked and obj==102 and self.post_case in ('invalid','read_error','relief_invalid'):
            if self.post_case=='read_error':raise RuntimeError('new delegate read failed')
            raise OSError('new delegate invalid')
        return super().obj(obj)

    def ref(self,obj,key):
        if (obj,key)==(20,'_clickHandler') and self.clicked and self.post_case not in ('normal','f9','focus','slow_last_click_read'):
            self.changed=True;self.post_refs+=1
            if self.post_case in ('again','relief_again') and self.post_refs>=2:return 103
            if self.post_case=='late_again' and self.post_refs>=3:return 103
            return 102
        if self.changed and obj==102 and key=='_target' and self.post_case=='relief_model':return 12
        if self.changed and obj==102 and key=='_target' and self.post_case in ('target','relief_target'):return 999
        if self.clicked and (obj,key)==(21,'icon_btn') and self.post_case=='button':return 31
        return super().ref(obj,key)

    def state(self):
        result=super().state()
        if self.changed and self.post_case=='event':result['status_started']+=1
        if self.changed and self.post_case=='scene':result['scope']='OTHER'
        if self.clicked and self.post_case=='unobserved_relief' and self.changed:
            result.update(status=4,status_started=self.native['status_started']+1000)
        if self.clicked and self.post_case.startswith('relief'):
            result.update(status=4,status_started=self.native['status_started']+1000,previous_status=3)
            if self.changed and self.post_case=='relief_mismatch':result['status_started']+=1000
            if self.changed and self.post_case=='relief_next' and self.new_parses:
                result.update(status=2,status_started=result['status_started']+1000)
            if self.post_case=='relief_after_validated' and self.clock.up is not None and self.clock.t-self.clock.up>=.01:
                result.update(status=2,status_started=result['status_started']+1000)
            if self.changed and self.post_case=='relief_scene':result['scope']='OTHER'
            if self.changed and self.post_case=='relief_stove':result['stove_identity']=[370033,88,123]
        return result

    def integer(self,obj,key,*args):
        if self.clicked and key=='_lastClickTime':
            if self.post_case=='slow_last_click_read':
                self.clock.t+=1.01
                if not hasattr(self,'slow_read_finished_at'):
                    self.slow_read_finished_at=self.clock.t
            if self.post_case=='tail_slow':self.clock.t+=.02
            if self.post_case in ('timeout','slow','relief_no_ack','relief_after_validated'):return 10.
            if self.post_case=='relief_delayed_ack' and self.clock.t-self.clock.up<.03:return 10.
            return 11.
        if self.changed and key=='<token>k__BackingField' and self.post_case in ('token','relief_token'):return 2
        if obj==102 and key=='method' and self.post_case in ('method','relief_method'):return 5001
        return super().integer(obj,key,*args)


from utils.drivers.cooking_single_pot import SinglePotReader
super_method=SinglePotReader._hud_callback_method


def execute(case):
    scene=PostScene(case);clock=scene.clock;reports=[]
    class Device:
        def __init__(self):self.keys=set();self.buttons=set();self.log=[];self.point=(20,30);self.click_alt=[]
        def execute(self,e):
            self.log.append((dict(e),clock.t))
            if e['type'] in ('mouse_down','mouse_up'):self.click_alt.append('alt_l' in self.keys)
            if e['type']=='key_down':self.keys.add(e['key'])
            if e['type']=='key_up':
                self.keys.discard(e['key'])
                if case=='relief_cleanup_error':raise RuntimeError('simulated ALT release reporting failure')
            if e['type']=='mouse_down':self.buttons.add('left')
            if e['type']=='mouse_up':
                self.buttons.discard('left');scene.clicked=True;clock.up=clock.t
            if e['type']=='cursor_move':self.point=(e['x'],e['y'])
        def release_all(self):
            for key in list(self.keys):self.execute({'type':'key_up','key':key})
            for button in list(self.buttons):self.execute({'type':'mouse_up','button':button})
    device=Device()
    controls=SimpleNamespace(snapshot=lambda:(True,(case=='f9' and scene.clicked) or
        (case in ('f9_revalidate','relief_f9') and scene.changed)),pause=lambda:None)
    window=SimpleNamespace(pointer=lambda:(*device.point,True),size=lambda:(100,100),
        foreground=lambda:not((case=='focus' and scene.clicked) or
            (case in ('focus_revalidate','relief_focus') and scene.changed)))
    source=Path(__file__).resolve().parents[2]/'utils/flows/cooking_single_pot.py'
    outer=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
               if isinstance(n,ast.FunctionDef) and n.name=='run_single_pot')
    defs=[n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name in ('guard','wait','click','click_body')]
    code='def run():\n    alt_interaction_active=False;pending_ack=None;ack_succeeded=False\n'+''.join(
        '    '+line+'\n' for node in defs for line in ast.unparse(node).splitlines())
    code+='    return click(lambda:reader.hud_target(size,"stir",expected),hold_alt=True)\n'
    ns={'reader':scene.reader,'size':(100,100),'expected':scene.native,'device':device,'window':window,
        'clock':clock,'controls':controls,'task_deadline':100.,'read':lambda f:f(),
        'report':lambda *a,**k:reports.append((a,k)),'danger_times':{},'math':math,
        'now':lambda:'offline','CookingUiClickAck':CookingUiClickAck,'ClickDiagnostics':ClickDiagnostics,
        'release_cooking_inputs':release_cooking_inputs,
        'cooking_alt':lambda d,w,g,t,r:cooking_alt(d,w,g,t,r,alt_pressed=lambda:False),
        'begin_move':lambda *a:{},'execute_move':lambda d,e,z:d.execute(e),'finish_move':lambda *a:{}}
    exec(code,ns)
    error=None
    try:ns['run']()
    except Exception as exc:error=exc
    return scene,device,reports,error


@pytest.mark.parametrize('case,success',[
    ('normal',True),('equivalent',True),('timeout',False),('target',False),('method',False),
    ('token',False),('event',False),('scene',False),('again',False),('invalid',False),
    ('button',False),('f9',False),('focus',False),('slow',False),
    ('f9_revalidate',False),('focus_revalidate',False),('read_error',False),('late_again',False),('tail_slow',False),('slow_last_click_read',False)])
def test_production_post_click_chain(case,success):
    scene,device,reports,error=execute(case)
    assert (error is None)==success,(case,error)
    downs=[t for e,t in device.log if e['type']=='mouse_down']
    ups=[t for e,t in device.log if e['type']=='mouse_up']
    assert len(downs)==len(ups)==1
    assert scene.old_reads==0
    assert scene.new_parses<=1
    assert not device.keys and not device.buttons
    alt_up=[t for e,t in device.log if e['type']=='key_up']
    assert len(alt_up)==1 and alt_up[0]>=ups[0]
    diagnostic=next(k['left_diagnostic'] for a,k in reports if a[0]=='stir_left_diagnostic')
    assert device.click_alt==[True,True]
    post=next(k['ack_diagnostic'] for a,k in reports if a[0]=='ack_post_diagnostic')
    record=post['post_click_callback_revalidation']
    if case=='equivalent':
        assert diagnostic['ui_click_confirmed'] is True
        assert record['passed'] and record['old_callback']==101 and record['new_callback']==102
    if case=='timeout':
        assert record['passed'] and not diagnostic.get('ui_click_confirmed',False)
        assert 1.<=scene.clock.t-scene.clock.up<1.03
    if case in ('slow','tail_slow'):assert '原 UI 确认期限' in str(error)
    if case in ('f9','f9_revalidate'):assert 'F9' in str(error)
    if case in ('focus','focus_revalidate'):assert '失焦' in str(error)
    if case=='read_error':assert isinstance(error,RuntimeError) and str(error)=='new delegate read failed'
    if case=='slow_last_click_read':
        assert isinstance(error,ValueError) and '限定时间内未确认点击' in str(error)
        assert diagnostic['ui_click_confirmed'] is False
        assert diagnostic['ui_click_before']['last_click_time']==10.
        assert diagnostic['ui_click_after']['last_click_time']==11.
        assert 'ui_first_update_observed_at' not in diagnostic
        assert record is None and not scene.changed and scene.new_parses==0
        # Finalize may reread the field after cleanup; measure the live read only.
        assert scene.slow_read_finished_at-scene.clock.up==pytest.approx(1.01)


@pytest.mark.parametrize('case,success',[
    ('relief',True),('relief_mismatch',False),('relief_next',False),
    ('relief_scene',False),('relief_stove',False),('relief_model',False),
    ('relief_token',False),('relief_target',False),('relief_method',False),
    ('relief_again',False),('relief_invalid',False),('relief_no_ack',False),
    ('relief_delayed_ack',True),('relief_timeoutcost',False),('relief_after_validated',False),
    ('relief_f9',False),('relief_focus',False),('relief_cleanup_error',False)])
def test_relief_phase_is_live_and_ack_remains_independent(case,success):
    scene,device,reports,error=execute(case)
    assert (error is None)==success,(case,error)
    assert sum(e['type']=='mouse_down' for e,t in device.log)==1
    assert sum(e['type']=='mouse_up' for e,t in device.log)==1
    assert scene.old_reads==0 and scene.new_parses<=1
    assert device.click_alt==[True,True]
    assert not device.buttons and not device.keys
    up=next(t for e,t in device.log if e['type']=='mouse_up')
    alt_up=[t for e,t in device.log if e['type']=='key_up']
    assert len(alt_up)==1 and alt_up[0]>=up
    diag=next(k['left_diagnostic'] for a,k in reports if a[0]=='stir_left_diagnostic')
    post=next(k['ack_diagnostic'] for a,k in reports if a[0]=='ack_post_diagnostic')
    relief=post['confirmation_first_observed_relief']
    assert relief['status']==4 and relief['status_started']==scene.native['status_started']+1000
    record=post['post_click_callback_revalidation']
    assert record['confirmation_observed_phase']==relief
    if success:
        assert record['passed'] and diag['ui_click_confirmed']
        assert diag['ui_click_after']['last_click_time']>diag['ui_click_before']['last_click_time']
    elif case=='relief_no_ack':
        assert record['passed'] and not diag.get('ui_click_confirmed',False)
        assert 1.<=scene.clock.t-up<1.03
    elif case=='relief_after_validated':
        assert record['passed'] and not diag.get('ui_click_confirmed',False)
        assert 'Relief 阶段变化' in str(error)
    elif case=='relief_cleanup_error':
        assert isinstance(error,RuntimeError) and str(error)=='simulated ALT release reporting failure'
        assert record['passed'] and diag['ui_click_confirmed']
    else:
        assert not record['passed'] and not diag.get('ui_click_confirmed',False)
    if case=='relief_f9':assert 'F9' in str(error)
    if case=='relief_focus':assert '失焦' in str(error)
    if case=='relief_invalid':assert isinstance(error,OSError) and str(error)=='new delegate invalid'


def test_relief_seen_only_inside_revalidation_cannot_be_used_as_live_ack_evidence():
    scene,device,reports,error=execute('unobserved_relief')
    assert isinstance(error,ValueError)
    assert sum(e['type']=='mouse_down' for e,t in device.log)==1
    assert sum(e['type']=='mouse_up' for e,t in device.log)==1
    assert not device.keys and not device.buttons
    assert scene.old_reads==0 and scene.new_parses==0
    post=next(k['ack_diagnostic'] for a,k in reports if a[0]=='ack_post_diagnostic')
    assert post['confirmation_first_observed_relief'] is None
    assert not post['post_click_callback_revalidation']['passed']
    assert post['post_click_callback_revalidation']['confirmation_observed_phase']['status']==3
