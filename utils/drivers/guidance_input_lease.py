"""Short movement permits; watchdog never reads game memory or presses a key.

The controller owns permission renewal after validation. All input mutations
share the lock, so a watchdog release cannot race a new key-down. Callbacks used
by poll must be bounded local controls/window queries, not memory readers.
"""
from collections import Counter, deque
import math
import threading

from utils.core import thresholds as policy


class GuidanceInputLease:
    def __init__(self, device, controls, window, size, clock):
        self.device, self.controls, self.window = device, controls, window
        self.size, self.clock = size, clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.deadline = None
        self.tripped = False
        self.error = None
        self.counts, self.recent = Counter(), deque(maxlen=32)
        self.phase, self.phase_at = 'idle', self.clock()
        self.observed_held = False
        self.gap_since = self.gap_reason = None
        self.gaps = deque(maxlen=32)
        self.gap_count, self.gap_total, self.gap_max = 0, 0., 0.

    def set_phase(self, phase):
        # Attribution only. Updating phase must never renew an input permit.
        with self.lock:
            previous = self.phase
            self.phase, self.phase_at = phase, self.clock()
            return previous

    def start(self):
        self.thread = threading.Thread(target=self._run, name='guidance-input-watchdog', daemon=True)
        self.thread.start()

    def arm(self, deadline):
        # Caller holds lock across key changes and arm. Cannot resurrect a permit
        # revoked during a blocked read without the controller handling the trip.
        if self.stop.is_set() or self.tripped or self.error or not math.isfinite(deadline) or deadline <= self.clock():
            raise ValueError('移动许可已失效，等待重新核对')
        self.deadline = deadline

    def release(self, reason, active=False):
        with self.lock:
            actual = getattr(self.device, 'keys', None)
            held = bool(actual) if isinstance(actual, (set, frozenset)) else \
                self.deadline is not None or (active and not self.tripped)
            self.deadline = None
            self.device.release_all()
            self.observed_held = False
            if held:
                self.counts[reason] += 1
                released_at = self.clock()
                self.recent.append({'at': released_at, 'reason': reason, 'phase': self.phase,
                                    'phase_seconds': max(0., released_at-self.phase_at)})
                if self.gap_since is None:
                    self.gap_since, self.gap_reason = released_at, reason

    def observe_keys(self, keys):
        """Called after successful input, under the same serialized input lock."""
        with self.lock:
            if not keys and self.observed_held and self.gap_since is None:
                self.gap_since, self.gap_reason = self.clock(), 'steering_idle'
                self.counts['steering_idle'] += 1
                self.recent.append({'at': self.gap_since, 'reason': 'steering_idle'})
            self.observed_held = bool(keys)
            if keys and self.gap_since is not None:
                now = self.clock()
                gap = max(0., now-self.gap_since)
                self.gaps.append({'released_at': self.gap_since, 'resumed_at': now,
                                  'seconds': gap, 'reason': self.gap_reason})
                self.gap_count += 1
                self.gap_total += gap
                self.gap_max = max(self.gap_max, gap)
                self.gap_since = self.gap_reason = None

    def poll(self):
        with self.lock:
            if self.deadline is None:
                return
        # Do not hold the input lock across OS queries. Revalidate the permit
        # under lock afterwards; an expired permit is never renewed by this task.
        try:
            running, stopped = self.controls.snapshot()
            reason = ('stop' if stopped else 'pause' if not running else
                      'focus_or_size' if not self.window.foreground() or self.window.size() != self.size else None)
        except Exception:
            reason = 'watchdog_query_error'
        with self.lock:
            if self.deadline is None:
                return
            reason = reason or ('lease_expired' if self.clock() >= self.deadline else None)
            if reason:
                self.tripped = True
                try:
                    self.release(reason)
                except Exception as exc:
                    self.error = exc

    def _run(self):
        while not self.stop.wait(policy.COORD_GUIDANCE_INPUT_POLL_SECONDS):
            self.poll()

    def metrics(self):
        with self.lock:
            return {'release_counts': dict(self.counts), 'recent_releases': list(self.recent),
                    'resumed_gap_count': self.gap_count, 'resumed_gap_total_seconds': self.gap_total,
                    'longest_resumed_gap_seconds': self.gap_max, 'recent_input_gaps': list(self.gaps),
                    'current_phase': self.phase,
                    'current_release_seconds': max(0., self.clock()-self.gap_since) if self.gap_since is not None else 0.,
                    'permit_remaining': max(0., self.deadline-self.clock()) if self.deadline is not None else 0.}

    def close(self):
        self.stop.set()
        try:
            self.release('cleanup')
        finally:
            if self.thread is not None:
                self.thread.join(timeout=policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
