"""Typed camera direction plus playback without calibration input pulses."""
import math
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_mono_position import Runtime
from tests.unit.test_coordinate_player import Game, manual_points
from utils.drivers.mono_position import MonoPositionLocator
from utils.drivers.movement_basis import MovementBasisReader, camera_basis
from utils.flows.coordinate_player import drive_points
from utils.core.coordinate_navigation import LocalNavigator


def runtime():
    r = Runtime()
    r.add_class('PlayerSphereChecker', 'XDTLevelAndEntity.Gameplay.Interaction.Checker',
                [('_cameraForward',16,17),('_playerPos',28,17)],size=40)
    checker = r.make_object('PlayerSphereChecker',40)
    r.write(checker+16,struct.pack('<6f',0.,-.6,.8,98.,22.15,-9.))
    cls = r.classes['LocalPlayerComponent']
    old = struct.unpack('<Q',r.read(cls+0xa0,8))[0]
    table = r.alloc(64)
    r.write(table,r.read(old,32))
    typ = r.alloc(16)
    r.write(typ+8,struct.pack('<HB',1,18))
    r.write(table+32,struct.pack('<QQQi',typ,r.string('_checker'),cls,320))
    r.p64(cls+0xa0,table)
    r.write(cls+0x100,struct.pack('<I',2))
    r.p64(r.player+320,checker)
    locator = MonoPositionLocator(r.read,r.base)
    return r,checker,MovementBasisReader(r.read,locator)


def test_typed_local_camera_normalises_pitch_and_tracks_turning_without_moving():
    r,c,reader = runtime()
    assert reader.snapshot() == {'forward':[0.,1.],'right':[1.,-0.]}
    r.write(c+16,struct.pack('<3f',.8,-.6,0.))
    assert reader.snapshot() == {'forward':[1.,0.],'right':[0.,-1.]}
    assert reader.locator.snapshot()['position'] == (98.,21.5,-9.)


@pytest.mark.parametrize('fault',['nan','vertical','stale','foreign','role'])
def test_invalid_or_stale_camera_never_becomes_a_guessed_basis(fault):
    r,c,reader = runtime()
    if fault == 'nan': r.write(c+16,struct.pack('<f',float('nan')))
    if fault == 'vertical': r.write(c+16,struct.pack('<3f',0.,1.,0.))
    if fault == 'stale': r.write(c+28,struct.pack('<3f',300.,20.,0.))
    if fault == 'foreign': r.p64(r.classes['PlayerSphereChecker']+0x50,r.string('Other'))
    if fault == 'role': r.write(r.entity+148,b'\0')
    with pytest.raises(ValueError):reader.snapshot()


@pytest.mark.parametrize('angle',[0.,math.pi/3,-math.pi/2])
def test_playback_uses_current_camera_from_first_movement_and_after_pause(angle,monkeypatch):
    from utils.flows.coordinate_player import _Follower
    game = Game(angle)
    game.pause_at,game.resume_at = 1.,2.
    game.movement_basis = lambda:camera_basis([-math.sin(game.angle),0.,math.cos(game.angle)])
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('No calibration pulses')))
    report = Mock()
    nav = LocalNavigator(lambda:{'state':'ready','colliders':[]},clock=game.monotonic,water_areas=())
    assert drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,report,
                        clock=game,navigator=nav)
    assert not any(c.args[0] == 'calibrating' for c in report.call_args_list)
    assert not game.keys


def test_missing_camera_waits_without_wasd_or_calibration_until_f9(monkeypatch):
    from utils.flows.coordinate_player import _Follower
    game = Game();game.stop_at = .3
    game.movement_basis = Mock(side_effect=ValueError('loading'))
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('No fallback movement')))
    assert not drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,Mock(),clock=game)
    assert not game.events and not game.keys


def test_camera_change_during_walking_reloads_basis_without_probe_pulses(monkeypatch):
    from utils.flows.coordinate_player import _Follower
    game=Game();game.rotate_at=.7
    game.movement_basis=lambda:camera_basis([0.,0.,-1. if game.time>=game.rotate_at else 1.])
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('no probe')))
    nav=LocalNavigator(lambda:{'state':'ready','colliders':[]},clock=game.monotonic,water_areas=())
    assert drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,Mock(),clock=game,navigator=nav)
    assert not game.keys


def test_only_synced_vertical_view_explicitly_allows_safe_fallback():
    from utils.drivers.movement_basis import DirectionUnavailable
    r,c,reader=runtime();r.write(c+16,struct.pack('<3f',0.,1.,0.))
    with pytest.raises(DirectionUnavailable) as exc:reader.snapshot()
    assert exc.value.safe_to_probe
    r.write(c+28,struct.pack('<3f',1000.,20.,0.))
    with pytest.raises(ValueError) as exc:reader.snapshot()
    assert not getattr(exc.value,'safe_to_probe',False)


def test_readonly_checker_replacement_during_sampling_cannot_return_stale_direction():
    from utils.drivers.mono_scene import SceneMetadata
    r,c,reader=runtime()
    def read(address,size):
        value=r.read(address,size)
        if address==c+16:r.p64(r.player+320,0)
        return value
    reader.meta=SceneMetadata(read,r.domain)
    with pytest.raises(ValueError):reader.snapshot()


@pytest.mark.parametrize('obstacle',['wall','tree','home'])
def test_vertical_view_never_triggers_fallback_near_obstacles_or_home(obstacle,monkeypatch):
    from utils.flows.coordinate_player import _Follower
    from utils.drivers.movement_basis import DirectionUnavailable
    game=Game();game.stop_at=1.5
    game.movement_basis=Mock(side_effect=DirectionUnavailable('vertical',safe_to_probe=True))
    s={'state':'ready','colliders':[]}
    if obstacle=='wall':s['colliders']=[{'polygon':[[1.,-1.],[2.,-1.],[2.,1.],[1.,1.]],'height':[0.,2.]}]
    if obstacle=='tree':s['objects']=[{'category':'tree','position':[1.,0.,0.]}]
    if obstacle=='home':s['navigation_exclusions']=[[[1.,-1.],[2.,-1.],[2.,1.],[1.,1.]]]
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('unsafe calibration')))
    assert not drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,Mock(),clock=game,navigator=nav)
    assert not game.events


def test_synced_vertical_view_uses_one_small_probe_only_in_open_space(monkeypatch):
    from utils.flows.coordinate_player import _Follower
    from utils.drivers.movement_basis import DirectionUnavailable
    game=Game();game.movement_basis=Mock(side_effect=DirectionUnavailable('vertical',safe_to_probe=True))
    nav=LocalNavigator(lambda:{'state':'ready','colliders':[]},clock=game.monotonic,water_areas=())
    original=_Follower.calibrate;durations=[]
    def calibrate(self,*args,**kwargs):
        durations.append(self.calibration_seconds)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(_Follower,'calibrate',calibrate)
    assert drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,Mock(),clock=game,navigator=nav)
    assert durations==[.15] and not game.keys


def test_one_large_motion_deviation_does_not_override_readonly_basis(monkeypatch):
    from utils.flows.coordinate_player import _Follower
    from utils.core.direction_evidence import DirectionEvidence
    game=Game();game.movement_basis=lambda:camera_basis([0.,0.,1.])
    nav=LocalNavigator(lambda:{'state':'ready','colliders':[]},clock=game.monotonic,water_areas=())
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('wall slide is not calibration proof')))
    monkeypatch.setattr(DirectionEvidence,'observe',Mock(return_value={'state':'unknown','angle_degrees':100.}))
    assert drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,Mock(),clock=game,navigator=nav)
    assert not game.keys


def test_persistent_independent_motion_conflict_waits_at_resource_instead_of_probing(monkeypatch):
    from utils.flows.coordinate_player import _Follower
    from utils.core.direction_evidence import DirectionEvidence
    game=Game();game.stop_at=2.5;game.movement_basis=lambda:camera_basis([0.,0.,1.])
    s={'state':'ready','colliders':[],'objects':[{'category':'tree','position':[0.,0.,1.]}]}
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    monkeypatch.setattr(_Follower,'calibrate',Mock(side_effect=AssertionError('near resource, cannot probe')))
    monkeypatch.setattr(DirectionEvidence,'observe',Mock(return_value={'state':'rotation_candidate','angle_degrees':90.}))
    reports=Mock()
    assert not drive_points(manual_points([(0.,0.,6.)]),game.size(),lambda:game,game,game,game,reports,clock=game,navigator=nav)
    assert any(c.kwargs.get('direction_source')=='motion_conflict' for c in reports.call_args_list)
    assert not game.keys
