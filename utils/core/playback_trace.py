"""Bounded diagnostic copies; no geometry updates, game reads or game input."""
import math
from utils.core import thresholds as policy
from utils.core.coordinate_route import valid_position
from utils.core.coordinate_navigation import polygon_distance


def collision_context(navigator, position, target):
    result = {'state': 'unavailable', 'source': 'navigation_scene', 'contact_confirmed': False,
              'colliders': [], 'candidate_count': 0, 'truncated': False}
    scene = getattr(navigator, 'scene', None)
    if not isinstance(scene, dict) or scene.get('state') != 'ready' or not valid_position(position):
        return result
    candidates = []
    for box in scene.get('colliders', ()):
        if not isinstance(box, dict) or box.get('map_cached'):
            continue
        polygon = box.get('polygon')
        if not isinstance(polygon, (list, tuple)) or not 3 <= len(polygon) <= 256 or any(
                not isinstance(p, (list, tuple)) or len(p) != 2 or not all(
                    type(v) in (int, float) and math.isfinite(v) for v in p) for p in polygon):
            continue
        xs, zs = zip(*polygon)
        x, _, z = position
        if math.hypot(min(max(x, min(xs)), max(xs))-x, min(max(z, min(zs)), max(zs))-z) > policy.COORD_TRACE_COLLIDER_RADIUS:
            continue
        gap = polygon_distance((x, z), (x, z), tuple(map(tuple, polygon)))
        if gap <= policy.COORD_TRACE_COLLIDER_RADIUS:
            copied = {'polygon': [list(p) for p in polygon], 'distance_xz': gap,
                      'source': box.get('source'), 'category': box.get('category')}
            height = box.get('height')
            if isinstance(height, (list, tuple)) and len(height) == 2 and all(
                    type(v) in (int, float) and math.isfinite(v) for v in height):
                copied['height'] = list(height)
            display = box.get('display_box')
            if isinstance(display, dict) and display.get('schema') == 'oriented-box-v1':
                corners = display.get('corners')
                if isinstance(corners, (list, tuple)) and len(corners) == 8 and all(valid_position(p) for p in corners):
                    copied['display_box'] = {'schema': 'oriented-box-v1', 'corners': [list(p) for p in corners]}
            candidates.append(copied)
    candidates.sort(key=lambda b: b['distance_xz'])
    result.update(state='available', terrain_revision=scene.get('terrain_revision'),
                  candidate_count=len(candidates), truncated=len(candidates) > policy.COORD_TRACE_COLLIDER_LIMIT,
                  colliders=candidates[:policy.COORD_TRACE_COLLIDER_LIMIT])
    if valid_position(target):
        dx, dz = target[0]-position[0], target[2]-position[2]
        length = math.hypot(dx, dz)
        result['attempt_direction_xz'] = [dx/length, dz/length] if length else None
    return result
