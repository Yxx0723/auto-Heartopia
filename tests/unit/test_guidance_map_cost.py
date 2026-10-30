"""Conservative local-build broad phase and permit attribution; no game input."""
from unittest.mock import patch
from tests.unit.test_coordinate_navigation import scene,box
from utils.core.guidance_navigation import GuidanceNavigator


def test_far_colliders_do_not_enter_expensive_geometry_key_work():
    from utils.core.coordinate_navigation import geometry_key
    source=scene(*[box(100+i,101+i,100,101) for i in range(100)])
    nav=GuidanceNavigator(lambda:source,water_areas=())
    with patch('utils.core.coordinate_navigation.geometry_key',wraps=geometry_key) as key:
        nav.next_target((0,0,0),(0,0,6))
    assert key.call_count==0


def test_far_reference_faces_do_not_enter_height_clipping():
    from utils.core.navigation_reference import ReferenceRisks,_clip_height
    reference={'layers':[{'kind':'mountain','vertices':[(100,0,100),(101,2,100),(100,2,101)],'triangles':[(0,1,2)]}]}
    with patch('utils.core.navigation_reference._clip_height',wraps=_clip_height) as clip:
        assert not ReferenceRisks(reference,0.,(0,0,0),10.).entries
    assert clip.call_count==0


import random
import pytest
from copy import deepcopy
from utils.core.local_collision_scope import local_collision_scene
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.navigation_reference import ReferenceRisks
from utils.drivers.guidance_input_lease import GuidanceInputLease
from tests.unit.test_native_guidance_follow import guide_game


def test_scope_preserves_original_scene_and_keeps_crossing_huge_colliders():
    source=scene(box(-100,100,3,4),box(100,101,100,101));before=deepcopy(source)
    selected=local_collision_scene(source,(0,0,0),32.)
    assert selected['colliders']==source['colliders'][:1]
    assert source==before
    assert not ObstacleMap(selected,(0,0,0),water_areas=()).clear((0,0),(0,6))


@pytest.mark.parametrize('position',[(0,0,0),(25,1,-20),(-25,-1,20)])
def test_filtered_map_exactly_matches_original_local_polygons_and_checks(position):
    rng=random.Random(91)
    shapes=[box(x:=rng.uniform(-120,120),x+rng.uniform(.01,30),
                z:=rng.uniform(-120,120),z+rng.uniform(.01,30),(-2,3)) for _ in range(200)]
    shapes.append({'polygon':[[0,0],[4,0]],'height':[-2,3],
                   'projection_kind':'segment','surface_kind':'zero-x-box-plane'})
    source=scene(*shapes);source['quality']={'navigation_eligible':True}
    water=([[10,10],[20,10],[20,20],[10,20]],)
    full=ObstacleMap(source,position,water_areas=water)
    filtered=ObstacleMap(local_collision_scene(source,position,32.),position,water_areas=water)
    assert full.polygons==filtered.polygons and full.water_polygon_ids==filtered.water_polygon_ids
    assert full.bridge_decks==filtered.bridge_decks
    for _ in range(30):
        a=(position[0]+rng.uniform(-30,30),position[2]+rng.uniform(-30,30))
        b=(position[0]+rng.uniform(-30,30),position[2]+rng.uniform(-30,30))
        assert full.clear(a,b)==filtered.clear(a,b)


def test_search_radius_growth_reselects_distant_wall_instead_of_reusing_old_subset():
    source=scene(box(65,66,-4,4))
    assert not local_collision_scene(source,(0,0,0),32.)['colliders']
    assert local_collision_scene(source,(0,0,0),80.)['colliders']==source['colliders']


def test_scope_retains_invalid_data_for_existing_validator():
    bad={'polygon':[[float('nan'),0],[1,0],[1,1]],'height':[0,3]}
    source=scene(bad)
    assert local_collision_scene(source,(0,0,0),32.)['colliders']==[bad]


@pytest.mark.parametrize('kind',['mountain','water','bridge'])
def test_reference_broad_phase_matches_wide_map_then_exact_local_clipping(kind):
    # Include vertical/degenerate projections and triangles straddling the edge.
    vertices=[(10.4,0,0),(11.4,2,1),(10.4,2,0),(-20,0,-20),(20,2,-20),(0,2,20),
              (100,0,100),(102,2,100),(101,2,102)]
    ref={'layers':[{'kind':kind,'vertices':vertices,'triangles':[(0,1,2),(3,4,5),(6,7,8)]}]}
    wide=ReferenceRisks(ref,0.,(0,0,0),200.)
    local=ReferenceRisks(ref,0.,(0,0,0),10.)
    expected=[]
    for entry in wide.entries:
        polygon,pad=entry[2:]
        xs,zs=zip(*polygon)
        if max(xs)+pad>=-10 and min(xs)-pad<=10 and max(zs)+pad>=-10 and min(zs)-pad<=10:
            expected.append(entry)
    assert local.entries==expected
    for a,b in [((-9,-9),(9,9)),((9,0),(10,0)),((0,-9),(0,9))]:
        assert local.cost(a,b)==pytest.approx(wide.cost(a,b))


def test_phase_updates_do_not_renew_permit_and_expiry_captures_active_read():
    game=guide_game();lease=GuidanceInputLease(game,game,game,game.size(),game.monotonic)
    game.keys={'w'};lease.arm(.1)
    game.time=.05;lease.set_phase('guidance_read');assert lease.deadline==.1
    game.time=.2;lease.poll()
    m=lease.metrics();last=m['recent_releases'][-1]
    assert last['reason']=='lease_expired' and last['phase']=='guidance_read'
    assert last['phase_seconds']==pytest.approx(.15)
    assert not game.keys
    lease.close()


def test_public_follower_reports_release_stage_and_retains_stop_protection():
    from tests.unit.test_guidance_route_cache import cached_game
    from tests.unit.test_native_guidance_follow import run
    game=cached_game();game.stop_at=.8;source=scene();messages=[]
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    run(game,lambda s,m,**e:messages.append(e),nav)
    assert not game.keys
    assert any('current_phase' in e.get('input_continuity',{}) for e in messages)
