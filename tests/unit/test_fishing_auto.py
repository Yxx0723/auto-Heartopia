"""fishing_auto 全流程状态机单测：假内存+假颜色，不碰游戏。"""
import pytest

from utils.core import thresholds as policy
from utils.flows.fishing_auto import FishingObserver, FishingSession, Action, Observation


class FakeMemory:
    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def read(self):
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def sess(memory, colors=None, clock=None, **kw):
    read_line = (lambda: next(colors)) if colors is not None else None
    return FishingSession(FishingObserver(FakeMemory(memory).read, read_line),
                          clock=clock or FakeClock(), **kw)


def test_observer_maps_state():
    obs = FishingObserver(FakeMemory([(policy.FISHING_STATE_HOOKED, 0.3)]).read).observe()
    assert obs.stage == 'hooked' and obs.state == 6 and obs.play_time == 0.3
    assert obs.line_dom == 'unknown'


def test_observer_maps_idle_and_unknown():
    assert FishingObserver(FakeMemory([(0, 0)]).read).observe().stage == 'idle'
    assert FishingObserver(FakeMemory([(99, 0)]).read).observe().stage == 'unknown'


def test_observer_merges_line_color():
    color = {'dom': 'red', 'red': 400, 'yellow': 0}
    obs = FishingObserver(FakeMemory([(6, 0)]).read, lambda: color).observe()
    assert obs.red_px == 400 and obs.line_dom == 'red'


def test_observer_rejects_bad_callbacks():
    with pytest.raises(ValueError):
        FishingObserver(None)
    with pytest.raises(ValueError):
        FishingObserver(lambda: (0, 0), 123)


def test_start_from_idle_presses_cast():
    s = sess([(0, 0)])
    act = s.start()
    assert act.kind == 'cast' and s.holding and s.state == 'casting'


def test_start_ignored_when_not_idle():
    s = sess([(0, 0)])
    s.start()
    assert s.start().kind == 'none'


def test_cast_then_release_to_waiting():
    clock = FakeClock()
    s = sess([(0, 0)] * 8, clock=clock, cast_seconds=1.0)
    s.start()
    assert s.update().kind == 'none'          # 瞄准中
    clock.advance(1.1)
    act = s.update()
    assert act.kind == 'release' and not s.holding and s.state == 'waiting'
    assert s.casts == 1


def test_waiting_bite_requires_two_white_samples_before_hold():
    clock = FakeClock()
    colors = iter([{'dom':'white'}]*4)
    s = sess([(0,0),(6,0)], colors, clock, cast_seconds=.5)
    s.start()
    clock.advance(.6)
    s.update()
    assert s.update().kind=='none'
    assert s.update().kind=='hold' and s.state=='hooked'


def test_waiting_timeout_recasts():
    clock = FakeClock()
    s = sess([(0, 0)] * 6, clock=clock, cast_seconds=0.5, recast_seconds=2)
    s.start(); clock.advance(0.6); s.update()   # waiting
    clock.advance(2.1)
    act = s.update()
    assert act.kind == 'cast' and s.state == 'casting'


def test_red_releases_then_recover_reholds():
    colors = iter({'dom':color} for color in ('white','white','red','red','white','white'))
    s = sess([(6,0)],colors)
    assert [s.update().kind for _ in range(6)]==['none','hold','release','none','none','hold']


def test_done_releases_to_idle():
    s = sess([(6,0),(6,0),(0,0)],iter([{'dom':'white'}]*3))
    assert s.update().kind=='none'
    assert s.update().kind=='hold'
    assert s.update().kind=='release' and not s.holding and s.state=='idle'


def test_hook_timeout_safety_release():
    clock = FakeClock()
    s = sess([(6, 0)] * 4, clock=clock, cast_seconds=0.5, timeout=1.0)
    s.start(); clock.advance(0.6); s.update()
    s.update()
    clock.advance(2.0)
    act = s.update()
    assert act.kind == 'release' and s.state == 'idle' and '超时' in act.reason


def test_unknown_color_releases_hold():
    colors = iter([{'dom':'white'},{'dom':'white'},{'dom':'unknown'}])
    s = sess([(6,0)],colors)
    assert s.update().kind=='none'
    assert s.update().kind=='hold'
    assert s.update().kind=='release' and not s.holding


def test_auto_hook_accepted_in_idle_without_immediate_input():
    s = sess([(6,0)])
    assert s.update().kind=='none' and s.state=='hooked'


def test_stop_releases_any_key():
    clock = FakeClock()
    s = sess([(0, 0)], clock=clock)
    s.start()
    act = s.stop()
    assert act.kind == 'release' and not s.holding and s.state == 'idle'
    assert s.stop().kind == 'none'


def test_input_sink_receives_actions():
    sent = []
    clock = FakeClock()
    observer = FishingObserver(FakeMemory([(0, 0)]).read)
    s = FishingSession(observer, input_sink=sent.append, clock=clock)
    s.start()
    assert [a.kind for a in sent] == ['cast']
    assert all(isinstance(a, Action) for a in sent)


def test_full_cycle_action_sequence():
    """完整一循环：抛竿→咬钩→收线→收尾，动作序列正确。"""
    clock = FakeClock()
    colors = iter([{'dom': 'white', 'red': 0, 'yellow': 0}] * 20)
    s = sess([(0, 0), (0, 0), (6, 0), (6, 0), (0, 0)], colors, clock, cast_seconds=0.5)
    kinds = [s.start().kind]
    kinds.append(s.update().kind)                    # casting none
    clock.advance(0.6)
    kinds.append(s.update().kind)                    # release -> waiting
    kinds.append(s.update().kind)                    # hooked hold
    kinds.append(s.update().kind)                    # second white frame
    kinds.append(s.update().kind)                    # done release
    assert kinds == ['cast', 'none', 'release', 'none', 'hold', 'release']
    assert s.state == 'idle' and not s.holding and s.casts == 1


def test_replay_real_catch_sequence():
    """回放 2026-11-22 实测的一条鱼：State 6(拉扯)→0(收尾)。"""
    clock = FakeClock()
    states = [(policy.FISHING_STATE_HOOKED, round(i * .14, 3)) for i in range(8)]
    states += [(0, 0.0) for _ in range(6)]
    white = iter([{'dom': 'white', 'red': 0, 'yellow': 0}] * 20)
    s = sess(states, white, clock)
    act = s.update()   # 空闲检测到咬钩，直接接管
    assert act.kind == 'none'
    kinds = [s.update().kind for _ in range(len(states) - 1)]
    assert 'release' in kinds
    assert s.state == 'idle' and not s.holding
