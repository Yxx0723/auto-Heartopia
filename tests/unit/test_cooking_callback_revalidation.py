"""Read-only fake scene exercises production hud_target; never creates input devices."""
import struct
import pytest
from types import SimpleNamespace
from utils.drivers.cooking_single_pot import SinglePotReader

class Scene:
    def __init__(self,case):
        self.case=case;self.callback_reads=0;self.swapped=False;self.old_after_swap=0
        self.reader=SinglePotReader.__new__(SinglePotReader)
        self.reader.bound=((0,99),0,0,0,0,40)
        self.reader.ui=SimpleNamespace(meta=self,ui_views=lambda:(0,0,0,[1]),expected=lambda *a:None)
        self.reader.layout=SimpleNamespace(active=lambda obj:True)
        self.reader._hud_cook_id=lambda *a:(77,lambda:True)
        self.reader.entity_net=lambda *a:0
        self.reader._rect=lambda *a:(10.,20.,30.,40.)
        self.reader.integer=self.integer
        self.native={'scope':'S','stove_identity':[370033,77,123], 'level_object_id':123,'burner_net_id':77,
            'status':3,'status_started':(1<<62)+621355968000000000,'period_ms':6000}
        self.reader.state=self.state
    def state(self):
        value=dict(self.native)
        if self.case=='event' and self.swapped:value['status_started']+=10000
        return value
    def obj(self,obj):
        if obj==101 and self.swapped and self.case=='old_invalid':
            self.old_after_swap+=1;raise OSError('old callback invalid')
        if obj==102 and self.case=='new_invalid':raise OSError('new callback invalid')
        names={1:('TrackingPanel','XDTGame.UI.Panel'),10:('EntityTrackBarModel','XDTGUI.Module.Track.Bars'),
            11:('CookDangerTrackCellModel','XDTGUI.Module.Track.Cells'),20:('CommonIconForCookDanger','XDTGame.UI.Widget')}
        name,namespace=names.get(obj,('WidgetTrigger`1','XDTGUI.Core.Statics'))
        return {'name':name,'namespace':namespace,'fields':{'tracking_cook_danger_list':{'flags':0}} if obj==5 else {}}
    def ref(self,obj,key):
        self.obj(obj)
        if (obj,key)==(20,'_clickHandler'):
            self.callback_reads+=1
            if self.case=='unchanged':return 101
            if self.callback_reads>=2:self.swapped=True
            if self.case=='again' and self.callback_reads>=4:return 103
            return 102 if self.swapped else 101
        if key=='delegates':return 0
        if obj in (101,102,103) and key=='_target':return 999 if obj==102 and self.case=='target' else 11
        if obj==104 and key=='_target':return 20
        if (obj,key)==(21,'icon_btn'):return 31 if self.case=='button' and self.swapped else 30
        refs={(1,'ui'):2,(2,'tracking_bar_widget'):3,(3,'_trackModule'):4,(4,'<XDTGame.Core.IModule.Owner>k__BackingField'):99,
            (4,'_bars'):100,(10,'_cells'):110,(10,'_entity'):40,(3,'ui'):5,(5,'tracking_cook_danger_list'):6,
            (6,'_children'):120,(20,'ui'):21,(21,'<gameObject>k__BackingField'):22,(21,'root_visible_go'):23,
            (30,'_clickHandler'):104,(31,'_clickHandler'):104}
        return refs[obj,key]
    def list_refs(self,obj):return {100:[10],110:[11],120:[20]}[obj]
    def integer(self,obj,key,*args):
        self.obj(obj)
        if key=='_lastClickTime':return 11. if getattr(self,'clicked',False) else 10.
        if key=='method':return 6000 if obj==104 else 5001 if obj==102 and self.case=='method' else 5000
        if key=='_valid' or key=='_visible':return 1
        if key=='<entityId>k__BackingField':return 77
        if key=='_endUnix':return 6000
        if key=='_duration':return 6000.
        if key=='<token>k__BackingField':return 2 if self.case=='token' and self.swapped else 1
        raise AssertionError((obj,key))
    def data(self,obj,size):
        result=bytearray(32);struct.pack_into('<I',result,4,0x06000001)
        struct.pack_into('<Q',result,8,601 if obj==6000 else 501)
        struct.pack_into('<Q',result,24,obj+10000)
        return bytes(result)
    def cls(self,obj):
        return {'name':'CommonIcon' if obj==601 else 'CookDangerTrackCellModel',
                'namespace':'XDTGame.UI.Widget' if obj==601 else 'XDTGUI.Module.Track.Cells'}
    def text(self,obj):return 'OnIconClicked' if obj==16000 else 'WrongMethod' if obj==15001 else 'TriggerOnClickByView'

@pytest.mark.parametrize('case,allowed',[
    ('unchanged',True),('equivalent',True),('target',False),('method',False),('token',False),
    ('again',False),('new_invalid',False),('event',False),('button',False),('old_invalid',True)])
def test_production_hud_revalidation(case,allowed):
    scene=Scene(case);r=scene.reader
    if allowed:
        target=r.hud_target((100,100),'stir',scene.native)
        assert target['event_id']==str(scene.native['status_started'])
        if case!='unchanged':
            evidence=r.hud_diagnostics['callback_revalidation']
            assert evidence['passed'] and evidence['allow_dispatch']
            assert evidence['new_callback']==evidence['address_recheck']==102
            assert len(r._danger_revalidation_used)==1
        assert scene.old_after_swap==0
    else:
        with pytest.raises(ValueError):r.hud_target((100,100),'stir',scene.native)
        assert not r.hud_diagnostics['callback_revalidation']['allow_dispatch']
    # This production reader only returns a target; no input dispatcher exists here.
    assert not hasattr(scene,'device')


def execute_production_click(scene):
    import ast,math
    from pathlib import Path
    from utils.drivers.cooking_alt_interaction import cooking_alt,release_cooking_inputs
    from utils.drivers.cooking_click_diagnostics import ClickDiagnostics
    from utils.drivers.cooking_ui_click_ack import CookingUiClickAck
    source=(Path(__file__).resolve().parents[2]/'utils/flows/cooking_single_pot.py').read_text(encoding='utf-8')
    outer=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name=='run_single_pot')
    defs=[x for x in outer.body if isinstance(x,ast.FunctionDef) and x.name in ('click','click_body')]
    code='def run():\n    alt_interaction_active=False;pending_ack=None;ack_succeeded=False\n'+''.join('    '+line+'\n' for f in defs for line in ast.unparse(f).splitlines())+'    return click(lambda:reader.hud_target(size,"stir",expected),hold_alt=True)\n'
    class Device:
        def __init__(self):self.keys=set();self.buttons=set();self.log=[];self.point=(20,30)
        def execute(self,e):
            self.log.append(e)
            if e['type']=='key_down':self.keys.add(e['key'])
            if e['type']=='mouse_down':self.buttons.add('left')
            if e['type']=='mouse_up':self.buttons.discard('left');scene.clicked=True
            if e['type']=='cursor_move':self.point=(e['x'],e['y'])
        def release_all(self):self.keys.clear();self.buttons.clear()
    device=Device();clock=SimpleNamespace(t=0.);clock.monotonic=lambda:clock.t
    def wait(t):clock.t+=t
    window=SimpleNamespace(pointer=lambda:(*device.point,True))
    namespace={'reader':scene.reader,'size':(100,100),'expected':scene.native,'device':device,'window':window,
        'clock':clock,'guard':lambda:None,'wait':wait,'read':lambda fn:fn(),'report':lambda *a,**k:None,
        'danger_times':{},'math':math,'now':lambda:'offline','CookingUiClickAck':CookingUiClickAck,
        'ClickDiagnostics':ClickDiagnostics,'release_cooking_inputs':release_cooking_inputs,
        'cooking_alt':lambda d,w,g,t,r:cooking_alt(d,w,g,t,r,alt_pressed=lambda:False),
        'begin_move':lambda *a:{},'execute_move':lambda d,e,z:d.execute(e),'finish_move':lambda *a:{}}
    exec(code,namespace)
    error=None
    try:namespace['run']()
    except Exception as exc:error=exc
    return device,error

@pytest.mark.parametrize('case,allowed',[
    ('unchanged',True),('equivalent',True),('target',False),('method',False),('token',False),
    ('again',False),('new_invalid',False),('event',False),('button',False),('old_invalid',True)])
def test_actual_click_dispatch_bound(case,allowed):
    device,error=execute_production_click(Scene(case))
    downs=sum(e['type']=='mouse_down' for e in device.log)
    ups=sum(e['type']=='mouse_up' for e in device.log)
    assert (downs,ups)==((1,1) if allowed else (0,0))
    assert (error is None)==allowed
    assert not device.keys and not device.buttons


def test_recovery_budget_survives_multiple_hud_reads():
    scene=Scene('equivalent');r=scene.reader
    r.hud_target((100,100),'stir',scene.native)
    assert len(r._danger_revalidation_used)==1
    r.hud_target((100,100),'stir',scene.native)
    assert r.hud_diagnostics['callback_revalidation']['allow_dispatch']
    scene.case='again'
    with pytest.raises(ValueError):r.hud_target((100,100),'stir',scene.native)
    assert len(r._danger_revalidation_used)==1
