"""Conservative PUBLIC-side policy, not a reconstructed cadastral/teleport boundary.

Use the house-facing edge of the actual forecourt trigger and the configured house
direction. Exclude its entire house-facing half-plane within the map bounds.
This deliberately sacrifices outer-world shortcuts for this patrol only.
"""
import math
from utils.core.coordinate_route import valid_position
from utils.core.coordinate_navigation import polygon_distance
from utils.core import thresholds as policy


def exclusion_polygon(center, entrance, forecourt, bounds):
    if not valid_position(center) or not valid_position(entrance) or len(forecourt) < 3:
        raise ValueError('家园方向或门口触发区无效')
    dx, dz = center[0]-entrance[0], center[2]-entrance[2]
    length = math.hypot(dx, dz)
    if length < 1.:
        raise ValueError('无法可靠区分地块侧与公共侧')
    nx, nz = dx/length, dz/length
    if any(not valid_position(p) for p in forecourt):
        raise ValueError('门口触发区坐标无效')
    offset = max(nx*p[0]+nz*p[2] for p in forecourt)
    # Clip the real map rectangle by the conservative forbidden half-plane.
    left, low, right, high = bounds
    poly = [(left,low),(right,low),(right,high),(left,high)]
    result = []
    for a,b in zip(poly, poly[1:]+poly[:1]):
        va, vb = nx*a[0]+nz*a[1]-offset, nx*b[0]+nz*b[1]-offset
        if va >= 0:
            result.append(list(a))
        if (va >= 0) != (vb >= 0):
            t=va/(va-vb);result.append([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])])
    if len(result) < 3:
        raise ValueError('公共侧约束不在当前地图范围内')
    return result, (nx,nz)


def public_clear(position, polygons, margin=None):
    if not valid_position(position) or not polygons:
        return False
    p=(position[0],position[2])
    gap=policy.COORD_PATROL_HOME_MARGIN if margin is None else margin
    return all(polygon_distance(p,p,polygon) > gap for polygon in polygons)


def public_checkpoint(entrance, direction, polygons):
    # Engineering search band; no assumed ground height or loading radius.
    for step in range(1, 81):
        p=[entrance[0]-direction[0]*step*.5,entrance[1],entrance[2]-direction[1]*step*.5]
        if public_clear(p,polygons,policy.COORD_PATROL_HOME_MARGIN+policy.COORD_MAX_CALIBRATION_DISTANCE
                        +policy.COORD_NAV_CLEARANCE):
            return p
    raise ValueError('门前公共侧未找到保守巡查落脚点')
