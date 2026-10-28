"""Guidance-only continuity, collision freshness and evidence contracts."""
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_scene_read_recovery import minimal_scene_reader
from tests.unit.test_native_guidance_follow import guide_game, run
from tests.unit.test_coordinate_navigation import scene,box
from utils.drivers.playback_history import RecordingPosition


def test_navigation_scan_skips_display_entity_work_without_skipping_owner_discovery():
    reader=minimal_scene_reader();m=reader.meta
    reader.navigation_only=True
    reader.read_registry=Mock(return_value=[2])
    m.obj.side_effect=lambda at:({'name':'StaticLevelObjectWrap','address':22} if at==22 else {'name':'Hierarchy','namespace':'XDTGame.Core','address':99})
    m.ref.side_effect=lambda at,name:22 if name=='_hierarchyObject' else 0
    m.field.return_value={'offset':16}
    m.data.side_effect=lambda at,n:struct.pack('<3f',0,0,0) if n==12 else struct.pack('<4f',0,0,0,1) if n==16 else struct.pack('<f',1)
    reader.object_category=Mock(return_value='other')
    reader.barrier_owners=Mock(return_value={})
    reader.refresh_live_objects=Mock(return_value=[])
    value=reader.snapshot()
    reader.object_category.assert_not_called()
    assert value['quality']['navigation_eligible']
    assert value['objects']==[]


def test_guidance_semantic_reads_are_recorded_but_attached_failure_is_not():
    from utils.drivers.playback_history import GuidanceRecordingPosition
    reader=Mock();reader.read_for_playback.return_value=(1,2,3)
    history=Mock()
    recorded=GuidanceRecordingPosition(reader,history,lambda:5.)
    assert recorded.read_for_playback()==(1,2,3)
    history.sample.assert_called_once_with((1,2,3),5.)
    reader.read_for_playback.side_effect=ValueError('attached')
    with pytest.raises(ValueError):recorded.read_for_playback()
    assert history.sample.call_count==1


def test_legacy_recording_wrapper_remains_unchanged():
    reader=Mock();history=Mock()
    recorded=RecordingPosition(reader,history,lambda:0.)
    recorded.read_for_playback()
    history.sample.assert_not_called()


def test_quick_guidance_navigation_does_not_release_unchanged_keys_every_tick(monkeypatch):
    from utils.core.guidance_navigation import GuidanceNavigator
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy,'COORD_GUIDANCE_REFRESH_SECONDS',100.)
    game=guide_game();game.stop_at=.45
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    source=scene()
    run(game,navigator=nav)
    downs=[t for t,e in game.events if e['type']=='key_down']
    assert len(downs)==1
    assert not game.keys


def test_guidance_map_rebuild_releases_input_before_work_and_new_wall_is_checked():
    from utils.core.guidance_navigation import GuidanceNavigator
    source=[scene()];released=[]
    nav=GuidanceNavigator(lambda:source[0],water_areas=())
    nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append(1))
    released.clear()
    nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append(1))
    assert not released
    source[0]=scene(box())
    point=nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append(1))
    assert released and point[0]!=0
    assert not nav.obstacles.clear((0,0),(0,6))


def test_guidance_small_blocked_aim_can_select_a_checked_later_route_position():
    game=guide_game()
    from utils.core.guidance_navigation import GuidanceNavigator
    source=scene(box(-.2,.2,1.,1.5))
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    messages=[]
    outcome=run(game,lambda s,m,**e:messages.append((s,m,e)),nav)
    assert outcome['arrived']
    assert not game.keys
    assert any(e.get('guidance_rejoin') for _,_,e in messages)

@pytest.mark.parametrize('fault',['native','managed'])
def test_collision_only_reader_keeps_the_existing_incomplete_collision_gate(fault):
    from tests.unit.test_guidance_planar_scene import plane_reader
    reader=plane_reader((1,2,0) if fault=='native' else (-1,2,0));reader.navigation_only=True
    if fault=='native':reader.native.snapshot.side_effect=ValueError('world changed')
    assert not reader.snapshot()['quality']['navigation_eligible']


def test_guidance_dedicated_worker_is_closed_and_never_uses_the_display_provider(monkeypatch):
    from tests.unit.test_point_playback import FakeWindow,WaitingControls
    from utils.ui.point_playback import PointPlayback
    workers=[]
    class Worker:
        def __init__(self,*a,**k):self.closed=False;workers.append(self)
        def set_pid(self,pid):self.pid=pid
        def start(self):pass
        def navigation_snapshot(self,pid):assert pid==self.pid;return scene()
        def close(self):self.closed=True
    monkeypatch.setattr('utils.ui.point_playback.SceneMapWorker',Worker)
    def runner(*a,**k):
        assert k['navigator'].next_target((0,0,0),(0,0,2))
        return {'arrived':False,'reason':'test stopped'}
    worker=PointPlayback('profile',guidance_runner=runner,window_factory=FakeWindow,
        controls_factory=WaitingControls,device_factory=lambda w:Mock(),
        scene_provider=Mock(side_effect=AssertionError('display provider used')),
        guidance_scene_factory=Mock())
    worker.start_guidance(7);worker.thread.join(2)
    assert not worker.active and workers[0].closed
    worker.scene_provider.assert_not_called()
    worker.close()


def test_checked_continuous_navigation_stops_when_collision_provider_becomes_invalid():
    from utils.core.guidance_navigation import GuidanceNavigator
    game=guide_game();game.stop_at=2.
    nav=GuidanceNavigator(lambda:scene() if game.time<.6 else None,clock=game.monotonic,water_areas=())
    run(game,navigator=nav)
    assert not [(t,e) for t,e in game.events if t>.7 and e['type']=='key_down']
    assert not game.keys


def test_empty_map_does_not_allow_crossing_known_water_during_rejoin():
    from utils.core.guidance_navigation import GuidanceNavigator
    from utils.core.coordinate_navigation import NavigationWait
    nav=GuidanceNavigator(lambda:scene(),water_areas=([[-50,1],[50,1],[50,5],[-50,5]],))
    with pytest.raises(NavigationWait):nav.next_target((0,0,0),(0,0,3))
    assert not nav.obstacles.clear((0,0),(0,6))


def test_failure_explanation_contains_rejected_footprint_without_claiming_contact():
    from utils.core.guidance_navigation import GuidanceNavigator
    nav=GuidanceNavigator(lambda:scene(box()),water_areas=())
    nav.next_target((0,0,0),(0,0,6))
    evidence=nav.explain((0,0,0),(0,0,6))
    assert evidence['start_clear'] and not evidence['direct_clear']
    assert evidence['rejecting_footprints'] and evidence['agent_y']==0
    assert len(evidence['rejecting_footprints'])<=8
