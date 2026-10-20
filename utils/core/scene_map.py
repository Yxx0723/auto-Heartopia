"""Pure world transforms and XZ projections for verified scene geometry."""
import math
from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy


def quaternion(value):
    if len(value) != 4 or not all(math.isfinite(v) for v in value):
        raise ValueError('Invalid scene rotation')
    length = math.sqrt(sum(v * v for v in value))
    if not policy.COORD_SCENE_MIN_QUATERNION_NORM <= length <= policy.COORD_SCENE_MAX_QUATERNION_NORM:
        raise ValueError('Invalid scene rotation norm')
    return [v / length for v in value]


def rotate(vector, q):
    x, y, z, w = q
    vx, vy, vz = vector
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return [vx + w * tx + y * tz - z * ty,
            vy + w * ty + z * tx - x * tz,
            vz + w * tz + x * ty - y * tx]


def compose_transform(parent, child):
    p, q, s = parent
    cp, cq, cs = child
    q, cq = quaternion(q), quaternion(cq)
    if not valid_position(p) or not valid_position(cp) or not math.isfinite(s * cs) or s <= 0 or cs <= 0:
        raise ValueError('Invalid scene transform')
    position = [a + b for a, b in zip(p, rotate([v * s for v in cp], q))]
    x, y, z, w = q
    a, b, c, d = cq
    rotation = [w*a + x*d + y*c - z*b, w*b - x*c + y*d + z*a,
                w*c + x*b - y*a + z*d, w*d - x*a - y*b - z*c]
    return position, quaternion(rotation), s * cs


def hull(points):
    points = sorted(set(map(tuple, points)))
    def cross(o, a, b):
        return (a[0]-o[0]) * (b[1]-o[1]) - (a[1]-o[1]) * (b[0]-o[0])
    def half(values):
        result = []
        for point in values:
            while len(result) >= 2 and cross(result[-2], result[-1], point) <= 0:
                result.pop()
            result.append(point)
        return result[:-1]
    return [list(p) for p in half(points) + half(reversed(points))]


def box_geometry(position, rotation, center, size, parent=None):
    if not all(valid_position(v) for v in (position, center, size)) or any(v <= 0 for v in size):
        raise ValueError('Invalid scene box')
    transform = compose_transform(parent or ([0, 0, 0], [0, 0, 0, 1], 1.),
                                  (position, rotation, 1.))
    p, q, s = transform
    corners = []
    for dx in (-1, 1):
        for dy in (-1, 1):
            for dz in (-1, 1):
                local = [(c + sign * length / 2) * s for c, sign, length in zip(center, (dx, dy, dz), size)]
                world = [a + b for a, b in zip(p, rotate(local, q))]
                if not valid_position(world):
                    raise ValueError('Invalid world box')
                corners.append(world)
    return {'polygon': hull((v[0], v[2]) for v in corners),
            'height': [min(v[1] for v in corners), max(v[1] for v in corners)]}


def barrier_strokes(polygon):
    """One segment's rail and posts, in world units, without joining gaps.

    The collision polygon remains the authoritative footprint. These strokes
    are only a map symbol and never enlarge navigation obstacles.
    """
    if len(polygon) < 3 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in polygon):
        return []
    a, b = max(zip(polygon, polygon[1:] + polygon[:1]),
               key=lambda edge: sum((v-u)**2 for u, v in zip(*edge)))
    length = math.hypot(b[0]-a[0], b[1]-a[1])
    if length < .001:
        return []
    axis = ((b[0]-a[0])/length, (b[1]-a[1])/length)
    normal = (-axis[1], axis[0])
    along = [sum(v*u for v, u in zip(p, axis)) for p in polygon]
    across = [sum(v*u for v, u in zip(p, normal)) for p in polygon]
    middle = (min(across)+max(across))/2
    def cut(fixed, coordinates, values):
        intersections = []
        for i in range(len(polygon)):
            j = (i+1) % len(polygon)
            a, b = coordinates[i], coordinates[j]
            if abs(a-b) < 1e-9:
                if abs(fixed-a) < 1e-9:
                    intersections.extend((values[i], values[j]))
            elif min(a, b)-1e-9 <= fixed <= max(a, b)+1e-9:
                intersections.append(values[i] + (fixed-a)/(b-a)*(values[j]-values[i]))
        return (min(intersections), max(intersections)) if intersections else None
    section = cut(middle, across, along)
    if section is None:
        return []
    low, high = section
    def point(t, n):
        return [axis[0]*t + normal[0]*n, axis[1]*t + normal[1]*n]
    strokes = [[point(low, middle), point(high, middle)]]
    count = min(64, max(1, math.ceil((high-low)/2)))
    for i in range(count+1):
        t = low + (high-low)*i/count
        section = cut(t, along, across)
        if section:
            strokes.append([point(t, section[0]), point(t, section[1])])
    return strokes


def select_geometry(scene, position, radius):
    if not valid_position(position) or not math.isfinite(radius) or radius <= 0:
        return [], []
    x, z = position[0], position[2]
    objects = [o for o in scene.get('objects', []) if math.hypot(o['position'][0]-x, o['position'][2]-z) <= radius]
    colliders = []
    for box in scene.get('colliders', []):
        xs, zs = zip(*box['polygon'])
        nearest_x = min(max(x, min(xs)), max(xs))
        nearest_z = min(max(z, min(zs)), max(zs))
        if math.hypot(nearest_x-x, nearest_z-z) <= radius:
            colliders.append(box)
    return objects, colliders


def select_water_areas(areas, left, right, low, high):
    """Select reference water polygons intersecting an XZ viewport."""
    if not all(math.isfinite(value) for value in (left, right, low, high)) or left >= right or low >= high:
        return []
    visible = []
    for polygon in areas:
        if len(polygon) < 3:
            continue
        xs, zs = zip(*polygon)
        if max(xs) >= left and min(xs) <= right and max(zs) >= low and min(zs) <= high:
            visible.append(polygon)
    return visible
