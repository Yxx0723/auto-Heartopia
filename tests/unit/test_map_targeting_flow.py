"""Complete UI target setup against a fake map; never sends real game input."""
import pytest

from utils.core.map_targeting import MapView
from utils.flows.map_targeting import MapTargeter
from utils.core.map_target_policy import MapTargetPolicy


class FakeMap:
    def __init__(self):
        self.time=0.;self.keys=set();self.buttons=set();self.events=[]
        self.opened=self.add=self.spot=self.tracked=False
        self.cursor=(0,0,True);self.target=(10.,2.,20.);self.markers={}
        self.held_at=0.;self.stop_at=100.;self.wrong_hold=self.wrong_track=False
        self.wrong_cursor=False;self.track_clicks=0
        self.m_open_only=False

    def monotonic(self):return self.time
    def sleep(self,seconds):self.time+=seconds
    def size(self):return [1000,1000]
    def foreground(self):return True
    def pointer(self):return (0,0,True) if self.wrong_cursor else self.cursor
    def snapshot(self):return True,self.time>=self.stop_at
    def check(self):
        if self.time>=self.stop_at:raise RuntimeError('stopped')

    def execute(self,e):
        self.events.append(e.copy());kind=e['type']
        if kind=='cursor_move':self.cursor=(e['x'],e['y'],True)
        elif kind=='key_down':self.keys.add(e['key'])
        elif kind=='key_up':
            self.keys.discard(e['key'])
            if e['key']=='m':self.opened=True if self.m_open_only else not self.opened
            if not self.opened:self.spot=False
        elif kind=='mouse_down':self.buttons.add(e['button']);self.held_at=self.time
        elif kind=='mouse_up':
            self.buttons.discard(e['button']);x,y,_=self.cursor
            if (x,y)==(100,100):self.opened=True
            elif (x,y)==(50,50):self.opened=False;self.spot=False
            elif self.time-self.held_at>=1.:self.add=True
            elif self.add and (x,y)==(750,750):self.markers['17']=self.target;self.add=False
            elif self.spot and (x,y)==(850,850):self.tracked=True;self.track_clicks+=1
            elif self.markers and (x,y)==(510,480):self.spot=True

    def release_all(self):
        for k in tuple(self.keys):self.execute({'type':'key_up','key':k})
        for b in tuple(self.buttons):self.execute({'type':'mouse_up','button':b})

    def guidance(self):
        if not self.tracked:raise ValueError('not tracked')
        t=(99.,0.,99.) if self.wrong_track else (10.,0.,20.)
        return dict(state='ready',context='module',scene_context='s',tracking_id='1234',
                    target=t,path=((0.,0.,0.),t),captured_at=self.time)


class Panel:
    def __init__(self,game):self.g=game
    def snapshot(self):return {'state':'projection_unverified' if self.g.opened else 'closed'}
    def geometry(self,size):
        if not self.g.opened:raise ValueError('map closed')
        return MapView('s','v',tuple(size),(1000.,1000.),(0.,0.),1.,1.,self.g.time,True,True,(0.,0.,1000.,1000.))
    def custom_markers(self):return dict(self.g.markers)
    def map_control(self,size,action):
        opened=self.g.opened if action=='close' else not self.g.opened
        return dict(state='open' if opened else 'closed',kind='map_'+action,
            captured_at=self.g.time,scene_context='s',view_identity='v',
            buttons={action:(40,40,60,60) if action=='close' else (90,90,110,110)})
    def dialog(self,size,kind='add'):
        opened=self.g.add if kind=='add' else self.g.spot
        d={'state':'open' if opened else 'closed','kind':kind,'captured_at':self.g.time}
        if opened:
            d.update(usage_id='0' if kind=='add' else '17',category=7,scene_context='s',
                position_xz=(99.,99.) if self.g.wrong_hold else (10.,20.),
                buttons={'save':(700,700,800,800)} if kind=='add' else {'track':(800,800,900,900)})
        return d


def targeter(game):
    return MapTargeter(game,game,game,game,game,game.check,lambda *a,**k:None,panel=Panel(game))


def test_automated_map_setup_confirms_target_before_returning_a_route():
    g=FakeMap();result=targeter(g).set_target(g.target)
    assert result['map_marker_id']=='17' and result['tracking_id']=='1234'
    assert result['saved_target']==g.target and not result['collection_complete']
    assert not g.opened and not g.keys and not g.buttons
    assert not any(e.get('key') in ('w','a','s','d','f') for e in g.events)


def test_matching_existing_marker_is_reused_without_creating_a_duplicate():
    g=FakeMap();g.markers['17']=g.target
    targeter(g).set_target(g.target)
    assert not g.add and len(g.markers)==1


def test_matching_active_guidance_is_not_toggled_off():
    g=FakeMap();g.markers['17']=g.target;g.tracked=True
    targeter(g).set_target(g.target)
    assert g.track_clicks==0


def test_preexisting_saved_marker_popup_is_closed_before_target_click():
    g=FakeMap();g.opened=True;g.spot=True;g.markers['17']=g.target
    targeter(g).set_target(g.target)
    downs=[e for e in g.events if e['type'] in ('key_down','mouse_down')]
    assert downs[0]=={'type':'mouse_down','button':'left'}
    assert sum(e.get('key')=='m' and e['type']=='key_down' for e in g.events)==1


def test_wrong_hold_readback_is_never_saved():
    g=FakeMap();g.wrong_hold=True
    with pytest.raises(ValueError,match='未保存'):targeter(g).set_target(g.target)
    assert not g.markers and not g.buttons and not g.keys


def test_native_redirection_cannot_be_accepted_as_the_saved_collection_point():
    g=FakeMap();g.wrong_track=True
    with pytest.raises(ValueError,match='未确认'):targeter(g).set_target(g.target)
    assert not g.keys and not g.buttons


def test_cursor_must_reach_the_pixel_before_pressing_a_mouse_button():
    g=FakeMap();g.wrong_cursor=True
    with pytest.raises(ValueError,match='鼠标未到达'):targeter(g).set_target(g.target)
    assert not any(e['type']=='mouse_down' for e in g.events)


def test_stop_during_long_press_releases_mouse_without_saving():
    g=FakeMap();g.stop_at=.5
    with pytest.raises(RuntimeError,match='stopped'):targeter(g).set_target(g.target)
    assert not g.buttons and not g.keys and not g.markers


def test_preexisting_unsaved_dialog_is_not_overwritten():
    g=FakeMap();g.opened=True;g.add=True
    with pytest.raises(ValueError,match='尚未保存'):targeter(g).set_target(g.target)
    assert not g.events


@pytest.mark.parametrize('field,value',[
    ('settle_seconds',0.),('hold_seconds',float('nan')),
    ('action_timeout',-1.),('world_tolerance',float('inf')),
    ('max_zoom_steps',True),('max_zoom_steps',-1),('pixel_margin',-1.),
])
def test_invalid_ui_policy_is_rejected_before_it_can_loop_or_accept_nan(field,value):
    with pytest.raises(ValueError):MapTargetPolicy(**{field:value})


def test_changed_dialog_identity_is_rejected_before_saving():
    g=FakeMap();t=targeter(g);original=t.panel.dialog;calls=0
    def dialog(*args,**kwargs):
        nonlocal calls
        d=original(*args,**kwargs)
        if d['state']=='open':
            calls+=1
            if calls>1:d['usage_id']='99'
        return d
    t.panel.dialog=dialog
    with pytest.raises(ValueError,match='点击前发生变化'):t.set_target(g.target)
    assert not g.markers and not g.buttons and not g.keys


def test_zoom_positions_cursor_over_map_before_sending_wheel():
    g=FakeMap();g.opened=True;g.target=(10.,2.,300.);g.scale=2.
    t=targeter(g);t.options=MapTargetPolicy(max_pan_steps=0);original=g.execute
    def execute(event):
        if event['type']=='scroll' and 6<g.cursor[0]<994 and 6<g.cursor[1]<994:
            g.scale=1.
        original(event)
    g.execute=execute
    t.panel.geometry=lambda size:MapView('s','v',tuple(size),(1000.,1000.),(0.,0.),
        g.scale,1.,g.time,True,True,(0.,0.,1000.,1000.))
    plan=t.prepare(g.target)
    assert plan.pixel==(510.,200.)
    wheel=next(i for i,e in enumerate(g.events) if e['type']=='scroll')
    assert g.events[wheel-1]['type']=='cursor_move'


def test_open_timeout_names_the_stage_and_last_observed_state():
    g=FakeMap();original=g.execute
    def execute(event):
        original(event)
        if event.get('key')=='m' or event['type']=='mouse_up':g.opened=False
    g.execute=execute
    with pytest.raises(ValueError,match='打开大地图.*closed'):targeter(g).set_target(g.target)
    assert not g.keys and not g.buttons


def test_m_opens_map_but_owned_back_button_closes_it():
    g=FakeMap();g.m_open_only=True
    result=targeter(g).set_target(g.target)
    assert result['map_marker_id']=='17' and not g.opened
    assert sum(e.get('key')=='m' and e['type']=='key_down' for e in g.events)==1


def test_hidden_pointer_before_map_is_opened_does_not_require_alt_or_a_click():
    g=FakeMap();original=g.pointer
    g.pointer=lambda:original() if g.opened else (500,500,False)
    result=targeter(g).set_target(g.target)
    first=next(e for e in g.events if e['type'] in ('key_down','mouse_down','cursor_move'))
    assert first=={'type':'key_down','key':'m'}
    assert result['map_marker_id']=='17' and not g.opened
    assert not any(e.get('key') in ('alt','alt_l','alt_r') for e in g.events)


def test_already_open_map_is_not_toggled_by_m():
    g=FakeMap();g.opened=True
    targeter(g).set_target(g.target)
    assert not any(e.get('key')=='m' for e in g.events)


def test_pointer_still_hidden_in_map_never_presses_a_mouse_button():
    g=FakeMap();g.pointer=lambda:(500,500,False)
    with pytest.raises(ValueError,match='鼠标未到达'):targeter(g).set_target(g.target)
    assert not any(e['type']=='mouse_down' for e in g.events)
    assert not g.keys and not g.buttons


def test_stop_while_opening_map_releases_m_without_clicking():
    g=FakeMap();g.stop_at=.04
    with pytest.raises(RuntimeError,match='stopped'):targeter(g).set_target(g.target)
    assert not g.keys and not g.buttons
    assert not any(e['type']=='mouse_down' for e in g.events)
