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


def test_failed_jump_with_navigator_replans_before_any_sideways_probe():
    from utils.flows.coordinate_player import _Follower
    game = Game()
    f = _Follower({'duration':0.,'client_size':game.size()},game,game,game,game,Mock(),game,
                  waypoints=[{'position':[0.,0.,10.]}],navigator=Mock(),
                  jump_on_recovery=True,stable_navigation=True,recovery_attempts=5)
    f.recovery_jump = Mock(return_value=False)
    f.recovery_leg = Mock(side_effect=AssertionError('Replan first'))
    assert not f.recover((0.,0.,10.),{},(0.,0.,0.),0,1)
    f.recovery_jump.assert_called_once()
    f.navigator.invalidate.assert_called_once()
    assert not game.keys
