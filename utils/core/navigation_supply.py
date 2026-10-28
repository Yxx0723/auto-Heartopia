"""Elapsed-time collision supply accounting, independent of UI and input.

No TTL extension or old-snapshot fallback. Call under the worker's lock.
The bounded latency window is instrumentation, not a game capability model.
"""
from collections import deque
import math

from utils.core import thresholds as policy
from utils.core.scene_read import navigation_quality_valid


def navigation_expiry(scene, published_at):
    if not scene or scene.get('state') != 'ready' or scene.get('world_state', 'ready') != 'ready' \
            or not navigation_quality_valid(scene, published_at):
        return None
    if 'quality' not in scene:  # Retain the legacy fixture/provider contract.
        return published_at + policy.COORD_NAV_SCENE_MAX_AGE
    return min(scene['quality']['parts'][key]['observed_from']
               for key in ('managed_collisions', 'native_collisions')) + policy.COORD_NAV_SCENE_MAX_AGE


class SnapshotSupply:
    def __init__(self):
        self.started = self.at = self.deadline = self.gap_since = None
        self.active = False
        self.demand = self.usable = self.longest_gap = self.last_gap = 0.
        self.previous_deadline = self.overlap = self.remaining = None
        self.scans = deque(maxlen=256)
        self.scan_count = 0

    def start(self, now):
        if not self.active:
            self.started = now if self.started is None else self.started
            self.at, self.active = now, True
            if self.deadline is None or self.deadline < now:
                self.gap_since = now

    def pause(self, now):
        self._advance(now)
        self.active, self.gap_since = False, None

    def _advance(self, now):
        if self.at is None:
            return
        if not math.isfinite(now) or now < self.at:
            raise ValueError('Navigation supply clock moved backwards')
        if not self.active:
            self.at = now
            return
        self.demand += now-self.at
        valid_to = self.at if self.deadline is None else max(self.at, min(now, self.deadline))
        self.usable += valid_to-self.at
        if valid_to < now:
            if self.gap_since is None:
                self.gap_since = valid_to
            self.longest_gap = max(self.longest_gap, now-self.gap_since)
        self.at = now

    def publish(self, scene, now, duration):
        self._advance(now)
        deadline = navigation_expiry(scene, now)
        quality = scene.get('quality') if scene else None
        parts = quality.get('parts') if isinstance(quality, dict) else None
        starts = [(parts[key].get('observed_from') if isinstance(parts.get(key), dict) else None) for key in
                  ('managed_collisions', 'native_collisions')] if isinstance(parts, dict) else []
        self.remaining = (min(starts)+policy.COORD_NAV_SCENE_MAX_AGE-now
                          if len(starts) == 2 and all(type(x) in (int, float) and math.isfinite(x) for x in starts)
                          else None)
        if deadline is not None:
            self.overlap = None if self.previous_deadline is None else self.previous_deadline-now
            self.previous_deadline = deadline
            if self.gap_since is not None:
                self.last_gap = now-self.gap_since
                self.longest_gap = max(self.longest_gap, self.last_gap)
                self.gap_since = None
        elif self.active and self.gap_since is None:
            self.gap_since = now  # An explicit failure revokes even an unexpired snapshot.
        self.deadline = deadline
        self.scans.append(max(0., duration))
        self.scan_count += 1

    def metrics(self, now):
        self._advance(now)
        demand = self.demand
        durations = sorted(self.scans)
        def percentile(q):
            return durations[max(0, math.ceil(q*len(durations))-1)] if durations else None
        return {'snapshot_demand_seconds': demand,
                'snapshot_usable_seconds': self.usable,
                'snapshot_usable_ratio': self.usable/demand if demand else None,
                'snapshot_gap_seconds': self.last_gap,
                'snapshot_current_gap_seconds': 0. if self.gap_since is None else now-self.gap_since,
                'snapshot_longest_gap_seconds': self.longest_gap,
                'snapshot_unusable_seconds': max(0., demand-self.usable),
                'snapshot_remaining_ttl': self.remaining,
                'snapshot_overlap_margin': self.overlap,
                'scan_count': self.scan_count, 'scan_window_count': len(durations),
                'scan_duration_p50': percentile(.5), 'scan_duration_p95': percentile(.95),
                'scan_duration_p99': percentile(.99),
                'scan_duration_max': durations[-1] if durations else None}
