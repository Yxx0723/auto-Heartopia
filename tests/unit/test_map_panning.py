"""Directional map panning uses layout/readback, never character movement."""
from dataclasses import replace

import pytest

from tests.unit.test_map_targeting_flow import FakeMap,targeter
from utils.core.map_targeting import MapView
from utils.core.map_target_policy import MapTargetPolicy
from utils.drivers.guidance_input_lease import GuidanceInputLease


def panning_map():
    g=FakeMap();g.opened=True;g.pan=(0.,0.);g.pan_enabled=True;g.wrong_pan=False
    t=targeter(g);original=g.execute
    def execute(event):
        if event['type']=='cursor_move' and 'left' in g.buttons and g.pan_enabled:
            dx,dy=event['x']-g.cursor[0],event['y']-g.cursor[1]
            factor=-1 if g.wrong_pan else 1
            g.pan=(g.pan[0]+factor*dx,g.pan[1]-factor*dy)
        original(event)
    g.execute=execute
    t.panel.geometry=lambda size:MapView('s','v',tuple(size),(1000.,1000.),g.pan,
        1.,1.,g.time,True,True,(100.,100.,900.,900.))
    t.lease=GuidanceInputLease(g,g,g,g.size(),g.monotonic)
    return g,t


@pytest.mark.parametrize('target',[(0.,2.,800.),(0.,2.,-800.),(800.,2.,0.),(-800.,2.,0.)])
def test_target_outside_each_edge_is_brought_into_view_without_zoom(target):
    g,t=panning_map();plan=t.prepare(target)
    x,y=plan.pixel
    assert 106<x<894 and 106<y<894
    assert not any(e['type']=='scroll' for e in g.events)
    assert any(e['type']=='mouse_down' for e in g.events)
    assert not g.buttons and not g.keys and not g.add
    assert not any(e.get('key') in ('w','a','s','d','f') for e in g.events)


def test_drag_moves_in_the_screen_direction_needed_for_a_target_above():
    g,t=panning_map();t.prepare((0.,2.,800.))
    assert g.pan[1]<0  # screen drag down moves canvas/world pan Y down.


def test_stop_during_drag_releases_the_mouse_and_never_marks():
    g,t=panning_map();g.stop_at=.2
    with pytest.raises(RuntimeError,match='stopped'):t.prepare((0.,2.,800.))
    assert not g.buttons and not g.keys and not g.markers


def test_opposite_pan_readback_is_not_used_to_click_the_target():
    g,t=panning_map();g.wrong_pan=True
    with pytest.raises(ValueError,match='平移.*目标'):t.prepare((0.,2.,800.))
    assert not g.buttons and not g.markers


def test_no_pan_progress_has_a_finite_attempt_limit():
    g,t=panning_map();g.pan_enabled=False
    t.options=replace(t.options,max_pan_steps=2,max_zoom_steps=0)
    with pytest.raises(ValueError):t.prepare((0.,2.,800.))
    assert g.time<10. and not g.buttons and not g.markers


@pytest.mark.parametrize('kw',[{'max_pan_steps':True},{'max_pan_steps':-1},
    {'drag_seconds':float('nan')},{'drag_steps':0},{'drag_steps':True}])
def test_invalid_pan_policy_is_rejected(kw):
    with pytest.raises(ValueError):MapTargetPolicy(**kw)


@pytest.mark.parametrize('fault',['scene','scale','controls'])
def test_drag_does_not_use_changed_context_or_cross_a_control(fault):
    g,t=panning_map();original=t.panel.geometry
    def geometry(size):
        view=original(size)
        if fault=='controls':return replace(view,blocked_rects=((450.,450.,550.,550.),))
        if g.pan!=(0.,0.):return replace(view,scene='other') if fault=='scene' else replace(view,scale=2.)
        return view
    t.panel.geometry=geometry
    with pytest.raises(ValueError):t.prepare((0.,2.,800.))
    assert not g.buttons and not g.markers
    if fault=='controls':assert not any(e['type']=='mouse_down' for e in g.events)


def test_full_setup_can_pan_then_save_track_verify_and_close():
    g,t=panning_map();g.target=(10.,2.,800.);original=g.execute;dialog=t.panel.dialog
    def execute(event):
        was_add=g.add;held=g.time-g.held_at
        original(event)
        if event['type']=='mouse_up' and held<1. and not was_add and g.opened and g.markers:
            pixel=t.panel.geometry(g.size()).world_to_client(g.target)
            if sum((a-b)**2 for a,b in zip(g.cursor[:2],pixel))<4:g.spot=True
    g.execute=execute
    def actual_dialog(*args,**kwargs):
        value=dialog(*args,**kwargs)
        if value['state']=='open':value['position_xz']=(g.target[0],g.target[2])
        return value
    t.panel.dialog=actual_dialog
    g.guidance=lambda:dict(state='ready' if g.tracked else 'inactive',context='module',
        scene_context='s',tracking_id='1234',target=(10.,0.,800.),
        path=((0.,0.,0.),(10.,0.,800.)),captured_at=g.time)
    result=t.set_target(g.target)
    assert result['saved_target']==g.target and result['map_marker_id']=='17'
    assert not g.opened and not g.buttons and not g.keys and not result['collection_complete']
