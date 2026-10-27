"""Regression for full-route caching, independent recovery and one clock."""
import time
from unittest.mock import Mock

import pytest

from tests.unit.test_native_guidance_follow import guide_game, run, snapshot
from tests.unit.test_native_guidance_reader import reader as memory_reader
from tests.unit.test_coordinate_navigation import scene
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.drivers.native_guidance import NativeGuidanceReader


def cached_game():
    game=guide_game()
    original=game.guidance
    game.guidance=Mock(side_effect=lambda:dict(original(),path_token='one'))
    game.guidance_status=Mock(side_effect=lambda:{k:v for k,v in dict(original(),path_token='one').items() if k!='path'})
    return game


def test_production_reader_default_clock_matches_playback_perf_counter():
    # Python <=3.12 on Windows: uptime and performance counters have different
    # origins/resolutions. Comparing them consumed most of a 0.5s budget.
    assert NativeGuidanceReader.__init__.__defaults__ == (time.perf_counter,)


def test_cached_route_is_not_copied_at_every_heartbeat():
    game=cached_game()
    assert run(game)['arrived']
    assert game.guidance_status.call_count > game.guidance.call_count*2
    assert game.guidance.call_count <= 5


def test_every_plan_slower_than_heartbeat_interval_still_allows_checked_movement():
    game=cached_game();game.stop_at=12.
    nav=LocalNavigator(lambda:scene(),clock=game.monotonic,water_areas=())
    original=nav.next_target
    def plan(*a,**k):
        assert not game.keys
        game.sleep(.15)
        return original(*a,**k)
    nav.next_target=plan
    assert run(game,navigator=nav)['arrived']
    assert not game.keys


def test_three_different_short_outages_do_not_accumulate_into_one_pause():
    game=cached_game();game.stop_at=15.
    normal_status=game.guidance_status.side_effect
    def status():
        if .5<=game.time<1.8:raise ValueError('path updating')
        return normal_status()
    game.guidance_status.side_effect=status
    normal_basis=game.movement_basis
    def direction():
        if 1.8<=game.time<3.2:raise ValueError('camera updating')
        return normal_basis()
    game.movement_basis=direction
    nav=LocalNavigator(lambda:scene(),clock=game.monotonic,water_areas=())
    original=nav.next_target
    def plan(*a,**k):
        if 3.2<=game.time<4.6:raise NavigationWait('scene updating',reason='scene')
        return original(*a,**k)
    nav.next_target=plan
    assert run(game,navigator=nav)['arrived']
    assert game.running
    assert not [(t,e) for t,e in game.events if .6<t<4.6 and e['type']=='key_down']


def test_header_change_refreshes_route_before_any_more_input():
    game=cached_game();original=game.guidance.side_effect
    game.guidance.side_effect=lambda:dict(original(),path_token='two' if game.time>=.6 else 'one',
        path=((0,.5,0),(2,.5,2),(4,.5,4)) if game.time>=.6 else snapshot()['path'])
    normal_status=game.guidance_status.side_effect
    game.guidance_status.side_effect=lambda:dict(normal_status(),path_token='two' if game.time>=.6 else 'one')
    revisions=[]
    assert run(game,lambda s,m,**e:revisions.append(e['guidance_revision']) if s=='guidance_path' else None)['arrived']
    assert len(set(revisions))==2


def test_status_only_read_does_not_copy_full_path_payload():
    value=memory_reader();value.meta.data=Mock(wraps=value.meta.data)
    full=value.snapshot()
    value.meta.data.reset_mock()
    status=value.status()
    assert 'path' not in status
    assert status['path_token']==full['path_token']
    assert not [c for c in value.meta.data.call_args_list if c.args[1]>12]


def test_periodic_audit_detects_in_place_middle_change_without_version_increment():
    game=cached_game();normal=game.guidance.side_effect
    def full():
        return dict(normal(),path=((0,.5,0),(2,.5,2),(4,.5,4)) if game.time>=.6 else snapshot()['path'])
    game.guidance.side_effect=full
    revisions=[]
    assert run(game,lambda s,m,**e:revisions.append(e['guidance_revision']) if s=='guidance_path' else None)['arrived']
    assert len(set(revisions))==2


def test_failed_route_replacement_does_not_continue_using_old_payload():
    game=cached_game();game.stop_at=4.
    normal=game.guidance.side_effect
    game.guidance.side_effect=lambda: (_ for _ in ()).throw(ValueError('replacing')) if game.time>=.6 else normal()
    status=game.guidance_status.side_effect
    game.guidance_status.side_effect=lambda:dict(status(),path_token='two' if game.time>=.6 else 'one')
    assert not run(game)['arrived']
    assert not [(t,e) for t,e in game.events if t>.75 and e['type']=='key_down']
    assert not game.keys


def test_failed_preparation_is_bounded_and_never_sends_input():
    game=cached_game()
    game.guidance.side_effect=ValueError('no complete route')
    outcome=run(game)
    assert not outcome['arrived'] and 15<=game.time<16
    assert not game.events and game.closed


def test_persistent_guidance_failure_pauses_after_its_own_budget_without_movement():
    game=cached_game();game.stop_at=18.
    prepared=game.guidance.side_effect()
    game.guidance.side_effect=lambda:prepared if game.time==0 else (_ for _ in ()).throw(ValueError('path unavailable'))
    game.guidance_status.side_effect=ValueError('status unavailable')
    assert not run(game)['arrived']
    assert not game.running and not game.events


@pytest.mark.parametrize('kind',['target','scene','inactive'])
def test_cached_route_stops_on_heartbeat_target_scene_or_inactive_change(kind):
    game=cached_game();game.stop_at=3.
    original=game.guidance_status.side_effect
    def status():
        result=original()
        if game.time>=.6:
            result.update({'target':(99,0,0)} if kind=='target' else
                          {'context':'other'} if kind=='scene' else {'state':'inactive'})
        return result
    game.guidance_status.side_effect=status
    assert not run(game)['arrived']
    assert not [(t,e) for t,e in game.events if t>.75 and e['type']=='key_down']
    assert not game.keys


def test_cache_copies_payload_but_does_not_expire_content_when_health_is_fresh():
    from utils.core.guidance_path import GuidanceRouteCache
    cache=GuidanceRouteCache()
    raw=dict(snapshot(),path_token='one',path=[[0,.5,0],[0,.5,4]])
    cache.install(raw,0.)
    raw['path'][1][2]=100
    assert cache.confirm(dict(snapshot(now=50),path_token='one'),50.)
    assert cache.require_healthy(50.)['path'][-1][2]==4
    with pytest.raises(ValueError):cache.require_healthy(51.)


def test_mixed_outage_has_global_bound_even_when_individual_stages_recover():
    from utils.core.guidance_path import GuidanceRecovery
    recovery=GuidanceRecovery()
    assert not recovery.failed('guidance',0.)
    for i in range(1,30):
        recovery.passed('guidance' if i%2 else 'scene')
        assert not recovery.failed('scene' if i%2 else 'guidance',float(i))
    assert recovery.failed('guidance',30.)
    recovery.ready()
    assert not recovery.failed('guidance',100.)


@pytest.mark.parametrize('method',['snapshot','status'])
@pytest.mark.parametrize('fault',['version','target','type','field_bounds'])
def test_both_full_and_status_reads_reject_mid_read_mutation(method,fault):
    import struct
    value=memory_reader();m=value.meta
    if fault=='type':m.element_name='Other'
    elif fault=='field_bounds':m.fields['module']['end']=(9999,17)
    else:
        def hook(at,size):
            if at==432:
                if fault=='version':m.memory[220:224]=struct.pack('<i',2)
                else:m.memory[120:132]=struct.pack('<3f',0,0,9)
        m.hook=hook
    with pytest.raises(ValueError):getattr(value,method)()


def test_waiting_for_f6_keeps_cache_but_revalidates_health_before_movement():
    game=cached_game();game.running=False
    sleep=game.sleep
    def wait(dt):
        sleep(dt)
        if game.time>=.8:game.running=True
    game.sleep=wait
    assert run(game)['arrived']
    assert not [(t,e) for t,e in game.events if t<.8 and e['type']=='key_down']
    assert game.guidance_status.call_count>0


def test_target_changed_while_waiting_for_f6_requires_confirmation_without_motion():
    game=cached_game();game.running=False;game.stop_at=2.
    sleep=game.sleep
    def wait(dt):
        sleep(dt)
        if .8<=game.time<.81:game.running=True
    game.sleep=wait
    normal=game.guidance_status.side_effect
    game.guidance_status.side_effect=lambda:dict(normal(),target=(99,0,0) if game.time>=.5 else (8,0,4))
    assert not run(game)['arrived']
    assert not game.events and not game.running


def test_status_failure_resets_cached_native_reader_without_write_api():
    from types import SimpleNamespace
    from utils.drivers.coordinate_memory import ReadOnlyPosition
    memory=ReadOnlyPosition.__new__(ReadOnlyPosition)
    memory.guidance_reader=SimpleNamespace(status=Mock(side_effect=ValueError('updating')))
    with pytest.raises(ValueError):memory.guidance_status()
    assert memory.guidance_reader is None
