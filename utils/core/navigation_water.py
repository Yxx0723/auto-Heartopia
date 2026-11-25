"""Approximate water exclusions used in addition to live scene colliders.

The original lake trace clipped the northwestern river at map row 275.
Include both banks' complete water polygons from the corrected trace. The
dry road crossing between them remains open; do not join the polygons or
replace that gap with a rectangle. Other water constraints stay unchanged.
This is map-derived guidance, not a claim of measured collision geometry.
"""
from utils.core.lake_areas import WATER_AREAS
from utils.core.map_water_reference import MAP_OCEAN_AREAS, MAP_WATER_AREAS


NORTHERN_RIVER_AREAS = tuple(
    polygon for polygon in MAP_WATER_AREAS
    if all(-170 <= x <= -95 and 108 <= z <= 135 for x, z in polygon)
)

NAVIGATION_WATER_AREAS = WATER_AREAS + NORTHERN_RIVER_AREAS

# The rare-wood route runs between this inlet and a neighboring channel.
# It was outside the legacy inland-water exclusions.
SOUTHEAST_CHANNEL_AREAS = tuple(
    polygon for polygon in MAP_WATER_AREAS
    if all(75 <= x <= 104 and -141 <= z <= -105 for x, z in polygon)
)


def local_coastal_water(position, radius, clearance):
    """Clip ocean rings outside the searchable square and its full buffer."""
    x, _, z = position
    extent = radius + clearance + 1.
    bounds = ((0, x - extent, 1), (0, x + extent, -1),
              (1, z - extent, 1), (1, z + extent, -1))
    result = []
    for polygon in MAP_OCEAN_AREAS:
        for axis, edge, direction in bounds:
            if not polygon:
                break
            clipped = []
            previous = polygon[-1]
            previous_inside = (previous[axis] - edge) * direction >= 0
            for point in polygon:
                current_inside = (point[axis] - edge) * direction >= 0
                if current_inside != previous_inside:
                    ratio = (edge - previous[axis]) / (point[axis] - previous[axis])
                    clipped.append(tuple(a + (b - a) * ratio for a, b in zip(previous, point)))
                if current_inside:
                    clipped.append(point)
                previous, previous_inside = point, current_inside
            polygon = clipped
        if len(polygon) >= 3:
            result.append(tuple(polygon))
    return tuple(result)
