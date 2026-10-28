"""Spawned collision workers use synthetic readers, never the real game."""
import os
import pickle
import threading
import time
from unittest.mock import Mock

import pytest

from utils.core.scene_read import SceneReadIssue, navigation_quality_valid


class SyntheticReader:
    def __init__(self,*args):self.last_read_stats={};self.count=0;self.pid=args[0]
    def snapshot(self):
        self.count+=1;now=time.monotonic();self.last_read_stats={'calls':self.count}
        return {'state':'ready','world_state':'ready','navigation_only':True,
            'objects':[],'colliders':[],'source_pid':os.getpid(), 'target_pid':self.pid,'sequence':self.count,
            'quality':{'navigation_eligible':True,'root_verified_at':now,
                'parts':{key:{'state':'complete','observed_from':now,'observed_to':now}
                         for key in ('managed_collisions','native_collisions')}}}
    def close(self):pass


class StaleReader(SyntheticReader):
    def snapshot(self):
        value=super().snapshot()
        for part in value['quality']['parts'].values():part['observed_from']-=7.
        return value


class MutationReader(SyntheticReader):
    def snapshot(self):
        if self.count==0:
            self.count+=1
            raise SceneReadIssue('changing',kind='mutation',stage='managed_collision_array',
                                 retryable=True,scope_verified=True)
        return super().snapshot()


class FailedReader(SyntheticReader):
    def snapshot(self):raise ValueError('secret pointer 0xDEADBEEF')


class OversizedReader(SyntheticReader):
    def snapshot(self):return {**super().snapshot(),'oversized':'x'*10000}


class BlockedReader(SyntheticReader):
    def snapshot(self):time.sleep(30.);return super().snapshot()


def reader(factory=SyntheticReader,**kw):
    from utils.drivers.navigation_process import NavigationProcessScene
    return NavigationProcessScene(123,'unused-profile',reader_factory=factory,**kw)


def test_spawn_has_distinct_process_and_reuses_one_child_without_input():
    source=reader()
    try:
        a=source.snapshot();b=source.snapshot()
        assert a['source_pid']!=os.getpid() and a['source_pid']==b['source_pid']
        assert (a['sequence'],b['sequence'])==(1,2)
        assert navigation_quality_valid(b,time.monotonic())
        assert source.last_read_stats['process']['child_read_seconds']>=0.
        assert source.last_read_stats['process']['wire_bytes']>0
        assert source.last_read_stats['process']['worker_pid']==b['source_pid']
    finally:source.close()
    assert not source.process.is_alive()
    source.close()  # Idempotent cleanup.


def test_process_transfer_does_not_renew_old_observation_time():
    source=reader(StaleReader)
    try:assert not navigation_quality_valid(source.snapshot(),time.monotonic())
    finally:source.close()


def test_verified_mutation_retains_identity_and_error_flags_across_wire():
    source=reader(MutationReader)
    try:
        with pytest.raises(SceneReadIssue) as caught:source.snapshot()
        assert caught.value.scope_verified and caught.value.retryable
        assert caught.value.stage=='managed_collision_array'
        assert source.snapshot()['sequence']==2
    finally:source.close()


def test_unknown_remote_error_is_not_retryable_or_exposed_as_raw_text():
    source=reader(FailedReader)
    try:
        with pytest.raises(SceneReadIssue) as caught:source.snapshot()
        assert caught.value.kind=='unknown' and not caught.value.retryable
        assert 'DEADBEEF' not in str(caught.value)
    finally:source.close()


def test_oversized_result_fails_closed_instead_of_publishing_partial_geometry():
    source=reader(OversizedReader,max_bytes=4096)
    try:
        with pytest.raises(SceneReadIssue) as caught:source.snapshot()
        assert caught.value.kind=='transport_limit'
    finally:source.close()


def test_cancel_stops_pending_read_and_cleanup_only_terminates_owned_child():
    source=reader(BlockedReader);stopped=threading.Event();source.cancelled=stopped.is_set
    errors=[]
    def read():
        try:source.snapshot()
        except SceneReadIssue as exc:errors.append(exc.kind)
    thread=threading.Thread(target=read);thread.start();time.sleep(.1);stopped.set()
    thread.join(2.)
    try:assert not thread.is_alive() and errors==['cancelled']
    finally:source.close()
    assert not source.process.is_alive()


def test_dead_child_is_rejected_without_waiting_for_whole_read_timeout():
    source=reader();source.process.terminate();source.process.join(2.)
    try:
        with pytest.raises(SceneReadIssue):source.snapshot()
    finally:source.close()


def test_blocked_child_times_out_and_is_reaped():
    source=reader(BlockedReader,timeout=1.)
    try:
        with pytest.raises(SceneReadIssue) as caught:source.snapshot()
        assert caught.value.kind=='worker_timeout'
    finally:source.close()
    assert not source.process.is_alive()


def test_scene_worker_close_interrupts_a_blocked_ipc_receiver():
    from utils.ui.scene_map_worker import SceneMapWorker
    sources=[]
    def factory(pid,profile):
        source=reader(BlockedReader);sources.append(source);return source
    worker=SceneMapWorker('unused',factory=factory);worker.set_pid(123);worker.start()
    deadline=time.monotonic()+3.
    while not sources and time.monotonic()<deadline:time.sleep(.01)
    assert sources
    worker.close()
    assert not worker.thread.is_alive() and not sources[0].process.is_alive()


def test_guidance_uses_process_reader_but_custom_factories_still_work(monkeypatch):
    from utils.drivers.mono_scene import NavigationSceneMap
    from utils.ui.point_playback import PointPlayback
    from tests.unit.test_point_playback import FakeWindow,WaitingControls
    factories=[]
    class Worker:
        def __init__(self,*a,**kw):factories.append(kw['factory'])
        def set_pid(self,pid):pass
        def start(self):pass
        def close(self):pass
    monkeypatch.setattr('utils.ui.point_playback.SceneMapWorker',Worker)
    for factory in (NavigationSceneMap,SyntheticReader):
        worker=PointPlayback('profile',window_factory=FakeWindow,controls_factory=WaitingControls,
            device_factory=lambda w:Mock(),guidance_scene_factory=factory,scene_provider=lambda p:None,
            guidance_runner=Mock(return_value={'arrived':False,'reason':'test stopped'}))
        worker.start_guidance(123);worker.thread.join(3.);assert not worker.active;worker.close()
    assert factories[0].__name__=='NavigationProcessScene'
    assert factories[1] is SyntheticReader


@pytest.mark.parametrize('frame', [b'bad pickle',{'request':99,'game_pid':123},
    {'request':1,'game_pid':999},{'request':1,'game_pid':123,'scene':[]},
    {'request':1,'game_pid':123,'error':{'kind':'mutation','stage':'test','retryable':1,'scope_verified':True}}])
def test_bad_or_late_ipc_frames_never_reach_navigation(frame):
    from utils.drivers.navigation_process import NavigationProcessScene
    source=NavigationProcessScene.__new__(NavigationProcessScene)
    source.closed=False;source.sequence=0;source.pid=123;source.timeout=1.;source.max_bytes=4096
    source.cancelled=lambda:False;source.last_read_stats={};source.connection=Mock();source.process=Mock(pid=999)
    source.connection.poll.return_value=True
    source.connection.recv_bytes.return_value=frame if isinstance(frame,bytes) else pickle.dumps(frame)
    with pytest.raises(SceneReadIssue) as caught:source.snapshot()
    assert caught.value.kind=='worker_protocol'


def test_geometry_payload_preserves_height_layer_direction_and_float_values():
    # Exercise the real child transport with a typed finite fixture.
    source=reader(GeometryReader)
    try:
        value=source.snapshot()
        assert value['colliders']==GEOMETRY
        value['colliders'][0]['height'][0]=999.
        assert source.snapshot()['colliders']==GEOMETRY
    finally:source.close()


GEOMETRY=[{'height':[1.23456789,3.75],'polygon':[(0.,1.),(2.,1.),(2.,3.)],
           'category':'wall','source':'fixture','direction':(0.,0.,1.)}]


class GeometryReader(SyntheticReader):
    def snapshot(self):return {**super().snapshot(),'colliders':GEOMETRY}


def test_game_pid_switch_reaps_old_child_and_never_reuses_its_snapshot():
    from utils.drivers.navigation_process import NavigationProcessScene
    from utils.ui.scene_map_worker import SceneMapWorker
    worker=SceneMapWorker('unused',factory=lambda pid,p:NavigationProcessScene(pid,p,reader_factory=SyntheticReader))
    try:
        worker.set_pid(123);worker.poll_once();old=worker.reader
        first=worker.navigation_snapshot(123)
        worker.set_pid(124);assert worker.navigation_snapshot(123) is None
        worker.poll_once();second=worker.navigation_snapshot(124)
        assert first['source_pid']!=second['source_pid'] and second['target_pid']==124
        assert not old.process.is_alive()
    finally:worker.close()


@pytest.mark.parametrize('factory,retained',[(MutationReader,True),(FailedReader,False),(StaleReader,True)])
def test_parent_worker_preserves_error_recovery_and_freshness_gates(factory,retained):
    from utils.drivers.navigation_process import NavigationProcessScene
    from utils.ui.scene_map_worker import SceneMapWorker
    sources=[]
    def open_reader(pid,profile):
        value=NavigationProcessScene(pid,profile,reader_factory=factory);sources.append(value);return value
    worker=SceneMapWorker('unused',factory=open_reader)
    try:
        worker.set_pid(123);worker.poll_once()
        assert worker.navigation_snapshot(123) is None
        assert (worker.reader is sources[0]) is retained
        if factory is MutationReader:
            assert worker.retry_delay==pytest.approx(.1)
            worker.poll_once();assert worker.navigation_snapshot(123)['sequence']==2
        if not retained:assert not sources[0].process.is_alive()
    finally:worker.close()
