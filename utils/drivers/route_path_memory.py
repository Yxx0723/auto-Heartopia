"""Bounded, per-route successful walking traces; never stores process addresses."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from utils.core import thresholds as policy
from utils.core.memory_corridor import (append_outcome, positive, recent_times, reliability,
                                        similar, typical_seconds, valid_profile)
from utils.core.coordinate_points import validate_points
from utils.core.coordinate_route import distance, valid_position
from utils.drivers.coordinate_point_store import PointStore, atomic_write_json


class RoutePathMemory:
    schema = 'route-path-memory-v1'
    max_points = 2048
    max_total_points = 50000

    def __init__(self, route_path, data):
        validate_points(data)
        self.targets = [tuple(p['position']) for p in data['points']]
        geometry = json.dumps(self.targets, separators=(',', ':'), allow_nan=False)
        self.fingerprint = hashlib.sha256(geometry.encode()).hexdigest()
        route_path = Path(route_path)
        self.path = route_path.parent / '路线记忆' / route_path.name
        self.statistics = {}
        self.learning_summary = {}
        self.rejected = set()
        self.selected = None
        self._rejections_applied = False
        self.legs = self._load()

    def _valid_trace(self, index, points):
        return (type(index) is int and 0 <= index < len(self.targets)
                and isinstance(points, list) and 2 <= len(points) <= self.max_points
                and all(valid_position(p) for p in points)
                and math.dist(points[-1], self.targets[index]) <= 1.5
                and all(math.dist(a, b) <= 6. for a, b in zip(points, points[1:])))

    def _load(self):
        self.statistics, self.learning_summary = {}, {}
        try:
            if self.path.stat().st_size > 8 * 1024 * 1024:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if data.get('schema') != self.schema or data.get('route_fingerprint') != self.fingerprint:
                return {}
            legs = data.get('legs')
            if not isinstance(legs, dict):
                return {}
            self.statistics = data.get("statistics", {}) if isinstance(data.get("statistics"), dict) else {}
            self.learning_summary = data.get("learning_summary", {}) if isinstance(data.get("learning_summary"), dict) else {}
            result = {}
            for key, variants in legs.items():
                if not isinstance(key, str) or not key.isdecimal() or not isinstance(variants, list):
                    continue
                good = [p for p in variants[:3] if self._valid_trace(int(key), p)]
                if good:
                    result[key] = good
            return result
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            return {}

    @property
    def count(self):
        return len(self.legs)

    @staticmethod
    def _identity(path):
        return hashlib.sha256(json.dumps(path, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _length(path):
        return sum(distance(a, b) for a, b in zip(path, path[1:]))

    def _cost(self, path, offset=0):
        identity = self._identity(path)
        record = self.statistics.get(identity, {})
        record = record if isinstance(record, dict) else {}
        length = self._length(path)
        base = typical_seconds(record, length / policy.COORD_MEMORY_ESTIMATED_SPEED)
        profile = record.get('sample_times')
        measured = record.get('profile_duration')
        if valid_profile(profile, len(path), measured):
            remaining = (profile[-1]-profile[offset])/profile[-1]
        else:
            remaining = self._length(path[offset:])/length if length else 0.
        return base*remaining*reliability(record, identity in self.rejected and not self._rejections_applied)

    def _apply_rejections(self):
        self._rejections_applied = True
        for rejected in self.rejected:
            record = self.statistics.get(rejected, {})
            record = dict(record) if isinstance(record, dict) else {}
            count = record.get('blocked', 0)
            record['blocked'] = min(count + 1, 1000000000) if type(count) is int and count >= 0 else 1
            append_outcome(record, False)
            self.statistics[rejected] = record

    def flush_rejections(self):
        """Persist negative evidence even when every remaining waypoint was skipped."""
        if not self.rejected:
            return False
        with PointStore(self.path).locked():
            legs = self._load()
            self._apply_rejections()
            self._save(legs)
        return True

    def _save(self, legs):
        kept = {self._identity(p) for paths in legs.values() for p in paths}
        statistics = {key: value for key, value in self.statistics.items() if key in kept}
        try:
            atomic_write_json(self.path, {'learning_summary': self.learning_summary, 'statistics': statistics,
                'schema': self.schema, 'route_fingerprint': self.fingerprint,
                'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'legs': legs})
        except (OSError, ValueError, TypeError):
            self.legs = self._load()
            raise
        else:
            self.legs, self.statistics = legs, statistics
            self.rejected.clear()
        finally:
            self._rejections_applied = False

    def reject_selected(self):
        # Save penalties together with the next completed leg, never interrupt
        # gameplay with a disk write during obstacle recovery.
        if self.selected is not None:
            self.rejected.add(self.selected)
            self.selected = None

    def lookup(self, index, position, can_connect=None):
        self.selected = None
        choices = []
        for path in self.legs.get(str(index), ()):
            nearest = min(range(len(path) - 1), key=lambda offset: math.dist(position, path[offset]))
            for offset, point in [(nearest, path[nearest])]:
                gap = math.dist(position, point)
                if gap <= 4. and abs(position[1] - point[1]) <= policy.COORD_MEMORY_ENTRY_HEIGHT \
                        and (can_connect is None or can_connect(point)):
                    cost = self._cost(path, offset)
                    choices.append((cost + gap / policy.COORD_MEMORY_ESTIMATED_SPEED, gap, offset, path))
        if not choices:
            return []
        _, _, offset, path = min(choices, key=lambda item: (item[0], item[1], item[2]))
        self.selected = self._identity(path)
        return [tuple(p) for p in path[offset:]]

    def remember(self, index, points, duration=None, *, sample_times=None, collection_outcome=None):
        trace = [list(p) for p in points]
        if not self._valid_trace(index, trace) or sum(distance(a, b) for a, b in zip(trace, trace[1:])) < 1.:
            return False
        # Remove loops that return to the same place, so a failed local probe
        # and its backtrack do not become the preferred walk next time.
        if sample_times is None:
            # Exact returns join the same observed XYZ; nearby returns do not.
            walked, first_at = [], {}
            for point in trace:
                key = tuple(point)
                match = first_at.get(key)
                if match is not None and match < len(walked)-3:
                    for old in walked[match+1:]:
                        old_key = tuple(old)
                        if first_at.get(old_key, -1) > match:
                            first_at.pop(old_key, None)
                    del walked[match+1:]
                first_at.setdefault(key, len(walked))
                walked.append(point)
            trace = walked
        if not self._valid_trace(index, trace):
            return False
        with PointStore(self.path).locked():
            legs = self._load()
            self._apply_rejections()
            variants = legs.get(str(index), [])
            # Every completion is recorded. Keep successful alternatives instead
            # of replacing a faster corridor with the latest detour.
            canonical = next((p for p in variants if similar(p, trace)), trace)
            identity = self._identity(canonical)
            previous = self.statistics.get(identity, {})
            previous = previous if isinstance(previous, dict) else {}
            count = previous.get('successes', 0)
            count = count if type(count) is int and 0 <= count < 1000000000 else 0
            record = {**previous, 'successes': count + 1, 'length': self._length(canonical),
                      'last_used': datetime.now().astimezone().isoformat(timespec='seconds')}
            append_outcome(record, True)
            observed = recent_times(previous)
            if positive(duration):
                record['recent_seconds'] = (observed+[float(duration)])[-policy.COORD_MEMORY_RECENT_WINDOW:]
            if trace == canonical and valid_profile(sample_times, len(trace), duration):
                record['sample_times'] = list(sample_times)
                record['profile_duration'] = duration
            record['movement_evidence'] = 'continuous_coordinates_with_walk_input'
            record['collection_outcome'] = collection_outcome or 'action_completed_unconfirmed_loot'
            record['loot_confirmed'] = collection_outcome == 'confirmed_drop'
            old_time = previous.get('best_seconds')
            times = [value for value in (old_time, duration) if type(value) in (int, float)
                     and math.isfinite(value) and value > 0]
            if type(previous.get('blocked')) is int:
                record['blocked'] = previous['blocked']
            if times:
                record['best_seconds'] = min(times)
            self.statistics[identity] = record
            variants = [p for p in variants if self._identity(p) != identity]
            # Latest successful corridor is retained for changed obstacles;
            # reserve another slot for the best previous corridor.
            variants.sort(key=self._cost)
            legs[str(index)] = [canonical, *variants[:2]]
            if sum(len(p) for paths in legs.values() for p in paths) > self.max_total_points:
                self.legs = self._load()
                self._rejections_applied = False
                return False
            summary = self.learning_summary.get(str(index), {})
            count = summary.get('completed_walks', 0) if isinstance(summary, dict) else 0
            count = count if type(count) is int and 0 <= count < 1000000000 else 0
            self.learning_summary[str(index)] = {'completed_walks': count + 1,
                'last_length': self._length(trace), 'last_completed': record['last_used']}
            self._save(legs)
        return True

    def clear(self):
        with PointStore(self.path).locked():
            self.path.unlink(missing_ok=True)
            self.legs = {}
            self.statistics = {}
            self.learning_summary = {}
            self.rejected.clear()
            self.selected = None
