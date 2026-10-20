"""Approximate water exclusions used in addition to live scene colliders.

The original lake trace clipped the northwestern river at map row 275.
Include both banks' complete water polygons from the corrected trace. The
dry road crossing between them remains open; do not join the polygons or
replace that gap with a rectangle. Other water constraints stay unchanged.
This is map-derived guidance, not a claim of measured collision geometry.
"""
from utils.core.lake_areas import WATER_AREAS
from utils.core.map_water_reference import MAP_WATER_AREAS


NORTHERN_RIVER_AREAS = tuple(
    polygon for polygon in MAP_WATER_AREAS
    if all(-170 <= x <= -95 and 108 <= z <= 135 for x, z in polygon)
)

NAVIGATION_WATER_AREAS = WATER_AREAS + NORTHERN_RIVER_AREAS
