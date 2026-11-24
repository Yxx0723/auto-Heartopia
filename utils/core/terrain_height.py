"""Display-only height classification over verified main-map terrain walls.

Everything here is derived from `utils.core.terrain_reference.TERRAIN_EDGES`
(3398 exact vertical edges, extracted read-only on 2026-10-20 from
`mc_terrain_collider01`). Nothing in this module feeds navigation, collision,
pathing or memory reads: it only classifies edges that already exist so the map
can shade them by their measured vertical extent.

Measured facts this module relies on (2026-11-24, all from TERRAIN_EDGES):

* 3398 edges, 73 distinct ``(low, high)`` pairs.
* 2956 edges (87.0%) form the main wall band, ``low`` around -12.78 and
  ``high`` around 48.09.
* 411 edges (12.1%) start below Y=-31 ("deep base"); this group also holds the
  highest crest at Y=53.375.
* 31 edges (0.9%) top out below Y=30 ("low kerb").

The collider mesh contains only 17 horizontal faces spread over 7 Y values, so
no ground-elevation field exists anywhere in this project. This module
therefore never reports a terrain elevation, a slope or a walkability: it only
answers "how tall is this wall segment" and "does this segment touch a given
character Y band".
"""
from functools import lru_cache
import math

from utils.core import thresholds as policy
from utils.core.terrain_reference import TERRAIN_EDGES

# Class boundaries are read off the measured distribution above, not tuned.
# `deep` wins over the others because the deepest walls also carry the crest.
DEEP_LOW = -25.       # below: deep base walls (411 edges)
LOW_HIGH = 30.        # below: low kerbs (31 edges)

HEIGHT_CLASSES = (
    ('deep', '#4A5B52', '深基山壁：下界低于 -25，含最高脊线'),
    ('main', '#6E7F6B', '主山体：占 87% 的山壁主体'),
    ('low', '#97A78C', '低垣：上界低于 30'),
)

# One light axis, applied through |n·L| so we never claim which side of a wall
# faces outward — the reference edges carry no facing flag.
LIGHT_AXIS = (-0.7071067811865476, 0.7071067811865476)


def wall_class(edge):
    """Height class name for one ``(ax, az, bx, bz, low, high)`` edge."""
    low, high = edge[4], edge[5]
    if low < DEEP_LOW:
        return 'deep'
    if high < LOW_HIGH:
        return 'low'
    return 'main'


@lru_cache(maxsize=1)
def class_edges():
    """Edges bucketed by height class, computed once per process."""
    bucketed = {name: [] for name, _, _ in HEIGHT_CLASSES}
    for edge in TERRAIN_EDGES:
        bucketed[wall_class(edge)].append(edge)
    return {name: tuple(edges) for name, edges in bucketed.items()}


def wall_normal(edge):
    """Unit normal of the wall footprint in XZ, sign-agnostic by construction."""
    ax, az, bx, bz = edge[0], edge[1], edge[2], edge[3]
    dx, dz = bx - ax, bz - az
    length = math.hypot(dx, dz)
    if not length:
        return (0., 0.)
    return (-dz / length, dx / length)


def facing_light(edge):
    """0.35 .. 1.0 lit factor from |normal · light|, for relief shading only."""
    nx, nz = wall_normal(edge)
    dot = abs(nx * LIGHT_AXIS[0] + nz * LIGHT_AXIS[1])
    return 0.35 + 0.65 * dot


def shade(color, factor):
    """Scale a ``#rrggbb`` colour towards black; used for the lit factor."""
    if not (isinstance(color, str) and len(color) == 7 and color[0] == '#'):
        return color
    return '#' + ''.join(
        f'{max(0, min(255, round(int(color[i:i + 2], 16) * factor))):02x}'
        for i in (1, 3, 5))


def height_band(height):
    """Character-Y band used by the height-slice view.

    Width reuses COORD_NAV_HEIGHT_REFRESH (0.75) doubled, matching how the
    existing map-height cache bands already pad around a character Y. The
    band is a view reference, never a claim about the surface underfoot.
    """
    if type(height) not in (int, float) or not math.isfinite(height):
        raise ValueError('Invalid height band anchor')
    half = policy.COORD_NAV_HEIGHT_REFRESH * 2
    return (height - half, height + half)


def edge_touches_band(edge, band):
    """True when the wall's measured [low, high] overlaps the Y band."""
    low, high = edge[4], edge[5]
    return high >= band[0] and low <= band[1]


def edges_in_band(height):
    """Edges crossing one character-Y band, as ``(edge, class_name)`` pairs."""
    band = height_band(height)
    return tuple((edge, wall_class(edge)) for edge in TERRAIN_EDGES
                 if edge_touches_band(edge, band))


@lru_cache(maxsize=1)
def coverage():
    """Measured coverage of the wall reference, for the acceptance report."""
    total = len(TERRAIN_EDGES)
    bucketed = class_edges()
    lengths = {name: sum(math.hypot(e[2] - e[0], e[3] - e[1]) for e in edges)
               for name, edges in bucketed.items()}
    xs = [v for e in TERRAIN_EDGES for v in (e[0], e[2])]
    zs = [v for e in TERRAIN_EDGES for v in (e[1], e[3])]
    return {
        'edges': total,
        'classes': {name: {'edges': len(edges),
                           'share': len(edges) / total,
                           'length': lengths[name]}
                    for name, edges in bucketed.items()},
        'bounds': {'x': (min(xs), max(xs)), 'z': (min(zs), max(zs))},
        'y': (min(e[4] for e in TERRAIN_EDGES), max(e[5] for e in TERRAIN_EDGES)),
        'ground_faces': 0,          # collider ships 17 horizontal faces only
        'ground_elevation': False,  # deliberately not claimed
    }
