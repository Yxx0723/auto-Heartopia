"""Read-only scene error semantics; unknown failures retain legacy safeguards."""
import math
import time

from utils.core import thresholds as policy


class SceneReadIssue(ValueError):
    def __init__(self, message, *, kind, stage='metadata', retryable=False, scope_verified=False):
        super().__init__(message)
        self.kind, self.stage = kind, stage
        self.retryable, self.scope_verified = retryable, scope_verified


def issue_summary(error):
    """Safe structured diagnostics, without pointer values or raw exception text."""
    if isinstance(error, SceneReadIssue):
        return {key: getattr(error, key) for key in ('kind', 'stage', 'retryable', 'scope_verified')}
    return {'kind': 'io_error' if isinstance(error, OSError) else 'unknown',
            'stage': 'snapshot', 'retryable': False, 'scope_verified': False}


class RecoveryBudget:
    """Shared extra-work budget, not a hard timeout for an OS read call."""
    def __init__(self, *, clock=time.monotonic, cancelled=lambda: False,
                 max_retries=policy.COORD_SCENE_READ_EXTRA_ATTEMPTS,
                 max_seconds=policy.COORD_SCENE_READ_EXTRA_SECONDS):
        self.clock, self.cancelled = clock, cancelled
        self.max_retries, self.max_seconds = max_retries, max_seconds
        self.retries, self.spent = 0, 0.

    def check(self):
        if self.cancelled():
            raise SceneReadIssue('场景读取已取消', kind='cancelled', stage='control')

    def reserve(self):
        self.check()
        if self.retries >= self.max_retries or self.spent >= self.max_seconds:
            return False
        self.retries += 1
        return True


def read_collection(resolve, read, guard, budget, *, stage):
    """Retry only a verified mutation, re-resolving the owner field each time."""
    last = None
    for attempt in range(policy.COORD_SCENE_COLLECTION_ATTEMPTS):
        budget.check()
        if attempt and not budget.reserve():
            break
        started = budget.clock()
        try:
            guard()
            result = read(resolve())
            budget.check()
            guard()
            return result
        except SceneReadIssue as exc:
            if exc.kind != 'mutation' or not exc.retryable:
                raise
            budget.check()
            guard()  # Failure is retryable only while the trusted scope agrees.
            last = SceneReadIssue(str(exc), kind='mutation', stage=stage,
                                  retryable=True, scope_verified=True)
        finally:
            if attempt:
                budget.spent += max(0., budget.clock()-started)
    raise last


def navigation_quality_valid(scene, now):
    """Additional production gate; old fixtures keep their existing semantics."""
    if 'quality' not in scene:
        return True
    quality = scene['quality']
    if not isinstance(quality, dict) or quality.get('navigation_eligible') is not True:
        return False
    def timestamp(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= now
    root_at = quality.get('root_verified_at')
    parts = quality.get('parts')
    if not timestamp(root_at) or not isinstance(parts, dict):
        return False
    for name in ('managed_collisions', 'native_collisions'):
        part = parts.get(name)
        if not isinstance(part, dict) or part.get('state') != 'complete':
            return False
        start, end = part.get('observed_from'), part.get('observed_to')
        if not timestamp(start) or not timestamp(end) or not start <= end <= root_at \
                or now-start > policy.COORD_NAV_SCENE_MAX_AGE:
            return False
    return True
