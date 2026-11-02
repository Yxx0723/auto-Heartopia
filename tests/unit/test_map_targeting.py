"""Map targeting contracts; no real game input or saved-route changes."""
from dataclasses import replace
import math

import pytest

from utils.core.map_targeting import MapView, plan_map_click, confirm_map_target


def view(at=10., **kw):
    data=dict(scene='world-a',identity='map-a',client_size=(1366,768),canvas_size=(1920.,1080.),
              pan=(100.,-50.),scale=1.2,ratio=5.,captured_at=at,opened=True,verified=True,
              map_rect=(100.,100.,1266.,680.),blocked_rects=())
    data.update(kw)
    return MapView(**data)


def plan():
    return plan_map_click((10.,22.,20.),view(),view(10.1),10.15,max_age=.5,margin=8.)


def guide(at=10.3, **kw):
    d=dict(state='ready',context='module-a',scene_context='world-a',tracking_id='42',
           target=(10.,0.,20.),path=((0.,0.,0.),(10.,0.,20.)),captured_at=at)
    d.update(kw)
    return d


def test_world_screen_round_trip_keeps_saved_height_and_never_clamps():
    v=view();p=plan()
    assert p.target==(10.,22.,20.)
    assert v.client_to_world(p.pixel)==pytest.approx((10.,20.))
    assert p.pixel[0]>1366/2 and p.pixel[1]<768/2


@pytest.mark.parametrize('field,value', [('pan',(101.,-50.)),('scale',1.3),('ratio',4.),
    ('client_size',(1280,720)),('canvas_size',(2000.,1080.)),('scene','world-b'),
    ('identity','map-b'),('map_rect',(0.,0.,1000.,600.))])
def test_changed_view_cannot_reuse_a_click(field,value):
    with pytest.raises(ValueError):
        plan_map_click((10,22,20),view(),replace(view(10.1),**{field:value}),10.15,max_age=.5,margin=8.)


@pytest.mark.parametrize('fault',['closed','unverified','old','future','same_sample','nan','zero_scale','outside','blocked'])
def test_invalid_views_and_unsafe_pixels_never_produce_input_intent(fault):
    a,b,target=view(),view(10.1),(10,22,20)
    if fault=='closed': b=replace(b,opened=False)
    elif fault=='unverified': b=replace(b,verified=False)
    elif fault=='old': a,b=view(1.),view(1.1)
    elif fault=='future': b=view(11.)
    elif fault=='same_sample': b=a
    elif fault=='nan': b=replace(b,pan=(math.nan,0.))
    elif fault=='zero_scale': a,b=view(scale=0.),view(10.1,scale=0.)
    elif fault=='outside': target=(100000,22,100000)
    else: a,b=view(blocked_rects=((700,250,900,450),)),view(10.1,blocked_rects=((700,250,900,450),))
    with pytest.raises(ValueError):plan_map_click(target,a,b,10.15,max_age=.5,margin=8.)


def test_target_confirmation_requires_two_fresh_matching_real_ids():
    result=confirm_map_target(plan(),guide(),guide(10.4),10.45,requested_at=10.2,world_tolerance=.5)
    assert result['tracking_id']=='42'
    assert result['saved_target']==(10.,22.,20.)
    assert result['collection_complete'] is False


@pytest.mark.parametrize('change',[dict(target=(12.,0.,20.)),dict(scene_context='world-b'),
    dict(tracking_id='43'),dict(tracking_id=None),dict(captured_at=10.1),dict(state='pending'),
    dict(path=[]),dict(captured_at=11.)])
def test_wrong_old_or_unstable_guidance_never_confirms_a_map_click(change):
    with pytest.raises(ValueError):
        confirm_map_target(plan(),guide(),guide(10.4,**change),10.45,requested_at=10.2,world_tolerance=.5)


def test_display_path_end_is_separate_from_the_saved_collection_target():
    path=((0,0,0),(0,0,5))
    result=confirm_map_target(plan(),guide(path=path),guide(10.4,path=path),10.45,
                              requested_at=10.2,world_tolerance=.5)
    assert result['path_end_gap']>10
    assert not result['collection_complete']
