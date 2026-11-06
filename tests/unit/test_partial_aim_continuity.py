from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tests.unit.test_coordinate_player import manual_points
from tests.unit.test_memory_quality import ready_scene
from tests.unit.test_coordinate_navigation import box
from utils.core.collection_navigation import CollectionNavigator
from utils.core.coordinate_navigation import NavigationWait
from utils.drivers.route_path_memory import RoutePathMemory


def navigator(tmp_path):
    source=[ready_scene()]
    nav=CollectionNavigator(lambda:source[0],clock=lambda:0.,scene_clock=lambda:0.,water_areas=())
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(8,0,0)]))
    assert memory.remember(0,[[20,0,0],[16,0,0],[12,0,0],[8,0,0]])
    nav.path_memory=memory;nav.set_waypoint(0)
    candidates=Mock(return_value=[{'start':(0,0,0),'end':(3,0,0),'failures':0}])
    nav.traversal_memory=SimpleNamespace(candidates=candidates)
    return nav,source,candidates


def test_retained_partial_aim_does_not_replan_final_then_local_every_tick(tmp_path):
    nav,_,candidates=navigator(tmp_path)
    assert nav.next_target((0,0,0),(8,0,0))==(3.,0.,0.)
    plans=nav.stats['plans']
    for i in range(10):nav.next_target((i*.1,0,0),(8,0,0))
    assert nav.stats['plans']==plans and candidates.call_count==1
    assert nav.last_decision['retained_aim'] is True


def test_stale_snapshot_stops_retained_aim_instead_of_extending_it(tmp_path):
    nav,_,_=navigator(tmp_path);nav.next_target((0,0,0),(8,0,0))
    nav.scene_clock=lambda:10.
    with pytest.raises(NavigationWait) as error:nav.next_target((.1,0,0),(8,0,0))
    assert error.value.reason=='scene'


def test_new_obstacle_revalidates_and_replans_retained_aim(tmp_path):
    nav,source,candidates=navigator(tmp_path);nav.next_target((0,0,0),(8,0,0))
    source[0]=ready_scene(box(1,1.5,-1,1));release=Mock()
    nav.next_target((.1,0,0),(8,0,0),before_plan=release)
    assert release.called and candidates.call_count==1
    assert nav.goal==(3,0,0)
    previous=(.1,0.)
    for p in nav.path:
        assert nav.obstacles.clear(previous,(p[0],p[2]),escape=True)
        previous=(p[0],p[2])


def test_target_change_and_recovery_discard_retained_aim(tmp_path):
    nav,_,_=navigator(tmp_path);nav.next_target((0,0,0),(8,0,0))
    nav.blocked((0,0,0),(8,0,0))
    assert nav.partial_aim is None
    nav.next_target((0,0,0),(9,0,0))
    assert nav.partial_aim is None or nav.partial_aim['key'][1]==(9,0,0)
