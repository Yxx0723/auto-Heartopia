"""Height-layer map painting for the terrain and height-slice views.

Draws only the verified wall edges from ``utils.core.terrain_height``. There is
no ground surface here: every line is a measured wall segment, coloured by its
own measured vertical extent and dimmed by the direction it faces.

Two things are deliberately absent, because the data cannot support them:

* contour lines — the collider ships no ground surface to trace;
* elevation numbers — the wall band is not a ground altitude.

The height-slice view does **not** claim to separate surfaces. Each wall edge
carries its own measured ``(low, high)``; the slice view reports where that
segment sits relative to the character Y band — entirely above it, crossing it,
or entirely below it. That is a statement about the wall, never about the
ground underfoot.
"""
from functools import lru_cache

from utils.core.terrain_height import (
    HEIGHT_CLASSES, facing_light, height_band, shade, wall_class)

# Shade buckets: 5 steps per class keeps relief readable while capping the
# number of canvas items. Below this many buckets the relief looks banded.
SHADE_BUCKETS = 2

# Wall line weights in screen pixels, by class. A wall is a vertical face seen
# from above, so it reads as a ridge line; deep walls sit heaviest.
LINE_WIDTH = {'deep': 2.6, 'main': 2.0, 'low': 1.6}

# Below this scale the map is showing regional context, so the low kerbs are
# dropped rather than drawn as sub-pixel haze.
DETAIL_MIN_SCALE = 1.2

CLASS_COLOR = {name: color for name, color, _ in HEIGHT_CLASSES}
CLASS_LABEL = {name: label for name, _, label in HEIGHT_CLASSES}

# Relative-to-reference states for the height-slice view. Colours say "this
# wall sits above / across / below your current Y", nothing else.
SLICE_STATES = (
    ('above', '#B9743F'),     # whole wall above the reference band
    ('across', '#5F7360'),    # wall spans the reference band
    ('below', '#5D7A92'),     # whole wall below the reference band
)
SLICE_COLOR = dict(SLICE_STATES)


def slice_state(edge, band):
    """Where one wall sits relative to the character-Y band."""
    low, high = edge[4], edge[5]
    if low > band[1]:
        return 'above'
    if high < band[0]:
        return 'below'
    return 'across'


def _point_key(x, z):
    return (round(x * 500), round(z * 500))


@lru_cache(maxsize=96)
def band_chains(anchor, detail=True):
    """Stitch selected edges into polylines, one canvas item per run.

    ``anchor`` is ``None`` for the terrain view, or a character Y for the slice
    view. Chaining is pure geometry: it never invents a point.

    Shading is applied **after** chaining, from each run's own mean facing. That
    keeps the canvas item count near the M3 baseline — ``MapDrawCache.finish``
    walks every entry per frame, so splitting runs by shade would multiply the
    per-frame cost for a difference the eye cannot resolve.
    """
    from utils.core.terrain_reference import TERRAIN_EDGES

    band = height_band(anchor) if anchor is not None else None
    groups = {}
    for edge in TERRAIN_EDGES:
        name = wall_class(edge)
        if not detail and name == 'low':
            continue
        state = name if band is None else slice_state(edge, band)
        groups.setdefault(state, []).append(
            ((edge[0], edge[1]), (edge[2], edge[3])))
    out = []
    for state, segments in sorted(groups.items()):
        for index, run in enumerate(_chain(segments)):
            bucket = _run_shade(run)
            out.append(((state, bucket, index),
                        tuple(v for point in run for v in point)))
    return tuple(out)


def _run_shade(run):
    """Mean facing of one run, quantised into SHADE_BUCKETS steps."""
    if len(run) < 2:
        return 0
    total = 0.
    for (ax, az), (bx, bz) in zip(run, run[1:]):
        total += facing_light((ax, az, bx, bz, 0., 1.))
    mean = total / (len(run) - 1)
    return min(SHADE_BUCKETS - 1, int(mean * SHADE_BUCKETS))


def _chain(segments):
    """Join segments that share an endpoint into contiguous runs.

    The wall mesh is almost entirely degree-2 nodes (3345 of 3410), so a plain
    walk from every uneven node collapses thousands of edges into a few dozen
    polylines. Remaining edges after that pass can only be closed loops.
    """
    coords = {}
    adjacent = {}
    edges = set()
    for a, b in segments:
        ka, kb = _point_key(*a), _point_key(*b)
        if ka == kb:
            continue
        coords[ka], coords[kb] = a, b
        edges.add((ka, kb) if ka < kb else (kb, ka))
        adjacent.setdefault(ka, []).append(kb)
        adjacent.setdefault(kb, []).append(ka)
    if not edges:
        return ()
    used = set()
    runs = []

    def walk(start, first):
        run = [coords[start]]
        cursor, target = start, first
        while True:
            used.add((cursor, target) if cursor < target else (target, cursor))
            run.append(coords[target])
            nxt = None
            for candidate in adjacent.get(target, ()):
                if candidate == cursor:
                    continue
                if (target, candidate) in used or (candidate, target) in used:
                    continue
                nxt = candidate
                break
            if nxt is None:
                return tuple(run)
            cursor, target = target, nxt

    for node, neighbours in adjacent.items():
        if len(neighbours) == 2:
            continue
        for neighbour in neighbours:
            key = (node, neighbour) if node < neighbour else (neighbour, node)
            if key in used:
                continue
            runs.append(walk(node, neighbour))

    for a, b in sorted(edges):
        if (a, b) in used:
            continue
        runs.append(walk(a, b))
    return runs


def color_for(state, bucket):
    base = SLICE_COLOR.get(state) or CLASS_COLOR[state]
    return shade(base, 0.62 + 0.38 * (bucket + 0.5) / SHADE_BUCKETS)


def width_for(state):
    return LINE_WIDTH.get(state, 2.0)


def draw_height_layers(cache, viewport, mode, height=None):
    """Paint the requested height view into ``cache``.

    ``mode`` is ``'terrain'`` or ``'heightslice'``; the standard view never
    calls this function, so its output is untouched.

    Re-registration is deliberately cheaper than a full ``cache.polyline``
    call: the map cache prunes any key that is not re-registered each frame, so
    the keys and the ``used`` marks are written directly and the expensive path
    only runs when an entry is genuinely missing.
    """
    if mode not in ('terrain', 'heightslice'):
        return
    # One-unit quantisation keeps the cache warm while the character moves; the
    # band is 3 units wide, so a half-unit shift cannot change what it reports.
    anchor = None if mode == 'terrain' or height is None else float(round(height))
    if mode == 'heightslice' and anchor is None:
        return
    detail = viewport.scale >= DETAIL_MIN_SCALE
    counts = {}
    valid_keys, used, fixed = cache.geometry.valid_keys, cache.used, cache.fixed
    for (state, bucket, index), coords in band_chains(anchor, detail):
        key = ('height-wall', state, bucket, index)
        valid_keys.add(key)
        used.add(key)
        if fixed.get(key) is None:
            cache.polyline(key, coords, -4.6, [
                {'fill': color_for(state, bucket), 'width': width_for(state),
                 'capstyle': 'round', 'joinstyle': 'round', 'tags': 'height-wall'}])
        counts[state] = counts.get(state, 0) + len(coords) // 2 - 1
    if mode == 'heightslice':
        _draw_band_note(cache, anchor, counts)


def _draw_band_note(cache, anchor, counts):
    """Corner note stating that the anchor Y is a reference, not a surface."""
    low, high = height_band(anchor)
    detail = '  '.join(f'{label} {counts.get(state, 0)} 段'
                       for state, label in (('above', '上方'), ('across', '跨界'), ('below', '下方')))
    text = (f'高度分层  参考 Y {anchor:.0f}（{low:.1f} ~ {high:.1f}，非承重面）\n'
            f'墙段相对位置：{detail}')
    key = 'height-band-note'
    cache.geometry.valid_keys.add(key)
    entry = cache.existing(key)
    if entry is not None:
        # Tk re-lays out text on every configure, so only rewrite real changes.
        if getattr(cache, '_height_note_text', None) == text:
            return
        for item, _ in entry.commands:
            cache.canvas.itemconfigure(item, text=text)
        cache._height_note_text = text
        return
    entry = cache.new_entry(key, 6.5, None)
    item = cache.canvas.create_text(
        12, 12, anchor='nw', text=text, fill='#3D4A42', justify='left',
        font=('Microsoft YaHei UI', 9), tags='height-band-note')
    entry.commands.append((item, ()))
    cache._height_note_text = text
    cache.register(entry, item, 'height-band-note')
