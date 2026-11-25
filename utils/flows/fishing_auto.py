"""自动钓鱼的观察与决策层（与输入/截屏/游戏解耦，可单测）。

分工：
- 内存（FloatComponent.State）判定咬钩/收尾 —— 已实测：0=空闲，6=拉扯中。
- 视觉（鱼线区域取色）判定张力红/黄 —— 红=松键，白/黄=按住。
- 输入通过 input_sink 注入；默认 no-op，不发送任何按键（键位需现场确认）。

本模块不打开窗口、不读进程、不发输入，只依赖注入的观察者。
"""
import time
from dataclasses import dataclass
from typing import Callable, Optional

from utils.core import thresholds as policy
from utils.core.fishing_policy import TensionControl


@dataclass(frozen=True)
class Observation:
    stage: str          # idle / hooked / done
    state: int          # FloatComponent.State 原值
    play_time: float    # 本次拉扯计时
    line_dom: str       # white / yellow / orange / red / unknown
    red_px: int
    yellow_px: int


class FishingObserver:
    """把内存状态与视觉颜色合成一条 Observation。

    read_state() -> (state:int, play_time:float)
    read_line()  -> {'dom':str, 'red':int, 'yellow':int, ...} 或 None
    """

    def __init__(self, read_state: Callable, read_line: Optional[Callable] = None):
        if not callable(read_state):
            raise ValueError('read_state 必须可调用')
        if read_line is not None and not callable(read_line):
            raise ValueError('read_line 必须可调用')
        self.read_state = read_state
        self.read_line = read_line

    def observe(self) -> Observation:
        state, play_time = self.read_state()
        state = int(state)
        if state == policy.FISHING_STATE_HOOKED:
            stage = 'hooked'
        elif state == 0:
            stage = 'idle'
        else:
            stage = 'unknown'
        dom, red, yellow = 'unknown', 0, 0
        if self.read_line is not None:
            color = self.read_line()
            if color:
                dom = color.get('dom', 'unknown')
                red = int(color.get('red', 0))
                yellow = int(color.get('yellow', 0))
        return Observation(stage, state, float(play_time), dom, red, yellow)


@dataclass(frozen=True)
class Action:
    kind: str           # cast / hold / release / none
    reason: str


class FishingSession:
    """全流程状态机：抛竿 → 等咬钩 → 收线（张力控制）→ 收尾 → 重抛。

    输入通过 input_sink 注入；默认只记录不发送输入。
    cast  = 按下鼠标左键开始瞄准
    release = 松开鼠标左键（抛竿 / 收尾 / 红时放线）
    hold  = 按下鼠标左键收线

    张力策略与正式运行器共用。缺少视觉数据时释放，不能授权收线。
    """

    def __init__(self, observer: FishingObserver, input_sink: Optional[Callable] = None,
                 clock: Callable = None, timeout: float = None,
                 cast_seconds: float = None, recast_seconds: float = None):
        self.observer = observer
        self.input_sink = input_sink if callable(input_sink) else (lambda a: None)
        self.clock = clock if callable(clock) else time.monotonic
        self.timeout = timeout if timeout is not None else policy.FISHING_HOOK_TIMEOUT_SECONDS
        self.cast_seconds = cast_seconds if cast_seconds is not None else policy.FISHING_CAST_SECONDS
        self.recast_seconds = recast_seconds if recast_seconds is not None else policy.FISHING_RECAST_SECONDS
        self.state = 'idle'          # idle / casting / waiting / hooked
        self.holding = False         # 左键当前是否按下
        self.phase_at = None         # 当前阶段起始时刻
        self.hooked_at = None
        self.casts = 0               # 已抛竿次数
        self.actions = []
        self.tension = TensionControl()

    def _emit(self, kind, reason):
        action = Action(kind, reason)
        self.actions.append(action)
        self.input_sink(action)
        if kind in ('cast', 'hold'):
            self.holding = True
        elif kind == 'release':
            self.holding = False
        return action

    def _enter(self, state, now):
        self.state = state
        self.phase_at = now

    def start(self):
        """从空闲启动一次抛竿。返回 cast 动作。"""
        if self.state != 'idle' or self.holding:
            return Action('none', '非空闲，忽略启动')
        now = self.clock()
        self._enter('casting', now)
        return self._emit('cast', '按下左键瞄准')

    def stop(self):
        """安全收尾：任何按下状态都松开，回到 idle。"""
        if self.holding:
            self.state = 'idle'
            self.hooked_at = None
            return self._emit('release', '停止，安全松开左键')
        self.state = 'idle'
        self.hooked_at = None
        return Action('none', '停止（未持键）')

    def update(self) -> Action:
        obs = self.observer.observe()
        now = self.clock()

        if self.state == 'idle':
            # 空闲时若检测到咬钩（例如用户手动抛的），直接接管收线
            if obs.stage == 'hooked':
                self._enter('hooked', now)
                self.hooked_at = now
                self.tension = TensionControl()
                return self._adjust(obs,now)
            return Action('none', '待机')

        if self.state == 'casting':
            # 按住瞄准达到时长 → 松开抛竿
            if now - self.phase_at >= self.cast_seconds:
                self.casts += 1
                self._enter('waiting', now)
                return self._emit('release', f'抛竿#{self.casts}（瞄准{self.cast_seconds}s）')
            return Action('none', '瞄准中')

        if self.state == 'waiting':
            if obs.stage == 'hooked':
                self._enter('hooked', now)
                self.hooked_at = now
                self.tension = TensionControl()
                return self._adjust(obs,now)
            if obs.stage == 'idle' and now - self.phase_at >= self.recast_seconds:
                self._enter('casting', now)
                return self._emit('cast', '久未咬钩，重新抛竿')
            return Action('none', '等待咬钩')

        # hooked：拉扯中
        if obs.stage == 'idle':
            self._enter('idle', now)
            self.hooked_at = None
            act = self._emit('release', '拉扯结束（State=0），钓获未确认') if self.holding \
                else Action('none', '拉扯结束')
            return act

        if self.hooked_at is not None and now - self.hooked_at > self.timeout:
            self._enter('idle', now)
            self.hooked_at = None
            return self._emit('release', '咬钩超时，安全松开')

        return self._adjust(obs,now)

    def _adjust(self, obs, now):
        action = self.tension.evaluate(obs.line_dom if obs.stage=='hooked' else 'unknown',
                                       self.holding,now)
        if action=='press':
            return self._emit('hold','张力恢复，继续收线')
        if action in ('release','timeout'):
            if action=='timeout':
                self._enter('idle',now)
                self.hooked_at = None
            if self.holding:
                return self._emit('release','张力不安全，松开左键')
        return Action('none','等待有效张力')
