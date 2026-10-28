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
        result = {key: getattr(error, key) for key in ('kind', 'stage', 'retryable', 'scope_verified')}
        if hasattr(error, 'code'):
            result['code'] = error.code
        return result
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


def read_collection(resolve, read, guard, budget, *, stage, charge_mutation_guard=False):
    """Retry only a verified mutation, re-resolving the owner field each time."""
    last = None
    for attempt in range(policy.COORD_SCENE_COLLECTION_ATTEMPTS):
        budget.check()
        if attempt and not budget.reserve():
            break
        started, spent_before = budget.clock(), budget.spent
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
            # Linked reads perform their expensive proof only after mutation.
            # Charge that proof before starting a new retry; do not double-count
            # work already charged by a nested registry recovery.
            proof_started, proof_spent = budget.clock(), budget.spent
            try:
                guard()  # Failure is retryable only while the trusted scope agrees.
            finally:
                if charge_mutation_guard and not attempt:
                    budget.spent += max(0., budget.clock()-proof_started-(budget.spent-proof_spent))
            last = SceneReadIssue(str(exc), kind='mutation', stage=stage,
                                  retryable=True, scope_verified=True)
        finally:
            if attempt:
                budget.spent += max(0., budget.clock()-started-(budget.spent-spent_before))
    raise last


def contextual_issue(error, stage):
    """Localize a failure without logging arbitrary exception text or pointers."""
    if isinstance(error, SceneReadIssue) and error.kind in ('cancelled', 'scope_changed'):
        return error
    summary = issue_summary(error)
    result = SceneReadIssue('场景子步骤读取未确认', kind=summary['kind'], stage=stage,
                            retryable=summary['retryable'], scope_verified=summary['scope_verified'])
    result.code = summary.get('code') or {
        '场景集合类型不符': 'collection_type', '场景集合数量无效': 'collection_count',
        '场景集合布局无效': 'collection_layout', '场景集合字段范围无效': 'collection_field_bounds',
        '场景集合计数字段无效': 'collection_counter_layout', '场景集合过大': 'collection_capacity',
        '场景对象归属已变化': 'object_domain', '场景引用无效': 'invalid_reference',
        '场景数据正在变化': 'short_read', '场景数据范围无效': 'read_bounds',
    }.get(str(error), summary['kind'])
    return result


def read_linked_collection(resolve, read, verify_scope, budget, *, stage):
    """Re-resolve an owner link; only proven mutations invoke scope recovery.

    Stable reads use the existing container stamp plus a post-read link check.
    World/membership checks run on the exceptional path, not per array normally.
    Recovery shares the existing per-snapshot attempts/time budget.
    """
    address, after_read, tainted = None, False, False
    def reference_failure(exc):
        issue = contextual_issue(exc, stage)
        return issue.kind == 'invalid_reference' or getattr(issue, 'code', None) in ('short_read', 'read_bounds')
    def witness():
        budget.check()
        if budget.retries >= budget.max_retries or budget.spent >= budget.max_seconds:
            return None
        started, spent_before = budget.clock(), budget.spent
        try:
            value = resolve()
            budget.check()
            return value if type(value) is int and value > 0 else None
        finally:
            budget.spent += max(0., budget.clock()-started-(budget.spent-spent_before))
    def link():
        nonlocal tainted
        try:
            return resolve()
        except (OSError, ValueError) as exc:
            if isinstance(exc, SceneReadIssue) and exc.kind == 'mutation' and exc.retryable:
                tainted = True
            elif reference_failure(exc):
                try:
                    replacement = witness()
                except (OSError, ValueError) as proof:
                    if isinstance(proof, SceneReadIssue) and proof.kind in ('cancelled', 'scope_changed'):
                        raise
                    raise exc
                if replacement is not None:
                    tainted = True
                    raise SceneReadIssue('场景集合引用从无效变为有效，等待归属核对', kind='mutation',
                                         stage=stage, retryable=True) from exc
            raise
    def resolve_current():
        nonlocal address, after_read
        address, after_read = link(), False
        return address
    def read_current(at):
        nonlocal after_read, tainted
        try:
            result = read(at)
        except (OSError, ValueError) as exc:
            if isinstance(exc, SceneReadIssue) and exc.kind == 'mutation' and exc.retryable:
                tainted = True
            elif reference_failure(exc):
                # An old array can become unreadable after its owner publishes
                # a replacement. Error kind alone is NOT mutation evidence.
                # Require a different non-null link, then read_collection's
                # exceptional guard proves world AND membership before retry.
                budget.check()
                try:
                    replacement = witness()
                except (OSError, ValueError) as proof:
                    if isinstance(proof, SceneReadIssue) and proof.kind in ('cancelled', 'scope_changed'):
                        raise
                    raise exc
                if at and replacement and replacement != at:
                    tainted = True
                    raise SceneReadIssue('场景集合引用在失败读取期间发生变化', kind='mutation',
                                         stage=stage, retryable=True) from exc
            raise
        after_read = True
        return result
    def guard():
        nonlocal after_read, tainted
        if tainted:
            verify_scope()
        if after_read:
            after_read = False
            if link() != address:
                tainted = True
                raise SceneReadIssue('场景集合引用在读取时发生变化', kind='mutation', stage=stage, retryable=True)
    return read_collection(resolve_current, read_current, guard, budget, stage=stage, charge_mutation_guard=True)


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
