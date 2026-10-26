"""Conservative recognition of a live bridge deck for local XZ navigation.

The scene reader exposes verified box colliders, not semantic bridge labels or
ground triangles. A deck is therefore accepted only when a thin, long collider
crosses a known water polygon, has dry landings at both ends, and lies at the
player's current support-height band. These are conservative engineering
filters, not game-measured dimensions or proof of grounded state.
"""
import math


# Initial geometric filters. These are deliberately centralized and are not
# measured game limits; the live scene read currently has no grounded flag.
MIN_DECK_LENGTH = 4.0
MAX_DECK_LENGTH = 60.0
MIN_DECK_WIDTH = 1.2
MAX_DECK_WIDTH = 8.0
MIN_DECK_ASPECT = 1.8
MAX_DECK_THICKNESS = .9
SUPPORT_HEIGHT_TOLERANCE = .75
MAX_BRIDGE_OBSERVATION_GAP = 2.0
MIN_WATER_CROSSING = 1.5
MIN_DRY_LANDING = .5
CENTERLINE_SAMPLE_SPACING = .4
SEGMENT_SAMPLE_SPACING = .25


def _valid_polygon(polygon):
    return isinstance(polygon, (list, tuple)) and len(polygon) >= 3 and all(
        isinstance(point, (list, tuple)) and len(point) == 2
        and all(type(value) in (int, float) and math.isfinite(value) for value in point)
        for point in polygon)


def _inside(point, polygon):
    x, z = point
    result = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > z) != (b[1] > z) and x < (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0]:
            result = not result
    return result


def _point_segment_distance(point, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dz * dz
    t = max(0., min(1., ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length_sq)) \
        if length_sq else 0.
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dz)


def _boundary_distance(point, polygon):
    return min(_point_segment_distance(point, a, b)
               for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def _point_polygon_distance(point, polygon):
    if _inside(point, polygon):
        return 0.
    return _boundary_distance(point, polygon)


def _rectangle(polygon):
    """Return a minimum-area oriented rectangle around a convex footprint."""
    best = None
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length <= 1e-6:
            continue
        axis = (dx / length, dz / length)
        normal = (-axis[1], axis[0])
        along = [x * axis[0] + z * axis[1] for x, z in polygon]
        across = [x * normal[0] + z * normal[1] for x, z in polygon]
        low, high = min(along), max(along)
        left, right = min(across), max(across)
        length_span, width_span = high - low, right - left
        area = length_span * width_span
        if best is None or area < best[0]:
            best = (area, (axis[0], axis[1]), (low + high) / 2,
                    (left + right) / 2, length_span, width_span)
    if best is None:
        return None
    _, axis, _, _, length_span, width_span = best
    if width_span > length_span:
        axis = (-axis[1], axis[0])
    normal = (-axis[1], axis[0])
    along = [x * axis[0] + z * axis[1] for x, z in polygon]
    across = [x * normal[0] + z * normal[1] for x, z in polygon]
    along_mid = (min(along) + max(along)) / 2
    across_mid = (min(across) + max(across)) / 2
    length_span = max(along) - min(along)
    width_span = max(across) - min(across)
    center = (axis[0] * along_mid + normal[0] * across_mid,
              axis[1] * along_mid + normal[1] * across_mid)
    return center, axis, length_span, width_span


def geometry_key(collider):
    """Stable per-snapshot key used to exempt only the recognized deck box."""
    polygon, heights = collider.get('polygon'), collider.get('height')
    if not _valid_polygon(polygon) or not isinstance(heights, (list, tuple)) or len(heights) != 2:
        return None
    if not all(type(value) in (int, float) and math.isfinite(value) for value in heights):
        return None
    vertices = tuple(sorted((round(float(point[0]), 4), round(float(point[1]), 4)) for point in polygon))
    return (vertices, tuple(round(float(value), 4) for value in heights),
            collider.get('category'), collider.get('source'))


def _water_crossing(center, axis, length, polygon, water_areas):
    inset = min(.05, length * .025)
    usable_length = length - 2 * inset
    count = max(2, int(math.ceil(usable_length / CENTERLINE_SAMPLE_SPACING)))
    step = usable_length / count
    samples = []
    for index in range(count + 1):
        offset = -length / 2 + inset + index * step
        point = (center[0] + axis[0] * offset, center[1] + axis[1] * offset)
        if not _inside(point, polygon):
            return ()
        samples.append(point)

    crossed = []
    for water_index, water in enumerate(water_areas):
        if not _valid_polygon(water):
            continue
        wet = [_inside(point, water) for point in samples]
        start = None
        runs = []
        for index, is_wet in enumerate(wet + [False]):
            if is_wet and start is None:
                start = index
            elif not is_wet and start is not None:
                runs.append((start, index - 1))
                start = None
        if any((last - first) * step >= MIN_WATER_CROSSING
               and inset + first * step >= MIN_DRY_LANDING
               and inset + (count - last) * step >= MIN_DRY_LANDING
               for first, last in runs):
            crossed.append(water_index)
    return tuple(crossed)


def detect_bridge_decks(scene, position, water_areas):
    """Find candidate decks at the current height, only in a complete scene read.

    Complete here means the existing scene reader marked both managed and
    native collision collections navigation-eligible. It does not mean the
    full world is loaded or that the player is known to be grounded.
    """
    quality = scene.get('quality') if isinstance(scene, dict) else None
    if not isinstance(quality, dict) or quality.get('navigation_eligible') is not True:
        return ()
    if not isinstance(position, (list, tuple)) or len(position) != 3 \
            or not all(type(value) in (int, float) and math.isfinite(value) for value in position):
        return ()

    result, seen = [], set()
    for collider in scene.get('colliders', ()):
        if not isinstance(collider, dict) or collider.get('category') in ('fence', 'handrail', 'mountain'):
            continue
        source = collider.get('source')
        if source is not None and source not in (
                'verified-solid-collider', 'verified-native-world-collider', 'verified-managed-collider'):
            continue
        key = geometry_key(collider)
        if key is None or key in seen:
            continue
        polygon, heights = collider['polygon'], collider['height']
        low, high = heights
        if high < low or high - low > MAX_DECK_THICKNESS \
                or abs(high - position[1]) > SUPPORT_HEIGHT_TOLERANCE:
            continue
        rectangle = _rectangle(polygon)
        if rectangle is None:
            continue
        center, axis, length, width = rectangle
        if not MIN_DECK_LENGTH <= length <= MAX_DECK_LENGTH \
                or not MIN_DECK_WIDTH <= width <= MAX_DECK_WIDTH \
                or length / max(width, 1e-6) < MIN_DECK_ASPECT:
            continue
        if _point_polygon_distance((position[0], position[2]), polygon) > MAX_BRIDGE_OBSERVATION_GAP:
            continue
        water_ids = _water_crossing(center, axis, length, polygon, water_areas)
        if not water_ids:
            continue
        seen.add(key)
        result.append({'key': key, 'polygon': tuple(tuple(point) for point in polygon),
                       'water_ids': frozenset(water_ids), 'height': (low, high),
                       'center': center, 'axis': axis, 'length': length, 'width': width})
    return tuple(result)


def segment_uses_bridge(deck, water_index, start, end, water_polygon, water_clearance, agent_clearance):
    """Allow only the buffered water portion that remains inside this deck."""
    if water_index not in deck['water_ids']:
        return False
    polygon = deck['polygon']
    distance = math.dist(start, end)
    count = max(1, int(math.ceil(distance / SEGMENT_SAMPLE_SPACING)))
    touched = False
    for index in range(count + 1):
        fraction = index / count
        point = (start[0] + (end[0] - start[0]) * fraction,
                 start[1] + (end[1] - start[1]) * fraction)
        if _point_polygon_distance(point, water_polygon) > water_clearance + 1e-6:
            continue
        touched = True
        dx, dz = point[0] - deck['center'][0], point[1] - deck['center'][1]
        along = dx * deck['axis'][0] + dz * deck['axis'][1]
        across = -dx * deck['axis'][1] + dz * deck['axis'][0]
        if abs(across) + agent_clearance > deck['width'] / 2 + 1e-6:
            return False
        in_deck = _inside(point, polygon) or _point_polygon_distance(point, polygon) <= 1e-6
        in_water = _inside(point, water_polygon) or _point_polygon_distance(point, water_polygon) <= 1e-6
        in_dry_landing_buffer = not in_water and \
            abs(along) <= deck['length'] / 2 + water_clearance + 1e-6
        if not in_deck and not in_dry_landing_buffer:
            return False
        if abs(along) > deck['length'] / 2 + water_clearance + 1e-6:
            return False
    return touched
