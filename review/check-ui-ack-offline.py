from pathlib import Path
import sys,ast,textwrap,types,math,json,os,subprocess,hashlib,argparse
def find_root():
    override=os.environ.get('HEARTOPIA_REPO_ROOT')
    if override:
        candidate=Path(override).resolve()
    else:
        try:candidate=Path(subprocess.check_output(['git','-C',str(Path(__file__).resolve().parent),'rev-parse','--show-toplevel'],stderr=subprocess.DEVNULL,text=True).strip())
        except subprocess.CalledProcessError:candidate=Path(__file__).resolve().parent.parent/'collection-plan-20261106'
    if not (candidate/'utils/drivers/cooking_ui_click_ack.py').is_file():raise RuntimeError('Cannot locate repository; set HEARTOPIA_REPO_ROOT explicitly')
    return candidate
root=find_root();sys.path.insert(0,str(root))
from utils.drivers.windows_input import WindowsInput
from utils.drivers.town_window import TimelineInput
from utils.drivers.cooking_click_diagnostics import ClickDiagnostics,now
from utils.drivers.cooking_alt_interaction import cooking_alt,release_cooking_inputs
from utils.drivers.cooking_ui_click_ack import CookingUiClickAck
import utils.drivers.cooking_alt_interaction as alt
import subprocess
BASELINE="f66c7a877aa3b1882da445b15dd3a6e2eee4d11d"
def run_chain_suite(ack_class,flow_source):
    CookingUiClickAck=ack_class
    source=ast.parse(flow_source)
    fs={n.name:n for n in ast.walk(source) if isinstance(n,ast.FunctionDef)}
    factory='def factory():\n    alt_interaction_active=False\n    pending_ack=None\n    ack_succeeded=False\n'+''.join(textwrap.indent(ast.unparse(fs[n]),'    ')+'\n' for n in ('click','click_body'))+'    return click\n'
    results=[]
    for mode in ('callback_delayed','callback_immediate','no_callback','f9','focus_lost','binding_changed','scene_changed','player_alt','down_failed','token_changed','button_replaced','read_error','cleanup_error','nonfinite_nan','nonfinite_positive','nonfinite_negative'):
     class Clock:
      def __init__(self):self.value=0.;self.up=None
      def monotonic(self):return self.value
     c=Clock();calls=[];logs=[];key_time=[]
     class W:
      hwnd=1
      def pointer(self):return (20,30,True)
      def get_rect(self):return (0,0,100,100)
     w=W()
     def elapsed():return c.value-c.up if c.up is not None else -1
     refs={(2,'ui'):3,(3,'icon_btn'):1,(1,'_clickHandler'):4,(4,'_target'):2,(2,'_clickHandler'):5,(5,'_target'):6}
     cleanup_failed=[False]
     class M:
      def obj(self,p):return {'address':p,'name':'Action','namespace':'System'}
      def ref(self,p,k):
       if mode=='read_error' and elapsed()>=.01:raise OSError('simulated read error')
       if mode=='button_replaced' and elapsed()>=.01 and (p,k)==(3,'icon_btn'):return 999
       if mode=='binding_changed' and elapsed()>=.01 and p==4 and k=='_target':return 999
       return refs[p,k]
     class R:
      ui=types.SimpleNamespace(meta=M())
      hud_diagnostics={'kind':'stir','event_id':'100','widget_match_count':1,'binding_stable':True,
         'widgets':[{'event_model_match':True,'button':1,'icon':2,'callback_target':6}]}
      def state(self):return {'scope':'other' if mode=='scene_changed' and elapsed()>=.01 else 's',
          'stove_identity':[1,2,3],'status':3,'status_started':100}
      def integer(self,p,k,*args):
       if k=='method':return p+100
       if k=='<token>k__BackingField':return 11 if mode=='token_changed' and elapsed()>=.01 else 10
       if k=='_lastClickTime':
        if c.up is not None and mode.startswith('nonfinite_'):return {'nonfinite_nan':float('nan'),'nonfinite_positive':float('inf'),'nonfinite_negative':float('-inf')}[mode]
        updated=(mode=='callback_delayed' and elapsed()>=.03) or (mode=='callback_immediate' and c.up is not None)
        return 11. if updated else 10.
       raise AssertionError(k)
     r=R()
     def send(count,events,size):
      e=events._obj;kind=('mouse',e.mi.dwFlags) if e.type==0 else ('key',e.ki.dwFlags)
      calls.append((kind,c.value))
      if kind==('mouse',4):c.up=c.value
      if mode=='down_failed' and kind==('mouse',2):return 0
      if mode=='cleanup_error' and kind==('key',10) and not cleanup_failed[0]:cleanup_failed[0]=True;return 0
      return 1
     d=TimelineInput(w,WindowsInput(send=send))
     import utils.drivers.cooking_click_diagnostics as click_diag
     click_diag.win32gui.GetForegroundWindow=lambda:1
     alt.win32api.GetAsyncKeyState=lambda vk:0x8000 if mode=='player_alt' or d.keys else 0
     def guard():
      if mode in ('f9','focus_lost') and elapsed()>=.01:raise ValueError(mode)
     def wait(t):c.value+=t;guard()
     ns={'size':(100,100),'device':d,'window':w,'guard':guard,'read':lambda f:f(),'wait':wait,
      'report':lambda *a,**kw:logs.append(kw),'math':math,'cooking_alt':cooking_alt,
      'release_cooking_inputs':release_cooking_inputs,'begin_move':lambda *a:{},
      'execute_move':lambda dev,e,m:dev.execute(e),'finish_move':lambda *a:{},
      'ClickDiagnostics':ClickDiagnostics,'danger_times':{},'CookingUiClickAck':CookingUiClickAck,
      'reader':r,'clock':c,'now':now}
     exec(factory,ns);failed=False;error=None
     try:ns['factory']()(lambda:{'kind':'stir','event_id':'100','scope':'s','stove_identity':[1,2,3],'rect':(10,20,30,40)},hold_alt=True)
     except (ValueError,OSError) as exc:failed=True;error=(type(exc).__name__,str(exc))
     success=mode in ('callback_delayed','callback_immediate')
     assert failed!=success,(mode,failed)
     assert not d.keys and not d.buttons,(mode,calls)
     if ack_class is globals()['CookingUiClickAck'] and mode.startswith('nonfinite_'):
      post=next(x['ack_diagnostic'] for x in logs if 'ack_diagnostic' in x)
      assert 'non_finite' in json.dumps(post,allow_nan=False)
     downs=[x for x in calls if x[0]==('mouse',2)]
     assert len(downs)==(0 if mode=='player_alt' else 1),(mode,calls)
     if mode!='player_alt':
      up=next(t for k,t in calls if k==('mouse',4));alt_up=next(t for k,t in calls if k==('key',10))
      assert alt_up>=up
      diag=next(x['left_diagnostic'] for x in logs if 'left_diagnostic'in x)
      assert diag['alt_held_at_mouse_up']
      if success:assert diag['ui_click_confirmed'] and diag['ui_click_after']['last_click_time']==11.
      if mode=='callback_delayed':assert alt_up-up>=.03-1e-8
      if mode=='no_callback':assert alt_up-up>=1. and diag['ui_click_confirmed'] is False
     results.append({'case':mode,'passed':True,'inputs':calls,'exception':error,'keys':sorted(d.keys),'buttons':sorted(d.buttons),'clock':c.value})
    return results


def check_diagnostic_values():
    import copy,time,statistics
    from utils.drivers.cooking_ui_click_ack import _diagnostic_json_copy
    a=CookingUiClickAck.__new__(CookingUiClickAck)
    a.target={'event_id':'E'};a.pre_click_snapshot={}
    a.raw_observations=[(0.,.01,[('icon.callback',10,None,.001),('icon.callback',20,None,.002)],None)]
    r=a.finalize();checks=r['confirmation_observed_values'][0]['ack_binding_checks']
    assert (checks[1]['before'],checks[1]['after']['value'],checks[1]['changed'])==(10,20,True)
    assert r['confirmation_observed_values'][0]['ack_binding_before']['icon.callback']==10
    assert r['confirmation_observed_values'][0]['ack_binding_after']['icon.callback']['value']==20
    for value,meaning in [(float('nan'),'NaN'),(float('inf'),'positive_infinity'),(float('-inf'),'negative_infinity')]:
        a.raw_observations=[(0.,.01,[('last_click_time',value,None,.001)],ValueError('business error'))]
        r=a.finalize();json.dumps(r,allow_nan=False)
        tagged=r['confirmation_observed_values'][0]['ack_binding_checks'][0]['after']['value']
        assert tagged['original_type']=='float' and tagged['meaning']==meaning
        assert a.raw_observations[0][2][0][1] is value
        assert r['confirmation_observed_values'][0]['error']['message']=='business error'
    import utils.drivers.cooking_ui_click_ack as module
    original_dumps=module.json.dumps
    def failed_encoder(*args,**kwargs):raise TypeError('injected JSON encoder failure')
    try:
        module.json.dumps=failed_encoder
        a.raw_observations=[]
        minimal=a.finalize()
        assert minimal['state']=='diagnostic_encoding_failed'
        assert minimal['diagnostic_serialization_errors'][0]['error_type']=='TypeError'
        assert minimal['diagnostic_serialization_errors'][0]['error']=='injected JSON encoder failure'
        try:
            try:raise ValueError('original business exception')
            finally:a.finalize()
        except ValueError as exc:assert str(exc)=='original business exception'
        else:raise AssertionError('original business exception lost')
    finally:module.json.dumps=original_dumps
    json.dumps(minimal,allow_nan=False)
    raw={'finite':1.25,'unsupported':object()};safe,errors=_diagnostic_json_copy(raw)
    assert safe['finite']==1.25 and errors and raw['unsupported'] is not safe['unsupported']
    cycle=[];cycle.append(cycle);safe,errors=_diagnostic_json_copy(cycle);assert errors;json.dumps(safe,allow_nan=False)
    a.raw_observations=[(0.,.01,[('x',1,None,.001),('x',2,None,.002)],None)]
    a.pre_click_snapshot={'unsupported':object()};r=a.finalize();assert r['diagnostic_serialization_errors']
    durations=[]
    for _ in range(1000):
        t=time.perf_counter();a.finalize();durations.append((time.perf_counter()-t)*1000)
    durations.sort()
    return {'passed':True,'mock_finalize_ms':{'median':statistics.median(durations),'p95':durations[949],'max':max(durations)},'physical_reads':'unavailable'}

def check_callbacks():
    class Meta:
        def __init__(self,case):self.case=case;self.count=0
        def obj(self,address):
            if (self.case in (2,4) and address==10) or (self.case in (3,4) and address==20):raise OSError('invalid delegate')
            if self.case==10 and address==20:raise ValueError('domain mismatch')
            return {'address':address+100,'name':'Action','namespace':'System'}
        def ref(self,address,field):
            if address==1:
                self.count+=1
                return 30 if self.case==8 and self.count>1 else 10 if self.case==11 else 20
            self.obj(address)
            if self.case==9 and address==20:raise OSError('target failed')
            return 200 if self.case in (7,12) and address==20 else 100
    results=[]
    for case in range(1,13):
        m=Meta(case)
        def integer(address,*args):
            m.obj(address)
            if case==9 and address==20:raise OSError('method failed')
            return 600 if case in (6,12) and address==20 else 500
        a=CookingUiClickAck.__new__(CookingUiClickAck)
        a.reader=types.SimpleNamespace(ui=types.SimpleNamespace(meta=m),integer=integer);a.icon=1;a.icon_cb=10
        r=a._callback_snapshots();old=r['old_callback_snapshot'];new=r['current_callback_snapshot'];c=r['callback_reference_checks']
        assert old['owner']==10 and new['owner']==(10 if case==11 else 20)
        for row in (old,new):
            for field in ('delegate_type','target','method'):assert row[field]['owner']==row['owner']
        if case in (2,4):assert not old['target']['available']
        if case in (3,4,9,10):assert not new['target']['available']
        if case in (1,5,11):assert c['same_target'] is True and c['same_method'] is True
        if case==6:assert c['same_target'] is True and c['same_method'] is False
        if case==7:assert c['same_target'] is False and c['same_method'] is True
        if case==8:assert c['reference_stable'] is False and c['same_target'] is None and c['same_method'] is None
        if case==12:assert c['same_target'] is False and c['same_method'] is False
        results.append({'case':case,'passed':True})
    return results

def check_boundaries(original_class):
    def run(cls,cost,update):
        t=[0.];calls=[]
        refs={(1,'ui'):2,(2,'icon_btn'):3,(3,'_clickHandler'):4,(4,'_target'):1,(1,'_clickHandler'):5,(5,'_target'):6}
        ints={(4,'method'):7,(5,'method'):8,(6,'<token>k__BackingField'):9}
        def tick(kind):calls.append(kind);t[0]+=cost
        def ref(o,k):tick(('ref',o,k));return refs[o,k]
        def integer(o,k,*args):
            tick(('integer',o,k))
            return (11. if t[0]>=update else 10.) if k=='_lastClickTime' else ints[o,k]
        def state():tick(('state',));return {'scope':'S','stove_identity':[1],'status':3,'status_started':1}
        a=cls.__new__(cls);a.reader=types.SimpleNamespace(ui=types.SimpleNamespace(meta=types.SimpleNamespace(ref=ref)),integer=integer,state=state)
        a.target={'scope':'S','stove_identity':[1],'event_id':'1'};a.icon=1;a.ui=2;a.button=3;a.button_cb=4;a.icon_cb=5;a.model=6;a.methods=(7,8);a.token=9
        if cls is CookingUiClickAck:a.raw_observations=[];a._current_reads=[]
        while True:
            sample=a.observe()
            if sample['last_click_time']>10:return 'confirmed',round(t[0],9),calls
            if t[0]>=1:return 'timeout',round(t[0],9),calls
            t[0]+=.01
    results=[]
    for cost in (0,.0001,.001,.003):
        for update in (.90,.95,.99,.995,1.,1.05):
            a,b=run(original_class,cost,update),run(CookingUiClickAck,cost,update)
            assert a==b,(cost,update,a,b)
            results.append({'cost_ms':cost*1000,'update_at':update,'a':{'result':a[0],'end':a[1],'read_sequence':a[2]},'b':{'result':b[0],'end':b[1],'read_sequence':b[2]},'passed':True})
    return results

if __name__=='__main__':
    original={}
    source=subprocess.check_output(['git','-C',str(root),'show',BASELINE+':utils/drivers/cooking_ui_click_ack.py']).decode('utf-8')
    exec(source,original)
    original_flow=subprocess.check_output(['git','-C',str(root),'show',BASELINE+':utils/flows/cooking_single_pot.py']).decode('utf-8')
    a=run_chain_suite(original['CookingUiClickAck'],original_flow)
    b=run_chain_suite(CookingUiClickAck,(root/'utils/flows/cooking_single_pot.py').read_text(encoding='utf-8'))
    assert a==b,(a,b)
    from utils.core.cooking_single_pot import SinglePotEvents
    ev=SinglePotEvents({'product_id':45557});ev.started=True
    state={'status':3,'status_started':1,'previous_status':2,'owner_net_id':1,'local_net_id':1,'food_id':45557}
    assert ev.observe(state)==('stir','1');ev.reserve('stir','1');assert ev.observe(state) is None
    result={'baseline':BASELINE,'input_chain_a':a,'input_chain_b':b,'diagnostic_values':check_diagnostic_values(),'callback_cases':check_callbacks(),'boundaries':check_boundaries(original['CookingUiClickAck']),'danger_dedup_passed':True}
    print(json.dumps({'baseline':BASELINE,'input_chain_cases':len(b),'callback_cases':len(result['callback_cases']),'boundary_cases':len(result['boundaries']),'diagnostic_values':result['diagnostic_values'],'danger_dedup_passed':True},indent=2))
    result['tested_sources']={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('utils/drivers/cooking_ui_click_ack.py','utils/flows/cooking_single_pot.py')}
    result['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result['encoder_fault_injection_passed']=True
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path(__file__).parent/'ui-ack-offline-results.json');args=parser.parse_args()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
