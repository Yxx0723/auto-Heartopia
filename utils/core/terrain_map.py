"""Version-gated vertical edges and height-clipped tilted cliff faces."""
from functools import lru_cache
import math
from utils.core import thresholds as policy
from utils.core.terrain_reference import TERRAIN_EDGES, TERRAIN_GAME_ASSEMBLY_SHA256
from utils.core.terrain_faces_reference import TERRAIN_TILTED_FACES
from utils.core.coordinate_route import valid_position
from utils.core.scene_map import hull

TERRAIN_REVISION = 'main-terrain-v2-8ce4bd25cbb9'


def slope_degrees(triangle):
    if not isinstance(triangle, (list, tuple)) or len(triangle) != 3 \
            or not all(valid_position(v) for v in triangle):
        raise ValueError('山体三角面坐标无效')
    a, b, c = triangle
    u, v = ([b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)])
    n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    length = math.sqrt(sum(x*x for x in n))
    if length < 1e-9:
        raise ValueError('山体三角面退化')
    return math.degrees(math.acos(min(1., abs(n[1])/length)))


def projected_polygon(vertices):
    """A face or a thin vertical section, without a large mountain AABB."""
    polygon = hull((p[0], p[2]) for p in vertices)
    if len(polygon) >= 3:
        return tuple(map(tuple, polygon))
    if len(polygon) != 2:
        return ()
    (ax, az), (bx, bz) = polygon
    length = math.hypot(bx-ax, bz-az)
    dx = (bz-az)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
    dz = -(bx-ax)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
    return ((ax+dx, az+dz), (bx+dx, bz+dz), (bx-dx, bz-dz), (ax-dx, az-dz))


def triangle_geometry(triangle, index):
    if slope_degrees(triangle) < policy.COORD_TERRAIN_MIN_WALL_SLOPE:
        return None  # Floors and ordinary ramps are not inferred cliff walls.
    low, high = min(p[1] for p in triangle), max(p[1] for p in triangle)
    return {'polygon': projected_polygon(triangle), 'height': (low, high),
            'terrain_triangle': tuple(tuple(p) for p in triangle),
            'category': 'mountain', 'source': 'verified-main-terrain-mesh',
            'map_static': True, 'map_id': ('main-terrain-face', index)}


def terrain_slice(box, height):
    """Clip a verified triangle against the character body slab before XZ.

    Projecting its whole footprint extrudes a slanted wall over other heights.
    Slab clipping preserves the actual face and never invents a walkable ramp.
    """
    vertices = list(box['terrain_triangle'])
    for boundary, above in ((height+policy.COORD_NAV_GROUND_CLEARANCE, True),
                            (height+policy.COORD_NAV_BODY_HEIGHT, False)):
        clipped = []
        for a, b in zip(vertices, vertices[1:]+vertices[:1]):
            a_in = a[1] >= boundary if above else a[1] <= boundary
            b_in = b[1] >= boundary if above else b[1] <= boundary
            if a_in:
                clipped.append(a)
            if a_in != b_in:
                fraction = (boundary-a[1])/(b[1]-a[1])
                clipped.append(tuple(a[i]+fraction*(b[i]-a[i]) for i in range(3)))
        vertices = clipped
        if not vertices:
            return ()
    return projected_polygon(vertices)


def wall_geometry(edge, index):
    ax, az, bx, bz, low, high = edge
    length = math.hypot(bx-ax, bz-az)
    if not length:
        raise ValueError('地形边界长度无效')
    dx = (bz-az)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
    dz = -(bx-ax)/length*policy.COORD_TERRAIN_EDGE_HALF_WIDTH
    return {'polygon': ((ax+dx, az+dz), (bx+dx, bz+dz), (bx-dx, bz-dz), (ax-dx, az-dz)),
            'height': (low, high), 'category': 'mountain', 'source': 'verified-main-terrain-mesh',
            'map_static': True, 'map_id': ('main-terrain-wall', index)}


@lru_cache(maxsize=1)
def terrain_walls():
    return (tuple(wall_geometry(edge, index) for index, edge in enumerate(TERRAIN_EDGES))
            + tuple(triangle_geometry(triangle, face_id) for face_id, triangle in TERRAIN_TILTED_FACES))


def verified_terrain_walls(assembly_hash, main_map_ready):
    if main_map_ready is not True or assembly_hash != TERRAIN_GAME_ASSEMBLY_SHA256:
        return ()
    return terrain_walls()
