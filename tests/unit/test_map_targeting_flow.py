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
        if kind=='scroll':self.zoom_fraction=max(0.,getattr(self,'zoom_fraction',0.)-.5)
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
            elif (x,y)==(75,75):self.zoom_fraction=max(0.,getattr(self,'zoom_fraction',0.)-.5)
            elif (x,y)==(250,250):self.add=False;self.spot=False
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
    def snapshot(self):return {'state':'projection_unverified' if self.g.opened else 'closed',
                              'scene_context':'s','view_identity':'v'}
    def geometry(self,size):
        if not self.g.opened:raise ValueError('map closed')
        return MapView('s','v',tuple(size),(1000.,1000.),(0.,0.),1.,1.,self.g.time,True,True,(0.,0.,1000.,1000.))
    def custom_markers(self):return dict(self.g.markers)
    def zoom_state(self,size,*,include_button=True):
        return dict(state='open',kind='map_zoom',captured_at=self.g.time,scene_context='s',
            view_identity='v',fraction=getattr(self.g,'zoom_fraction',0.),buttons={'decrease':(65,65,85,85)})
    def overlays(self,size):
        return dict(popups=('add',) if self.g.add else ('spot',) if self.g.spot else (),
                    blank_points=((250.,250.),))
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


@pytest.mark.parametrize('distance,wrong_stop', [(49.9, False), (50., False), (50., True)])
def test_bus_target_uses_nearest_station_to_first_waypoint(distance, wrong_stop):
    game = FakeMap()
    game.opened = True
    game.read_for_playback = lambda: (10.-distance, 2., 20.)
    stop = (11., 0., 20.)
    game.guidance = lambda: dict(state='ready', context='module', scene_context='s',
        tracking_id='1234', target=stop, path=((0., 0., 0.), stop), captured_at=game.time)
    original = game.execute

    def execute(event):
        original(event)
        if event['type'] == 'mouse_up' and game.cursor[:2] == (511, 480):
            game.spot = True

    game.execute = execute

    class BusPanel(Panel):
        def snapshot(self):
            return dict(super().snapshot(), other_region_view=False)

        def bus_stations(self):
            return {'13': (-39., 0., 20.), '21': stop}

        def dialog(self, size, kind='add'):
            value = super().dialog(size, 'spot')
            return dict(value, kind='bus', usage_id='13' if wrong_stop else '21', category=1,
                        view_identity='v', track_can_start=True)

    task = targeter(game)
    task.panel = BusPanel(game)
    task.started = game.monotonic()
    task.map_scope = ('s', 'v')
    task.start_lease()
    try:
        if wrong_stop:
            with pytest.raises(ValueError, match='不是本次选中的公交站'):
                task.track_bus_for_route_start(game.target)
        else:
            result = task.track_bus_for_route_start(game.target)
            assert result['route_first_point'] == list(game.target)
            assert result.get('map_bus_id') == ('21' if distance >= 50 else None)
    finally:
        task.lease.close()
    assert game.track_clicks == (1 if distance >= 50 and not wrong_stop else 0)
    assert game.opened and not game.buttons and not game.markers


def test_center_island_uses_map_canvas_center_and_preserves_open_map():
    from dataclasses import replace

    game = FakeMap()
    game.opened = True
    game.pan = (-120., 80.)
    click_start = None
    execute = game.execute

    def execute_drag(event):
        nonlocal click_start
        execute(event)
        if event['type'] == 'mouse_down':
            click_start = game.cursor[:2]
        elif event['type'] == 'mouse_up':
            x, y = game.cursor[:2]
            game.pan = (game.pan[0] + x - click_start[0], game.pan[1] - y + click_start[1])

    game.execute = execute_drag
    task = targeter(game)
    original = task.panel.geometry
    task.panel.geometry = lambda size: replace(original(size), pan=game.pan)
    task.started = game.monotonic()
    task.map_scope = ('s', 'v')
    task.start_lease()
    try:
        result = task.center_island(task.panel.geometry(task.size))
    finally:
        task.lease.close()
    assert result == {'map_center_pixel': (500., 500.), 'map_center_drags': 1}
    assert game.opened and not game.buttons and not game.markers and not game.tracked


@pytest.mark.parametrize('initial_zoom,ack,marker,expected_clicks', [
    (.5, True, None, 1), (.5, False, None, 1), (0., True, None, 0),
    (.5, True, 'new', 3), (.5, True, 'existing', 1), (.5, True, 'wrong', 2),
    (.5, True, 'cursor_drift', 0),
])
def test_open_map_and_verify_one_minus_click(initial_zoom, ack, marker, expected_clicks):
    from dataclasses import replace

    game = FakeMap()
    game.zoom_fraction = initial_zoom
    game.wrong_hold = marker == 'wrong'
    if marker == 'existing':
        game.markers['17'] = game.target
    execute = game.execute

    def execute_zoom(event):
        execute(event)
        if event['type'] == 'mouse_up' and game.cursor[:2] == (75, 69) and ack:
            game.zoom_fraction = 0.

    game.execute = execute_zoom
    if marker == 'cursor_drift':
        pointer_calls = iter(((75, 69, True), (75, 79, True)))
        game.pointer = lambda: next(pointer_calls)

    class ZoomPanel(Panel):
        def snapshot(self):
            return dict(super().snapshot(), other_region_view=False, map_scale=1 + self.g.zoom_fraction)

        def geometry(self, size):
            return replace(super().geometry(size), scale=1 + self.g.zoom_fraction)

        def zoom_state(self, size, *, include_button=True):
            state = super().zoom_state(size, include_button=include_button)
            return dict(state, map_scale=1 + self.g.zoom_fraction,
                        buttons=state['buttons'] if self.g.zoom_fraction else {})

        def dialog(self, size, kind='add'):
            return dict(super().dialog(size, kind), view_identity='v')

    task = targeter(game)
    task.panel = ZoomPanel(game)
    original_geometry = task.panel.geometry
    geometry_reads = []

    def geometry_after_open(size):
        from utils.core.map_targeting import MapLayoutUpdating
        if not geometry_reads:
            geometry_reads.append(True)
            raise MapLayoutUpdating('界面变换数值正在更新')
        return original_geometry(size)

    task.panel.geometry = geometry_after_open
    if marker == 'cursor_drift':
        with pytest.raises(ValueError, match='复核后鼠标已偏离'):
            task.open_and_zoom_once()
    elif marker == 'wrong':
        with pytest.raises(ValueError, match='未保存标记'):
            task.open_and_zoom_once(game.target)
    elif marker:
        result = task.open_and_zoom_once(game.target)
        assert result['target'] == list(game.target)
        assert result['map_marker_id'] == '17'
        assert result['map_marker_reused'] is (marker == 'existing')
    elif initial_zoom and ack:
        assert task.open_and_zoom_once() == {'map_zoom_before': 1.5, 'map_zoom_after': 1.0}
    else:
        with pytest.raises(ValueError, match='最小缩放|未确认缩小'):
            task.open_and_zoom_once()
    assert game.opened and not game.buttons and not game.keys
    assert sum(event['type'] == 'mouse_down' for event in game.events) == expected_clicks
    if expected_clicks == 1:
        assert game.cursor[:2] == (75, 69)
    assert not any(event['type'] == 'scroll' for event in game.events)
    assert bool(game.markers) is (marker in ('new', 'existing'))
    assert not game.tracked and not game.track_clicks


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
    assert not any(e.get('key')=='m' for e in g.events)


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


def test_preexisting_unsaved_dialog_is_dismissed_without_saving_its_position():
    g=FakeMap();g.opened=True;g.add=True
    targeter(g).set_target(g.target)
    assert list(g.markers.values())==[g.target]
    assert next(e for e in g.events if e['type']=='cursor_move')['x']==250


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


def test_minimum_zoom_is_confirmed_before_target_projection():
    from utils.drivers.guidance_input_lease import GuidanceInputLease
    g=FakeMap();g.opened=True;g.target=(10.,2.,300.);g.scale=2.;g.zoom_fraction=1.
    t=targeter(g);t.options=MapTargetPolicy(max_pan_steps=0);original=g.execute
    t.lease=GuidanceInputLease(g,g,g,g.size(),g.monotonic)
    def execute(event):
        original(event)
        if event['type']=='scroll':g.scale=1.
    g.execute=execute
    t.panel.geometry=lambda size:MapView('s','v',tuple(size),(1000.,1000.),(0.,0.),
        g.scale,1.,g.time,True,True,(0.,0.,1000.,1000.))
    plan=t.prepare(g.target)
    assert plan.pixel==(510.,200.)
    assert any(e['type']=='scroll' and e['dy']<0 for e in g.events)
    assert not any(e.get('x')==75 and e.get('y')==75 for e in g.events)


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
