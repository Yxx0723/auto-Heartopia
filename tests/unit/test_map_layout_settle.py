"""Read-only layout instability must settle without repeating map input."""
from dataclasses import replace
import pytest
from tests.unit.test_map_targeting_flow import FakeMap,targeter
from utils.core import map_targeting


def updating(scope=('s','v')):
    cls=getattr(map_targeting,'MapLayoutUpdating',ValueError)
    return cls('地图布局读取期间视图发生变化',scope=scope) if cls is not ValueError else cls('地图布局读取期间视图发生变化')


def test_layout_read_changing_once_after_wheel_does_not_abort_setup():
    g=FakeMap();g.zoom_fraction=1.;t=targeter(g);old=t.panel.overlays;errors=[]
    def overlays(size):
        if any(e['type']=='scroll' for e in g.events) and not errors:
            errors.append(1);raise updating()
        return old(size)
    t.panel.overlays=overlays
    assert t.set_target(g.target)['map_marker_id']=='17'
    assert len(errors)==1 and not g.keys and not g.buttons


def test_zoom_waits_for_two_equal_layouts_before_next_input():
    g=FakeMap();g.zoom_fraction=1.;t=targeter(g);old=t.panel.geometry;reads=[]
    def geometry(size):
        v=old(size)
        if any(e['type']=='scroll' for e in g.events) and len(reads)<3:
            reads.append(1);return replace(v,pan=(float(len(reads)),0.))
        return v
    t.panel.geometry=geometry
    assert t.set_target(g.target)['map_marker_id']=='17'
    assert len(reads)==3


@pytest.mark.parametrize('fault',['scene','identity','closed'])
def test_pending_layout_cannot_resume_in_a_changed_context(fault):
    g=FakeMap();t=targeter(g);old=t.panel.snapshot;calls=[]
    def overlays(size):calls.append(1);raise updating()
    def snapshot():
        v=old()
        if calls:
            v['scene_context' if fault=='scene' else 'view_identity' if fault=='identity' else 'state']='other'
        return v
    t.panel.overlays=overlays;t.panel.snapshot=snapshot
    with pytest.raises(ValueError):t.set_target(g.target)
    assert not any(e['type'] in ('scroll','mouse_down') for e in g.events)


@pytest.mark.parametrize('stop',[False,True])
def test_pending_read_has_a_deadline_and_obeys_stop(stop):
    g=FakeMap();t=targeter(g);g.opened=True
    if stop:g.stop_at=.2
    def overlays(size):raise updating()
    t.panel.overlays=overlays
    with pytest.raises(RuntimeError if stop else ValueError):t.set_target(g.target)
    assert g.time<7. and not g.markers and not g.buttons and not g.keys


def test_unrelated_layout_error_is_not_retried():
    g=FakeMap();g.opened=True;t=targeter(g);calls=[]
    def overlays(size):calls.append(1);raise ValueError('版本不支持')
    t.panel.overlays=overlays
    with pytest.raises(ValueError,match='版本不支持'):t.set_target(g.target)
    assert len(calls)==1


def test_old_layout_is_discarded_after_a_long_but_bounded_update():
    g=FakeMap();g.opened=True;t=targeter(g);old=t.panel.geometry;reads=[]
    def geometry(size):
        reads.append(1)
        if len(reads)==2:g.time+=1.2;raise updating()
        return old(size)
    t.panel.geometry=geometry
    assert t.set_target(g.target)['map_marker_id']=='17'
    assert not g.buttons and not g.keys


@pytest.mark.parametrize('change',['scale','scene','owner'])
def test_reader_only_classifies_value_changes_as_pending(monkeypatch,change):
    from unittest.mock import Mock
    from tests.unit.test_map_panel import reader
    r,m,_=reader();ref=m.ref.side_effect
    m.ref.side_effect=lambda obj,name:600 if name=='<CanvasRect>k__BackingField' else 601 if name=='sketch_t' else ref(obj,name)
    native=Mock();native.rect.return_value=dict(rect=(-960.,-540.,1920.,1080.),anchored_position=(0.,0.))
    native.transforms.return_value=(((0.,0.,0.),(0.,0.,0.,1.),(1.19,1.19,1.)),)
    monkeypatch.setattr('utils.drivers.native_ui_layout.NativeUiLayout',Mock(return_value=native))
    first=r.snapshot();owner=r.owner();second={**first,'map_scale':1.2}
    if change=='scene':second={**first,'scene_context':'other'}
    r.snapshot=Mock(side_effect=[first,second]);r.owner=Mock(side_effect=[owner,(*owner[:-1],999)] if change=='owner' else [owner,owner])
    with pytest.raises(ValueError) as error:r.geometry((1366,768))
    cls=getattr(map_targeting,'MapLayoutUpdating',None)
    assert cls is not None and isinstance(error.value,cls)==(change=='scale')
