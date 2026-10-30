"""Continuity and independent stop protection; no real devices or game."""
from copy import deepcopy
from tests.unit.test_native_guidance_follow import guide_game, run
from tests.unit.test_guidance_route_cache import cached_game
from tests.unit.test_coordinate_navigation import scene
from utils.core.guidance_navigation import GuidanceNavigator


def test_normal_heartbeat_does_not_tap_forward_repeatedly():
    game=cached_game();game.stop_at=.9
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    source=scene()
    run(game,navigator=nav)
    assert len([e for _,e in game.events if e['type']=='key_down'])==1
    assert not game.keys


def test_identical_new_snapshots_do_not_release_movement():
    game=guide_game();game.stop_at=.45
    nav=GuidanceNavigator(lambda:deepcopy(scene()),clock=game.monotonic,water_areas=())
    run(game,navigator=nav)
    assert len([e for _,e in game.events if e['type']=='key_down'])==1
    assert not game.keys


import threading
import time
from unittest.mock import Mock
import pytest
from utils.drivers.guidance_input_lease import GuidanceInputLease
from utils.flows.native_guidance_follow import GuidanceFollower, _GuidanceRetry


@pytest.mark.parametrize('reason', ['lease_expired', 'stop', 'pause', 'focus_or_size', 'watchdog_query_error'])
def test_independent_watchdog_releases_during_blocked_controller(reason):
    game=guide_game();released=threading.Event()
    original=game.release_all
    def release():
        original();released.set()
    game.release_all=release
    lease=GuidanceInputLease(game,game,game,game.size(),time.monotonic)
    game.execute({'type':'key_down','key':'w'})
    lease.arm(time.monotonic()+(.04 if reason=='lease_expired' else 2.))
    if reason=='stop':game.stop_at=0.
    if reason=='pause':game.running=False
    if reason=='focus_or_size':game.focus=False
    if reason=='watchdog_query_error':game.foreground=Mock(side_effect=OSError('window gone'))
    lease.start()
    try:
        # No controller polling, input renewal or memory-reader cooperation.
        assert released.wait(.6)
        assert not game.keys and lease.tripped
        assert lease.metrics()['release_counts'][reason]==1
        with pytest.raises(ValueError):lease.arm(time.monotonic()+1.)
    finally:lease.close()
    assert not lease.thread.is_alive()


def test_read_does_not_extend_existing_permit_and_controller_resynchronizes_keys():
    game=cached_game();source=scene()
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    f=GuidanceFollower(game.size(),game,game,game,game,Mock(),game,nav)
    f.refresh_guidance((0,0,0))
    f.input_lease.arm(.1)
    f.keys={'w'};game.keys={'w'}
    game.time=.2
    with pytest.raises(_GuidanceRetry):f.guard()
    assert not game.keys and not f.keys and f.cache.healthy_at is None
    f.guard()  # Trip is consumed; next iteration may read fresh data.
    assert f.refresh_guidance((0,0,0))
    f.input_lease.close()


def test_new_collision_geometry_still_releases_before_rebuild():
    from tests.unit.test_coordinate_navigation import box
    source=[scene()];released=[]
    nav=GuidanceNavigator(lambda:source[0],water_areas=())
    nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append('plan'))
    released.clear();source[0]=deepcopy(source[0])
    nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append('plan'))
    assert not released
    source[0]=scene(box())
    target=nav.next_target((0,0,0),(0,0,6),before_plan=lambda:released.append('plan'))
    assert released and target[0]!=0
    assert not nav.obstacles.clear((0,0),(0,6))


def test_short_valid_health_reads_hold_key_and_emit_continuity_metrics():
    game=cached_game();game.stop_at=.9
    original=game.guidance_status.side_effect
    def read():
        if game.time>.3:
            assert game.keys=={'w'}
        game.sleep(.015)
        return original()
    game.guidance_status.side_effect=read
    reports=[];source=scene()
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    run(game,lambda s,m,**extra:reports.append(extra),nav)
    assert len([e for _,e in game.events if e['type']=='key_down'])==1
    assert any('input_continuity' in x for x in reports)
    assert not game.keys


@pytest.mark.parametrize('fault', ['collision','target','scene','focus','stop'])
def test_continuous_mode_retains_existing_stop_and_identity_guards(fault):
    game=cached_game();game.stop_at=1.2;source=scene()
    original=game.guidance_status.side_effect
    def read():
        value=original()
        if game.time>.35:
            if fault=='target':value['target']=(50,0,50)
            if fault=='scene':value['context']='other-scene'
            if fault=='focus':game.focus=False
            if fault=='stop':game.stop_at=game.time
        return value
    game.guidance_status.side_effect=read
    nav=GuidanceNavigator(lambda:None if fault=='collision' and game.time>.35 else source,
                          clock=game.monotonic,water_areas=())
    result=run(game,navigator=nav)
    assert not result['arrived'] and not game.keys
    assert not [(t,e) for t,e in game.events if t>.5 and e['type']=='key_down']


def test_device_release_failure_is_visible_to_controller():
    game=guide_game();device=Mock();device.release_all.side_effect=OSError('denied')
    lease=GuidanceInputLease(device,game,game,game.size(),lambda:2.)
    lease.arm(3.);game.focus=False;lease.poll()
    assert isinstance(lease.error,OSError) and lease.tripped


def test_closed_lease_does_not_leak_watchdog_thread_or_held_keys():
    game=guide_game();lease=GuidanceInputLease(game,game,game,game.size(),time.monotonic)
    game.keys={'w'};lease.arm(time.monotonic()+10.)
    lease.start();lease.close()
    assert not game.keys and not lease.thread.is_alive()


@pytest.mark.parametrize('reason', ['lease_expired','stop'])
def test_follower_blocked_health_read_is_released_before_read_returns(reason):
    game=cached_game();game.stop_at=1.2;source=scene();released=threading.Event()
    base_release=game.release_all
    def release():
        had_keys=bool(game.keys);base_release()
        if had_keys:released.set()
    game.release_all=release
    original=game.guidance_status.side_effect;checked=[]
    def read():
        if game.time>.3 and not checked:
            assert game.keys
            checked.append(True);released.clear()
            if reason=='stop':game.stop_at=game.time
            else:game.time+=.3
            assert released.wait(.6), 'Watchdog did not release while reader blocked'
            assert not game.keys
        return original()
    game.guidance_status.side_effect=read
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    run(game,navigator=nav)
    assert checked and not game.keys


def test_closed_lease_cannot_be_rearmed():
    game=guide_game();lease=GuidanceInputLease(game,game,game,game.size(),time.monotonic)
    lease.close()
    with pytest.raises(ValueError):lease.arm(time.monotonic()+1.)


def test_collision_expiry_caps_input_permit_without_extending_source_ttl(monkeypatch):
    from tests.unit.test_navigation_supply import ready
    game=cached_game();game.time=5.75;game.stop_at=6.5
    source=ready(0.,0.);deadlines=[]
    original=GuidanceInputLease.arm
    def arm(self,deadline):
        deadlines.append(deadline);return original(self,deadline)
    monkeypatch.setattr(GuidanceInputLease,'arm',arm)
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=(),scene_clock=game.monotonic)
    run(game,navigator=nav)
    assert deadlines and max(deadlines)<=6.
    assert not [(t,e) for t,e in game.events if t>=6. and e['type']=='key_down']
    assert not game.keys


@pytest.mark.parametrize('origin', [-400., .433, 90000.])
def test_collision_ttl_transfers_duration_between_different_clock_origins(origin):
    from tests.unit.test_navigation_supply import ready
    nav=GuidanceNavigator(lambda:None, scene_clock=lambda:5.9)
    nav.scene=ready(0.,0.)
    assert nav.collision_deadline(5.9+origin)==pytest.approx(6.+origin)


def test_expired_source_collision_cannot_be_resurrected_by_control_clock():
    from tests.unit.test_navigation_supply import ready
    nav=GuidanceNavigator(lambda:None, scene_clock=lambda:7.)
    nav.scene=ready(0.,0.)
    assert nav.collision_deadline(1.) is None


def test_permit_revocation_between_validation_and_keydown_retries_without_input():
    game=cached_game();source=scene()
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    f=GuidanceFollower(game.size(),game,game,game,game,Mock(),game,nav)
    f.move_deadline=1.
    f.input_lease.arm=Mock(side_effect=ValueError('expired between checks'))
    with pytest.raises(_GuidanceRetry):f.set_keys({'w'})
    assert not game.keys
    f.input_lease.close()
