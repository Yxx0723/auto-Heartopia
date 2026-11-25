from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import numpy as np
import pytest

pytest.importorskip('win32gui')
from tools import fishing_auto_run as live
from utils.drivers.fishing_input import FishingInput
from utils.vision.aim_ring import Ring
from utils.vision.fish_shadow import Shadow


def test_watchdog_releases_during_blocking_capture():
    device = Mock()
    stopped = threading.Event()
    def guard():
        if stopped.is_set():
            raise InterruptedError('F9')
    watch = FishingInput(device,guard)
    watch.start()
    try:
        watch.press()
        stopped.set()
        end = time.monotonic()+.5
        while watch.held and time.monotonic()<end:
            time.sleep(.01)
        assert device.execute.call_args.args[0]=={'type':'mouse_up','button':'left'}
        with pytest.raises(InterruptedError,match='F9'):
            watch.press()
    finally:
        watch.close()


def mock_runner():
    from collections import Counter
    runner = live.FishingRunner.__new__(live.FishingRunner)
    runner.started = runner.phase_at = time.monotonic()
    runner.stats,runner.failures = Counter(),Counter()
    runner.phase = 'idle'
    runner.input = Mock()
    runner.input.held = False
    runner.target = live.FishTarget()
    runner.device = Mock()
    runner.log = Mock()
    runner.inventory_reader = None
    runner.cast_open = runner.cast_effective = False
    runner.last_game_state = None
    runner.landing_evidence = None
    runner.consecutive_failures = runner.read_failures = 0
    runner.size = (1382,807)
    runner.aim_poll_at = 0.
    runner.cycle_seconds = 0.
    runner.cycle_count = 0
    return runner


def test_adaptive_cast_requires_two_rings_and_releases_after_single_move(monkeypatch):
    runner = mock_runner()
    runner.phase = 'aiming'
    runner.grab = lambda: np.zeros((807,1382,3),np.uint8)
    runner.target = Mock(stable=True,last_at=time.monotonic())
    runner.target.update.return_value = Shadow(1000,780,280,60,20,810.,300.,0.)
    runner.target.predict.return_value = (815.,302.)
    runner.ring = None
    monkeypatch.setattr(live,'find_ring',lambda *a,**k:Ring(700.,280.,20.))
    def release(move):
        move()
    runner.input.release.side_effect = release
    runner.observe_aim(time.monotonic(),True)
    runner.input.release.assert_not_called()
    runner.observe_aim(time.monotonic(),True)
    assert runner.phase=='waiting' and runner.casts==1
    runner.device.native.mouse.assert_called_once()


def test_aim_timeout_treats_release_as_possible_cast(monkeypatch):
    runner = mock_runner()
    runner.phase = 'aiming'
    runner.phase_at -= live.policy.FISHING_AIM_LIMIT+.1
    runner.target.last_at = runner.started
    runner.state = lambda: (0,True,{'stage':0,'fish_id':0})
    runner.tick()
    assert runner.phase=='waiting' and runner.casts==1
    runner.input.release.assert_called_once_with()
    runner.device.native.mouse.assert_not_called()


def test_existing_bite_does_not_retrieve_or_press_without_tension():
    runner = mock_runner()
    runner.state = lambda:(5,False,{'stage':2,'fish_id':10})
    runner.inventory = Mock(return_value=None)
    runner.tick()
    assert runner.phase=='hooked' and runner.stats['bites']==1
    runner.input.press.assert_not_called()


def test_rebind_does_not_generate_input_until_state_is_resolved():
    runner = mock_runner()
    runner.state = Mock(side_effect=ValueError('引用变化'))
    runner.bind_rod = Mock()
    runner.tick()
    runner.bind_rod.assert_called_once()
    runner.input.press.assert_not_called()


def test_consecutive_cast_misses_stop():
    runner = mock_runner()
    runner.state = lambda:(0,True,{'stage':0,'fish_id':0})
    for attempt in range(3):
        runner.begin_cast()
        runner.phase_at -= 2.
        if attempt==2:
            with pytest.raises(InterruptedError,match='达到上限'):
                runner.tick()
        else:
            runner.tick()
    assert runner.stats['recoveries']==3


def test_formal_fishing_waits_for_new_f6_press_in_game(monkeypatch,tmp_path):
    from utils.drivers.input_task_gate import InputTaskGate
    from utils.ui.fishing_control import FishingControl
    import win32api
    import win32con
    import win32gui
    pressed = iter([True,True,False,True,True,False,True])
    foreground = iter([20,10])
    wait = Mock()
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda vk:0x8000 if vk==win32con.VK_F6 and next(pressed) else 0)
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:next(foreground))
    monkeypatch.setattr(win32gui,'IsWindow',lambda hwnd:hwnd==10)
    monkeypatch.setattr(live,'find_game',lambda:10)
    factory = Mock(return_value=SimpleNamespace(casts=0,run=Mock()))
    monkeypatch.setattr(live,'FishingRunner',factory)
    control = FishingControl(tmp_path/'profile.json',tmp_path,InputTaskGate())
    monkeypatch.setattr(control.cancelled,'wait',wait)
    control.run()
    assert wait.call_count==5
    factory.return_value.run.assert_called_once()


def test_f9_cancels_before_f6(monkeypatch,tmp_path):
    from utils.drivers.input_task_gate import InputTaskGate
    from utils.ui.fishing_control import FishingControl
    import win32api
    import win32con
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda vk:0x8000 if vk==win32con.VK_F9 else 0)
    monkeypatch.setattr(live,'find_game',lambda:10)
    factory = Mock()
    monkeypatch.setattr(live,'FishingRunner',factory)
    control = FishingControl(tmp_path/'profile.json',tmp_path,InputTaskGate())
    control.run()
    factory.assert_not_called()
    assert control.message=='已取消自动钓鱼'


def test_fishing_archive_keeps_metadata_and_summary(tmp_path):
    import json
    from utils.drivers.fishing_archive import FishingArchive
    from utils.core.fishing_log import fishing_event
    archive = FishingArchive(tmp_path,{'position':[1,2,3],'window_size':[800,600]})
    archive.add(fishing_event('cast','已发出抛竿'))
    archive.finish({'casts':1})
    assert json.loads((tmp_path/'run.json').read_text(encoding='utf-8'))['position']==[1,2,3]
    assert json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))['casts']==1
    assert json.loads((tmp_path/'autofish.jsonl').read_text(encoding='utf-8'))['ev']=='cast'


def test_positive_scoped_learning_backs_up_and_deduplicates(tmp_path):
    import sqlite3
    from utils.drivers.fishing_archive import FishingArchive
    from utils.core.fishing_log import fishing_event
    run = tmp_path/'run1'
    run.mkdir()
    metadata = dict(position=[1,2,3],camera={'forward':[1,0]},window_size=[800,600],profile='v1')
    archive = FishingArchive(run,metadata)
    archive.add(fishing_event('result_unknown','未确认',cast_number=1,aim_evidence={'bad':True}))
    archive.add(fishing_event('catch','已确认',cast_number=2,aim_evidence={'float_world_xyz':[3,2,4]}))
    archive.finish({'casts':2})
    with sqlite3.connect(tmp_path/'fishing-aim.backup.sqlite3') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert db.execute('SELECT cast_no FROM aim_evidence').fetchall()==[(2,)]


def test_confirmed_escape_never_counts_as_catch():
    runner = mock_runner()
    runner.phase = 'hooked'
    runner.state = lambda: (4,False,{'stage':4,'fish_id':10})
    runner.tick()
    assert runner.phase=='returning' and runner.stats['escapes']==1
    assert runner.stats['catches']==0
    runner.input.release.assert_called_once()


def test_visual_success_animation_never_starts_reeling():
    runner = mock_runner()
    runner.state = lambda: (6,False,{'stage':5,'fish_id':10})
    runner.tick()
    runner.input.press.assert_not_called()
    assert runner.stats['bites']==0
