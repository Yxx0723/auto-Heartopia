"""Bounded, per-route successful walking traces; never stores process addresses."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from utils.core.coordinate_points import validate_points
from utils.core.coordinate_route import distance, valid_position
from utils.drivers.coordinate_point_store import PointStore, atomic_write_json


class RoutePathMemory:
    schema = 'route-path-memory-v1'
    max_points = 2048

    def __init__(self, route_path, data):
        validate_points(data)
        self.targets = [tuple(p['position']) for p in data['points']]
        geometry = json.dumps(self.targets, separators=(',', ':'), allow_nan=False)
        self.fingerprint = hashlib.sha256(geometry.encode()).hexdigest()
        route_path = Path(route_path)
        self.path = route_path.parent / '路线记忆' / route_path.name
        self.legs = self._load()

    def _valid_trace(self, index, points):
        return (type(index) is int and 0 <= index < len(self.targets)
                and isinstance(points, list) and 2 <= len(points) <= self.max_points
                and all(valid_position(p) for p in points)
                and math.dist(points[-1], self.targets[index]) <= 1.5
                and all(math.dist(a, b) <= 6. for a, b in zip(points, points[1:])))

    def _load(self):
        try:
            if self.path.stat().st_size > 8 * 1024 * 1024:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if data.get('schema') != self.schema or data.get('route_fingerprint') != self.fingerprint:
                return {}
            legs = data.get('legs')
            if not isinstance(legs, dict):
                return {}
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

    def lookup(self, index, position):
        choices = []
        for path in self.legs.get(str(index), ()):
            for offset, point in enumerate(path[:-1]):
                gap = math.dist(position, point)
                if gap <= 4. and abs(position[1] - point[1]) <= 2.:
                    choices.append((gap, offset, path))
        if not choices:
            return []
        _, offset, path = min(choices, key=lambda item: (item[0], item[1]))
        return [tuple(p) for p in path[offset:]]

    def remember(self, index, points):
        trace = [list(p) for p in points]
        if not self._valid_trace(index, trace) or sum(distance(a, b) for a, b in zip(trace, trace[1:])) < 1.:
            return False
        # Remove loops that return to the same place, so a failed local probe
        # and its backtrack do not become the preferred walk next time.
        walked = []
        for point in trace:
            match = next((i for i, old in enumerate(walked[:-3]) if math.dist(point, old) <= .4), None)
            if match is not None:
                del walked[match + 1:]
            walked.append(point)
        trace = walked
        if not self._valid_trace(index, trace):
            return False
        with PointStore(self.path).locked():
            legs = self._load()
            variants = legs.get(str(index), [])
            # Replace a walk from the same entry, retain up to two other entries.
            variants = [p for p in variants if math.dist(p[0], trace[0]) > 3.]
            legs[str(index)] = [trace, *variants[:2]]
            if sum(len(p) for paths in legs.values() for p in paths) > 50000:
                return False
            atomic_write_json(self.path, {'schema': self.schema, 'route_fingerprint': self.fingerprint,
                'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'legs': legs})
            self.legs = legs
        return True

    def clear(self):
        with PointStore(self.path).locked():
            self.path.unlink(missing_ok=True)
            self.legs = {}
