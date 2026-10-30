"""Optional local source geometry: bounded advisory costs, never ground truth.

Visual water is not a swimming boundary. Source collision meshes without an
active-shape association do not grant bridge access or create hard blockers.
The cache contains world XYZ, Y-up, and is gated by build and verified main map.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

from utils.core import thresholds as policy


def default_reference_path():
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    return root / 'runtime' / 'navigation' / 'two-lakes.json'


def validate_reference(value):
    """Validate and detach a bounded snapshot; never trust a supplied revision."""
    def numbers(values, count):
        return isinstance(values, (list, tuple)) and len(values) == count and all(
            type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e6 for v in values)
    if not isinstance(value, dict) or value.get('schema') != 'navigation-reference-v1':
        raise ValueError('Invalid navigation reference schema')
    build = value.get('game_hash')
    if not isinstance(build, str) or len(build) != 64 or any(c not in '0123456789abcdef' for c in build):
        raise ValueError('Invalid build fingerprint')
    bounds = value.get('bounds_xz')
    if value.get('scene') != 'startown' or not numbers(bounds, 4) or not (
            0 < bounds[1]-bounds[0] <= 1100 and 0 < bounds[3]-bounds[2] <= 1100):
        raise ValueError('Invalid reference scope')
    layers = value.get('layers')
    if not isinstance(layers, (list, tuple)) or not 1 <= len(layers) <= 16:
        raise ValueError('Invalid reference layers')
    clean, face_count, vertex_count = [], 0, 0
    for layer in layers:
        if not isinstance(layer, dict) or layer.get('kind') not in ('water', 'mountain', 'bridge'):
            raise ValueError('Invalid reference kind')
        source = layer.get('source')
        vertices, faces = layer.get('vertices'), layer.get('triangles')
        if not isinstance(source, str) or not 1 <= len(source) <= 512 or not isinstance(vertices, (list, tuple)) \
                or not isinstance(faces, (list, tuple)):
            raise ValueError('Missing geometry provenance')
        vertex_count += len(vertices)
        face_count += len(faces)
        if vertex_count > policy.COORD_REFERENCE_MAX_VERTICES or face_count > policy.COORD_REFERENCE_MAX_FACES:
            raise ValueError('Reference geometry budget exceeded')
        if not vertices or any(not numbers(v, 3) or not (
                bounds[0]-1e-6 <= v[0] <= bounds[1]+1e-6 and bounds[2]-1e-6 <= v[2] <= bounds[3]+1e-6)
                for v in vertices):
            raise ValueError('Invalid reference vertex')
        if any(not isinstance(f, (list, tuple)) or len(f) != 3 or any(
                type(i) is not int or not 0 <= i < len(vertices) for i in f) or len(set(f)) != 3 for f in faces):
            raise ValueError('Invalid reference face')
        clean.append({'kind': layer['kind'], 'source': source,
                      'vertices': tuple(tuple(v) for v in vertices), 'triangles': tuple(tuple(f) for f in faces)})
    result = {'schema': value['schema'], 'game_hash': build, 'scene': 'startown',
              'bounds_xz': tuple(bounds), 'layers': tuple(clean)}
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'),
                                                  allow_nan=False).encode()).hexdigest()
    return result


def load_reference(path, game_hash, main_map_ready, *, scene_name='startown'):
    """Called when the scene reader opens, never per steering edge or UI frame."""
    if main_map_ready is not True or scene_name != 'startown':
        return None
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(policy.COORD_REFERENCE_MAX_BYTES + 1)
        if len(raw) > policy.COORD_REFERENCE_MAX_BYTES:
            return None
        result = validate_reference(json.loads(raw))
        return result if result['game_hash'] == game_hash else None
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def _clip_height(points, boundary, sign):
    result = []
    for a, b in zip(points, points[1:] + points[:1]):
        da, db = sign*(a[1]-boundary), sign*(b[1]-boundary)
        if da >= 0:
            result.append(a)
        if (da >= 0) != (db >= 0):
            t = da/(da-db)
            result.append(tuple(x+(y-x)*t for x, y in zip(a, b)))
    return result


def _hull(points, clearance):
    points = sorted(set((p[0], p[2]) for p in points))
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def chain(values):
        result = []
        for p in values:
            while len(result) > 1 and cross(result[-2], result[-1], p) <= 1e-10:
                result.pop()
            result.append(p)
        return result
    hull = chain(points)[:-1] + chain(list(reversed(points)))[:-1]
    if len(hull) >= 3:
        return tuple(hull), clearance
    if len(points) < 2:
        return (), clearance
    a, b = points[0], points[-1]
    length = math.dist(a, b)
    if length < 1e-9:
        return (), clearance
    dx, dz = (b[0]-a[0])/length, (b[1]-a[1])/length
    # A vertical triangle projects to a line, not a missing polygon. Expand
    # this line by the existing agent padding, without extruding its Y range.
    c = clearance
    return ((a[0]-dx*c+dz*c, a[1]-dz*c-dx*c),
            (b[0]+dx*c+dz*c, b[1]+dz*c-dx*c),
            (b[0]+dx*c-dz*c, b[1]+dz*c+dx*c),
            (a[0]-dx*c-dz*c, a[1]-dz*c+dx*c)), 0.


def _interval(start, end, polygon, padding=0.):
    """Exact parameter interval in a CCW convex footprint (expanded/inset)."""
    low, high = 0., 1.
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dz = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dz)
        if length < 1e-12:
            continue
        initial = dx*(start[1]-a[1])-dz*(start[0]-a[0])+padding*length
        delta = dx*(end[1]-start[1])-dz*(end[0]-start[0])
        if abs(delta) < 1e-12:
            if initial < 0:
                return None
        elif delta > 0:
            low = max(low, -initial/delta)
        else:
            high = min(high, -initial/delta)
        if high <= low:
            return None
    return (low, high) if high > low else None


def _union(intervals):
    result = []
    for low, high in sorted(intervals):
        if result and low <= result[-1][1]+1e-10:
            result[-1] = result[-1][0], max(result[-1][1], high)
        else:
            result.append((low, high))
    return result


class ReferenceRisks:
    """Height-clipped, locally indexed risk exposure, unioned per source kind.

    Summing triangle counts biases dense meshes and path simplification. Instead
    integrate unioned exposure lengths, so a split edge has the same cost.
    No source mesh is interpreted as proof of standing or ground continuity.
    """
    def __init__(self, reference, height, origin, radius, bridge_decks=()):
        self.entries, self.bins, self.decks = [], {}, []
        self.cost_cache = {}
        self.size = policy.COORD_REFERENCE_BIN_SIZE
        for deck in bridge_decks:
            polygon, _ = _hull([(x, 0., z) for x, z in deck['polygon']], 0.)
            if polygon:
                self.decks.append(polygon)
        if not reference:
            return
        weights = {'water': policy.COORD_REFERENCE_WATER_WEIGHT,
                   'mountain': policy.COORD_REFERENCE_WALL_WEIGHT,
                   'bridge': policy.COORD_REFERENCE_BRIDGE_WEIGHT}
        left, right = origin[0]-radius, origin[0]+radius
        low, high = origin[2]-radius, origin[2]+radius
        for layer in reference['layers']:
            kind, vertices = layer['kind'], layer['vertices']
            for face in layer['triangles']:
                points = [vertices[i] for i in face]
                # Height clipping stays inside the original triangle. Even a
                # degenerate projected hull expands by at most sqrt(2)*agent
                # clearance per axis; 2*clearance is a conservative broad phase.
                margin = 2*policy.COORD_NAV_CLEARANCE
                xs, zs = [p[0] for p in points], [p[2] for p in points]
                if max(xs)+margin < left or min(xs)-margin > right \
                        or max(zs)+margin < low or min(zs)-margin > high:
                    continue
                ys = [p[1] for p in points]
                lower, upper = (height-policy.COORD_REFERENCE_WATER_HEIGHT_BAND,
                                height+policy.COORD_REFERENCE_WATER_HEIGHT_BAND) if kind == 'water' else (
                    height+policy.COORD_NAV_GROUND_CLEARANCE, height+policy.COORD_NAV_BODY_HEIGHT)
                if max(ys) < lower or min(ys) > upper:
                    continue
                points = _clip_height(_clip_height(points, lower, 1), upper, -1)
                if not points:
                    continue
                polygon, padding = _hull(points, policy.COORD_NAV_CLEARANCE)
                if not polygon:
                    continue
                xs, zs = zip(*polygon)
                xmin, xmax = max(left, min(xs)-padding), min(right, max(xs)+padding)
                zmin, zmax = max(low, min(zs)-padding), min(high, max(zs)+padding)
                if xmax < xmin or zmax < zmin:
                    continue
                index = len(self.entries)
                self.entries.append((kind, weights[kind], polygon, padding))
                for x in range(math.floor(xmin/self.size), math.floor(xmax/self.size)+1):
                    for z in range(math.floor(zmin/self.size), math.floor(zmax/self.size)+1):
                        self.bins.setdefault((x, z), set()).add(index)

    def cost(self, start, end):
        if not self.entries:
            return 0.
        # Only static geometry exposure is symmetric. Movement-failure costs
        # remain directional and are evaluated separately by ObstacleMap.
        key = tuple(sorted((tuple(start), tuple(end))))
        if key not in self.cost_cache:
            if len(self.cost_cache) >= policy.COORD_REFERENCE_COST_CACHE_LIMIT:
                self.cost_cache.clear()
            self.cost_cache[key] = self._cost(start, end)
        return self.cost_cache[key]

    def _cost(self, start, end):
        length = math.dist(start, end)
        if length < 1e-9 or not self.entries:
            return 0.
        indexes = set()
        for x in range(math.floor(min(start[0], end[0])/self.size), math.floor(max(start[0], end[0])/self.size)+1):
            for z in range(math.floor(min(start[1], end[1])/self.size), math.floor(max(start[1], end[1])/self.size)+1):
                indexes.update(self.bins.get((x, z), ()))
        by_kind, weights = {}, {}
        for index in indexes:
            kind, weight, polygon, padding = self.entries[index]
            interval = _interval(start, end, polygon, padding)
            if interval:
                by_kind.setdefault(kind, []).append(interval)
                weights[kind] = weight
        decks = _union([interval for polygon in self.decks if (
            interval := _interval(start, end, polygon, -policy.COORD_NAV_CLEARANCE))])
        total = 0.
        for kind, intervals in by_kind.items():
            exposure = _union(intervals)
            covered = sum(b-a for a, b in exposure)
            if kind == 'water':
                # Only already-recognized live decks can discount water risk.
                covered -= sum(max(0., min(b, d)-max(a, c)) for a, b in exposure for c, d in decks)
            total += length*max(0., covered)*weights[kind]
        return total
