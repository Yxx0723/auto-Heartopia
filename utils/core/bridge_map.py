"""Version/world gated bridge display references and separate surveyed links."""
from functools import lru_cache
import math

from utils.core.bridge_reference import BRIDGE_MESH_FACES, SURVEYED_BRIDGE_PATHS
from utils.core.terrain_reference import TERRAIN_GAME_ASSEMBLY_SHA256
from utils.core import thresholds as policy

BRIDGE_REVISION = 'startown-bridge-v1-20261021'


@lru_cache(maxsize=1)
def bridge_surfaces():
    meshes = tuple({'polygon': tuple((p[0], p[2]) for p in triangle),
                  'height': (min(p[1] for p in triangle), max(p[1] for p in triangle)),
                  'category': 'bridge', 'source': 'bridge-mesh-reference',
                  'map_id': ('bridge-reference', name, index)}
                 for name, faces in BRIDGE_MESH_FACES for index, triangle in enumerate(faces))
    surveyed = []
    for name, path in SURVEYED_BRIDGE_PATHS:
        for index, (a, b) in enumerate(zip(path, path[1:])):
            length = math.hypot(b[0]-a[0], b[2]-a[2])
            if length < 1e-8:
                continue
            dx, dz = ((b[2]-a[2])/length*policy.COORD_BRIDGE_CORRIDOR_RADIUS,
                      -(b[0]-a[0])/length*policy.COORD_BRIDGE_CORRIDOR_RADIUS)
            surveyed.append({'polygon': ((a[0]+dx, a[2]+dz), (b[0]+dx, b[2]+dz),
                                          (b[0]-dx, b[2]-dz), (a[0]-dx, a[2]-dz)),
                             'category': 'bridge', 'source': 'bridge-mesh-reference',
                             'map_id': ('bridge-survey', name, index)})
    return meshes + tuple(surveyed)


def verified_bridge_data(assembly_hash, main_map_ready):
    if main_map_ready is not True or assembly_hash != TERRAIN_GAME_ASSEMBLY_SHA256:
        return (), ()
    return bridge_surfaces(), tuple({'points': path, 'source': 'surveyed-walking-bridge',
                                     'id': name} for name, path in SURVEYED_BRIDGE_PATHS)
