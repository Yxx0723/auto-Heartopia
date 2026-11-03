"""Minimize the real map before any pan; dismiss incidental map overlays."""
import pytest
from dataclasses import replace

from tests.unit.test_map_targeting_flow import FakeMap,targeter
from utils.core.map_targeting import MapView,blank_map_points


def test_zoom_even_when_target_already_fits_before_marking():
    g=FakeMap();g.zoom_fraction=1.;result=targeter(g).set_target(g.target)
    assert result['map_marker_id']=='17' and g.zoom_fraction==0.
    assert next(i for i,e in enumerate(g.events) if e['type']=='scroll')<next(
        i for i,e in enumerate(g.events) if e.get('x')==510 and e.get('y')==480)


def test_zoom_failure_does_not_proceed_to_pan_or_mark():
    g=FakeMap();g.zoom_fraction=1.;t=targeter(g);original=g.execute
    def execute(e):
        original(e)
        if e['type']=='scroll':g.zoom_fraction=1.
    g.execute=execute
    with pytest.raises(ValueError,match='最小'):t.set_target(g.target)
    assert not g.markers and not g.buttons


def test_map_popup_is_dismissed_before_zoom_and_is_not_saved():
    g=FakeMap();g.opened=True;g.add=True;g.zoom_fraction=1.
    result=targeter(g).set_target(g.target)
    first=next(e for e in g.events if e['type']=='cursor_move')
    assert first['x']==250 and first['y']==250
    assert result['map_marker_id']=='17' and len(g.markers)==1


def test_popup_that_cannot_be_dismissed_has_a_finite_limit():
    g=FakeMap();g.opened=True;g.spot=True;t=targeter(g);original=g.execute
    def execute(e):original(e);g.spot=True
    g.execute=execute
    with pytest.raises(ValueError,match='弹窗'):t.set_target(g.target)
    assert not g.buttons and not g.markers


def test_blank_candidates_avoid_icons_popups_and_known_controls():
    view=MapView('s','v',(1000.,1000.),(1000.,1000.),(0.,0.),1.,1.,1.,True,True,
        (100.,100.,900.,900.),((100.,100.,400.,900.),))
    rects=((400.,100.,900.,500.),)
    points=blank_map_points(view,rects,1.,max_age=1.,margin=6.)
    assert points and all(x>406 and y>506 for x,y in points)
    assert not blank_map_points(view,((0.,0.,1000.,1000.),),1.,max_age=1.,margin=6.)
    with pytest.raises(ValueError):blank_map_points(replace(view,verified=False),rects,1.,max_age=1.,margin=6.)


def test_missing_blank_candidates_never_click_a_popup_button():
    g=FakeMap();g.opened=True;g.spot=True;t=targeter(g)
    t.panel.overlays=lambda size:dict(popups=('spot',),blank_points=())
    with pytest.raises(ValueError,match='空白区域'):t.set_target(g.target)
    assert not any(e['type']=='mouse_down' for e in g.events)


def test_popup_opened_by_a_drag_is_dismissed_before_readback():
    from tests.unit.test_map_panning import panning_map
    g,t=panning_map();original=g.execute
    def execute(e):
        original(e)
        if e['type']=='mouse_up' and g.cursor[:2]!=(250,250):g.spot=True
    g.execute=execute
    original_geometry=t.panel.geometry
    def geometry(size):
        if g.spot:raise ValueError('popup obstructs the map')
        return original_geometry(size)
    t.panel.geometry=geometry
    plan=t.prepare((0.,2.,800.))
    assert plan and not g.spot and not g.buttons
    assert any(e.get('x')==250 and e.get('y')==250 for e in g.events)
