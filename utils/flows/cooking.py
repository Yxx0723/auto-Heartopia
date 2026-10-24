"""Interruptible single-stove runner with injectable vision, inventory and input."""
import math

from utils.core.cooking import CookingSession
from utils.core import thresholds as policy


def run_cooking(session, window, controls, device, observe, inventory, clock, report,
                poll_seconds=policy.COOKING_POLL_SECONDS, inventory_seconds=policy.COOKING_INVENTORY_SECONDS):
    if not isinstance(session, CookingSession):
        raise ValueError('烹饪任务无效')
    size = window.size()
    previous_frame = None
    stable = 0
    inventory_at = -float('inf')
    stock = None
    was_running = False

    def guard():
        running, stopped = controls.snapshot()
        if stopped:
            return 'stopped'
        if not running:
            return 'paused'
        if not window.foreground() or window.size() != size:
            controls.pause()
            return 'paused'
        return 'running'

    def wait(seconds):
        deadline = clock.monotonic()+seconds
        while clock.monotonic() < deadline:
            if guard() != 'running':
                return
            clock.sleep(min(.02, max(0., deadline-clock.monotonic())))

    def same(first, second):
        if first is None or second is None or first.stage != second.stage or first.key != second.key:
            return False
        if first.target is None or second.target is None:
            return first.target is second.target
        return (len(first.target) == len(second.target) == 2 and
                all(type(v) in (int, float) and math.isfinite(v) for v in (*first.target, *second.target)) and
                math.dist(first.target, second.target) <= policy.COOKING_TARGET_DRIFT_PIXELS)

    def click(target, key=None):
        # Input is always checked immediately before dispatch; no stale delayed jobs.
        if guard() != 'running':
            return False
        if not isinstance(target, (tuple, list)) or len(target) != 2 or any(
                type(v) not in (int, float) for v in target):
            raise ValueError('烹饪点击目标未确认')
        x, y = target
        if not (0 <= x < size[0] and 0 <= y < size[1]):
            raise ValueError('烹饪目标超出游戏客户区')
        try:
            if key is not None:
                if key not in ('q', 'enter'):
                    raise ValueError('烹饪按键未经核对')
                device.execute({'type': 'key_down', 'key': key})
            else:
                device.execute({'type': 'mouse_down', 'button': 'left', 'x': round(x), 'y': round(y)})
            wait(.04)
        finally:
            device.release_all()
        return True

    try:
        while session.state not in session.terminal:
            state = guard()
            if state == 'stopped':
                report('stopped', '烹饪已停止，未完成锅保留记录', **session.record())
                return False
            if state != 'running':
                device.release_all()
                if was_running:
                    report('paused', '烹饪已暂停；锅内计时可能继续，恢复时重新识别')
                was_running = False
                previous_frame, stable = None, 0
                stock, inventory_at = None, -float('inf')
                clock.sleep(.02)
                continue
            was_running = True
            now = clock.monotonic()
            if now-inventory_at >= inventory_seconds:
                try:
                    stock = inventory()
                except (ValueError, OSError):
                    stock = None
                inventory_at = now
            observation = observe(window.snapshot())
            # Independent fresh captures; unknown never re-arms a stir prompt.
            stable = stable+1 if same(observation, previous_frame) and observation.stage != 'unknown' else 1
            previous_frame = observation
            if observation.stage == 'unknown' or stable < 2:
                # Timeouts still advance even while vision cannot classify the frame.
                from utils.core.cooking import Observation
                session.update(Observation('unknown'), stock, now)
                wait(poll_seconds)
                continue
            # Starts use a fresh owned inventory snapshot, not the polling cache.
            if observation.stage == 'menu' and (session.pot is None or session.pot['resolved']):
                try:
                    stock = inventory()
                except (ValueError, OSError):
                    stock = None
                inventory_at = clock.monotonic()
            # Validate target before reserving an action in the pure state machine.
            if observation.stage in ('menu', 'stir', 'done', 'interact') and observation.target is None:
                wait(poll_seconds)
                continue
            action = session.update(observation, stock, clock.monotonic())
            record = session.record()
            report('cooking', session.reason, **record)
            if action:
                # Persist reserved action before dispatch, so errors cannot cause a retry.
                report('cooking_action', action, action=action, **record)
                fresh = observe(window.snapshot()) if guard() == 'running' else None
                if not same(fresh, observation):
                    session.state, session.reason = 'unconfirmed', '操作前画面变化，未派发输入或重试投料'
                elif not click(fresh.target, fresh.key):
                    session.state, session.reason = 'unconfirmed', '操作派发时暂停或失焦，未自动重试投料'
            wait(poll_seconds)
        report('finished' if session.state == 'finished' else 'partial', session.reason, **session.record())
        return session.state == 'finished'
    finally:
        device.release_all()
