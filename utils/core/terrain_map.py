"""Version-gated, exact vertical terrain edges for display/navigation."""
from functools import lru_cache
import math
from utils.core import thresholds as policy
from utils.core.terrain_reference import TERRAIN_EDGES, TERRAIN_GAME_ASSEMBLY_SHA256


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
    return tuple(wall_geometry(edge, index) for index, edge in enumerate(TERRAIN_EDGES))


def verified_terrain_walls(assembly_hash, main_map_ready):
    if main_map_ready is not True or assembly_hash != TERRAIN_GAME_ASSEMBLY_SHA256:
        return ()
    return terrain_walls()
