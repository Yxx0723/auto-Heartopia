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
            held = self.deadline is not None or active
            self.deadline = None
            self.device.release_all()
            if held:
                self.counts[reason] += 1
                self.recent.append({'at': self.clock(), 'reason': reason})

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
                    'permit_remaining': max(0., self.deadline-self.clock()) if self.deadline is not None else 0.}

    def close(self):
        self.stop.set()
        try:
            self.release('cleanup')
        finally:
            if self.thread is not None:
                self.thread.join(timeout=policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
