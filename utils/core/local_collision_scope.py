"""Conservative build broad phase; never an independent collision verdict."""
from utils.core import thresholds as policy
from utils.core.bridge_navigation import MAX_BRIDGE_OBSERVATION_GAP
from utils.core.scene_map import collider_footprint


def local_collision_scene(scene, position, search_radius):
    # Retain every collider whose padded AABB can touch the indexed search area.
    # AABB overlap is necessary, not sufficient: exact existing checks follow.
    radius = search_radius+policy.COORD_NAV_LEG_LENGTH
    margin = max(policy.COORD_NAV_CLEARANCE, MAX_BRIDGE_OBSERVATION_GAP)
    left, right = position[0]-radius-margin, position[0]+radius+margin
    low, high = position[2]-radius-margin, position[2]+radius+margin
    kept = []
    for collider in scene.get('colliders', ()):
        polygon = collider_footprint(collider)
        if polygon is None:
            kept.append(collider)  # Leave original validation/error handling intact.
            continue
        xs, zs = zip(*polygon)
        if max(xs) < left or min(xs) > right or max(zs) < low or min(zs) > high:
            continue
        kept.append(collider)
    return {**scene, 'colliders': kept}
