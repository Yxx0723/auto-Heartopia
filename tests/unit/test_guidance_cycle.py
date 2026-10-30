"""Control cycle scheduling and prepared geometry; no real input."""
from unittest.mock import patch
from tests.unit.test_guidance_route_cache import cached_game
from tests.unit.test_native_guidance_follow import run
from tests.unit.test_coordinate_navigation import scene
from utils.core.guidance_navigation import GuidanceNavigator


def test_slow_work_does_not_add_full_sample_sleep_before_next_cycle():
    game=cached_game();game.stop_at=.65;source=scene()
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    original=nav.follow_target;starts=[]
    def plan(*a,**k):
        starts.append(game.time);result=original(*a,**k);game.sleep(.06);return result
    nav.follow_target=plan
    run(game,navigator=nav)
    assert len(starts)>2
    assert min(b-a for a,b in zip(starts,starts[1:]))<.09
    assert not game.keys


def test_worker_prepares_signature_before_control_thread_uses_snapshot():
    from unittest.mock import Mock
    from utils.ui.scene_map_worker import SceneMapWorker
    from tests.unit.test_navigation_supply import ready
    from utils.core.coordinate_navigation import scene_geometry_signature
    raw=ready(0.,0.);reader=Mock();reader.snapshot.return_value=raw
    worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:1.)
    worker.set_pid(7);assert worker.poll_once()
    nav=GuidanceNavigator(lambda:worker.navigation_snapshot(7),water_areas=())
    with patch('utils.core.coordinate_navigation.scene_geometry_signature',wraps=scene_geometry_signature) as calculate:
        nav.next_target((0,0,0),(0,0,3))
    assert calculate.call_count==0
    worker.close()


import json
import pytest
from copy import deepcopy
from unittest.mock import Mock
from utils.core.prepared_navigation import PreparedNavigationScene
from utils.core.coordinate_navigation import scene_geometry_signature
from utils.ui.scene_map_worker import SceneMapWorker
from utils.flows.native_guidance_follow import GuidanceFollower
from tests.unit.test_native_guidance_follow import guide_game
from tests.unit.test_coordinate_navigation import box
from tests.unit.test_navigation_supply import ready


def follower(game):
    return GuidanceFollower(game.size(),game,game,game,game,Mock(),game,
                             GuidanceNavigator(lambda:scene(),clock=game.monotonic,water_areas=()))


@pytest.mark.parametrize('work,expected',[(0.,.05),(.02,.03),(.06,0.),(.2,0.)])
def test_cycle_waits_only_remaining_period(work,expected):
    game=guide_game();f=follower(game);game.time=work
    f.wait_next_cycle(0.)
    assert f.read_stats['cycle_sleep_requested']==pytest.approx(expected)
    assert game.time==pytest.approx(max(work,.05))
    f.input_lease.close()


def test_no_permit_budget_releases_and_waits_instead_of_spinning():
    game=guide_game();f=follower(game);game.keys={'w'};f.keys={'w'}
    f.input_lease.arm(.035);game.time=.03
    f.wait_next_cycle(0.)
    assert not game.keys and game.time>=.05
    assert f.input_lease.metrics()['release_counts']['cycle_budget_exhausted']==1
    f.input_lease.close()


def test_cycle_wait_rounding_advances_past_float_sample_boundary():
    game=guide_game();f=follower(game);game.time=.14999999999999997
    f.wait_next_cycle(.1)
    assert game.time>.15
    f.input_lease.close()


def test_prepared_signature_matches_canonical_ignores_order_and_detaches_source():
    raw=ready(0.,0.);raw['colliders']=[box(),box(-4,-3,5,6)]
    before=scene_geometry_signature(raw);prepared=PreparedNavigationScene(raw)
    assert prepared.prepared_signature()==before
    reverse=deepcopy(raw);reverse['colliders'].reverse()
    assert PreparedNavigationScene(reverse).prepared_signature()==before
    raw['colliders'][0]['polygon'][0][0]=99
    assert prepared.prepared_signature()==before
    assert scene_geometry_signature(raw)!=before
    assert json.loads(json.dumps(prepared))['quality']['navigation_eligible']


def test_prepared_snapshot_forbids_nested_and_outer_geometry_mutation():
    raw=ready(0.,0.);raw['colliders']=[box()];p=PreparedNavigationScene(raw)
    with pytest.raises(TypeError):p['colliders']=[]
    with pytest.raises(TypeError):p['colliders'][0]['height']=(20,30)
    with pytest.raises(TypeError):p['colliders'][0]['polygon'][0][0]=8
    with pytest.raises(TypeError):p['quality']['navigation_eligible']=False
    assert deepcopy(p) is p


def test_plain_copied_snapshot_cannot_reuse_prepared_signature_after_change():
    raw=ready(0.,0.);p=PreparedNavigationScene(raw)
    changed=dict(p);changed['colliders']=[box()]
    nav=GuidanceNavigator(lambda:changed,water_areas=())
    point=nav.next_target((0,0,0),(0,0,6))
    assert point[0]!=0 and not nav.last_timings['signature_prepared']


def test_prepared_new_wall_and_new_scene_are_not_hidden_by_cached_signature():
    a=ready(0.,0.);a['cache_context']='first'
    b=ready(0.,0.);b['colliders']=[box()];b['cache_context']='second'
    source=[PreparedNavigationScene(a)]
    nav=GuidanceNavigator(lambda:source[0],water_areas=())
    nav.next_target((0,0,0),(0,0,6));source[0]=PreparedNavigationScene(b)
    assert nav.next_target((0,0,0),(0,0,6))[0]!=0
    assert not nav.obstacles.clear((0,0),(0,6))


def test_preparation_failure_revokes_previous_publication():
    reader=Mock();reader.snapshot.return_value=ready(0.,0.)
    worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:1.)
    worker.set_pid(7);assert worker.poll_once();assert worker.navigation_snapshot(7)
    with patch('utils.ui.scene_map_worker.PreparedNavigationScene',side_effect=ValueError('bad')):
        assert not worker.poll_once()
    assert worker.navigation_snapshot(7) is None
    worker.close()


def test_preparation_does_not_extend_expiry_if_work_runs_past_ttl():
    now=[1.];reader=Mock();reader.snapshot.return_value=ready(0.,0.)
    worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:now[0])
    worker.set_pid(7)
    def prepare(raw):
        result=PreparedNavigationScene(raw);now[0]=7.;return result
    with patch('utils.ui.scene_map_worker.PreparedNavigationScene',side_effect=prepare):worker.poll_once()
    assert worker.navigation_snapshot(7) is None
    worker.close()


def test_world_switch_during_preparation_discards_old_generation():
    reader=Mock();reader.snapshot.return_value=ready(0.,0.)
    worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:1.)
    worker.set_pid(7)
    def prepare(raw):
        worker.set_pid(8);return PreparedNavigationScene(raw)
    with patch('utils.ui.scene_map_worker.PreparedNavigationScene',side_effect=prepare):assert not worker.poll_once()
    assert worker.navigation_snapshot(7) is None and worker.navigation_snapshot(8) is None
    worker.close()


def test_headless_process_probe_checks_real_geometry_preparation_without_input(monkeypatch):
    from utils.drivers.navigation_process import probe_navigation_process
    source=Mock();source.snapshot.return_value=ready(0.,0.)
    source.last_read_stats={'process':{}};source.process.is_alive.return_value=False
    monkeypatch.setattr('utils.drivers.coordinate_process.find_game_processes',lambda:[7])
    monkeypatch.setattr('utils.drivers.navigation_process.NavigationProcessScene',lambda *a:source)
    result=probe_navigation_process('profile')
    assert result['read_only'] and not result['input_sent']
    assert len(result['scans'])==4
    assert all(x['prepared_signature_equal'] for x in result['scans'])
    source.close.assert_called_once()
