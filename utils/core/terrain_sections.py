"""Height-section known terrain triangles without widening scene permissions."""
import math

from utils.core import thresholds as policy
from utils.core.scene_map import collider_footprint, hull
from utils.core.coordinate_route import valid_position
from utils.core.terrain_map import wall_geometry
from utils.core.terrain_reference import TERRAIN_EDGES
from utils.core.terrain_wall_profiles import TERRAIN_WALL_PROFILES


def terrain_profile_signature(scene):
    """Bind metadata to its geometry, so swapped IDs cannot reuse sliced maps."""
    result = set()
    for collider in scene.get('colliders', ()):
        if collider.get('source') != 'verified-main-terrain-mesh' or collider.get('category') != 'mountain':
            continue
        heights, polygon = collider.get('height'), collider_footprint(collider)
        if polygon is None or not isinstance(heights, (list, tuple)) or len(heights) != 2 \
                or not all(type(v) in (int, float) and math.isfinite(v) for v in heights):
            continue
        identity = collider.get('map_id')
        identity = tuple(identity) if isinstance(identity, (list, tuple)) and len(identity) == 2 \
            and identity[0] == 'main-terrain-wall' and type(identity[1]) is int else None
        result.add((polygon, tuple(heights), identity))
    return frozenset(result)


def _profile(collider):
    identity = collider.get('map_id')
    if collider.get('source') != 'verified-main-terrain-mesh' or collider.get('category') != 'mountain' \
            or not isinstance(identity, (tuple, list)) or len(identity) != 2 \
            or identity[0] != 'main-terrain-wall' or type(identity[1]) is not int:
        return None
    index = identity[1]
    if not 0 <= index < len(TERRAIN_EDGES) or index not in TERRAIN_WALL_PROFILES:
        return None
    edge, faces = TERRAIN_WALL_PROFILES[index]
    if tuple(TERRAIN_EDGES[index]) != edge:
        return None
    expected = wall_geometry(edge, index)
    heights = collider.get('height')
    if not isinstance(heights, (tuple, list)) or tuple(heights) != expected['height'] \
            or collider_footprint(collider) != expected['polygon']:
        return None
    return faces


def section_polygon(triangle, height):
    """Conservative body slab covers the ENTIRE existing map-height cache band.

    Height is the observed character coordinate, not a claim of grounded state.
    The extra band prevents a cached slice hiding geometry before map refresh.
    """
    if type(height) not in (int, float) or not math.isfinite(height) \
            or not isinstance(triangle, (tuple, list)) or len(triangle) != 3 \
            or not all(valid_position(p) for p in triangle):
        raise ValueError('Invalid terrain body section')
    vertices = list(triangle)
    margin = policy.COORD_NAV_HEIGHT_REFRESH
    for boundary, sign in ((height+policy.COORD_NAV_GROUND_CLEARANCE-margin, 1),
                           (height+policy.COORD_NAV_BODY_HEIGHT+margin, -1)):
        clipped = []
        for a, b in zip(vertices, vertices[1:]+vertices[:1]):
            da, db = (a[1]-boundary)*sign, (b[1]-boundary)*sign
            if da >= 0:
                clipped.append(a)
            if (da >= 0) != (db >= 0):
                t = da/(da-db)
                clipped.append(tuple(x+(y-x)*t for x, y in zip(a, b)))
        vertices = clipped
        if not vertices:
            return ()
    polygon = hull((p[0], p[2]) for p in vertices)
    if len(polygon) == 2:
        (ax, az), (bx, bz) = polygon
        length = math.hypot(bx-ax, bz-az)
        if length <= 1e-9:
            return ()
        dx = (bz-az)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
        dz = -(bx-ax)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
        return ((ax+dx, az+dz), (bx+dx, bz+dz), (bx-dx, bz-dz), (ax-dx, az-dz))
    return tuple(map(tuple, polygon)) if len(polygon) >= 3 else ()


def section_scene(scene, height):
    if type(height) not in (int, float) or not math.isfinite(height):
        raise ValueError('Invalid terrain section height')
    colliders = []
    for collider in scene.get('colliders', ()):
        faces = _profile(collider)
        if faces is None:
            colliders.append(collider)  # Missing, modified or unknown data stays blocked.
            continue
        for face_id, triangle in faces:
            polygon = section_polygon(triangle, height)
            if polygon:
                colliders.append({**collider, 'polygon': polygon, 'section_source_face': face_id})
    return {**scene, 'colliders': colliders}
