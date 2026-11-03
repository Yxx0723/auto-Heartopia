"""Minimum zoom must not depend on a small button's visual alignment."""
from dataclasses import replace
import pytest
from tests.unit.test_map_targeting_flow import FakeMap,targeter


def wheel_map():
    g=FakeMap();g.zoom_fraction=1.;t=targeter(g);original=g.execute
    def execute(e):
        original(e)
        if e['type']=='scroll':g.zoom_fraction=max(0.,g.zoom_fraction-.5)
        if e['type']=='mouse_up' and g.cursor[:2]==(75,75):
            g.zoom_fraction=1.  # The visual minus is not at this cached center.
    g.execute=execute
    return g,t


def test_minimum_zoom_does_not_click_misaligned_minus():
    g,t=wheel_map();result=t.set_target(g.target)
    assert result['map_marker_id']=='17' and g.zoom_fraction==0.
    assert any(e['type']=='scroll' and e['dy']<0 for e in g.events)
    assert not any(e.get('x')==75 and e.get('y')==75 for e in g.events)


@pytest.mark.parametrize('fault',['pointer','scene','covered'])
def test_wheel_requires_arrival_and_stable_unobstructed_map(fault):
    g,t=wheel_map();original=t.panel.geometry
    if fault=='pointer':g.wrong_cursor=True
    def geometry(size):
        v=original(size)
        if fault=='covered':return replace(v,blocked_rects=((0.,0.,1000.,1000.),))
        if fault=='scene' and any(e['type']=='cursor_move' for e in g.events):return replace(v,scene='new')
        return v
    t.panel.geometry=geometry
    with pytest.raises(ValueError):t.set_target(g.target)
    assert not any(e['type']=='scroll' for e in g.events)
    assert not g.markers and not g.buttons


def test_wheel_zoom_still_requires_slider_progress():
    g,t=wheel_map();original=g.execute
    def execute(e):
        original(e)
        if e['type']=='scroll':g.zoom_fraction=1.
    g.execute=execute
    with pytest.raises(ValueError,match='最小'):t.set_target(g.target)
    assert not g.markers and not g.buttons
