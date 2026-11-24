"""Measure map repaint cost for the three M4 views against the M3 baseline.

Every frame re-issues the same static layers the standard view draws (terrain
tints and textures, water, islands, reference roads, wooden platforms), plus the
grid the application redraws each frame, and then optionally adds a height
layer. The baseline is therefore a real standard-view frame on a warm cache, not
an empty loop.

``MapDrawCache.finish()`` runs every frame in the application, so it is included
here too: it walks every retained entry, which is why the height layer is kept to
roughly baseline item count rather than one canvas item per edge.

Runs a live Tk canvas so the numbers include real canvas bookkeeping.

Usage:
    python tools/benchmark_map_modes.py [--frames 150] [--out outputs/m4/benchmark.json]

Project imports stay inside functions on purpose: importing this module must not
perform any work (``tests/test_architecture.py`` enforces that).
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

CANVAS_W, CANVAS_H = 500, 400

_LOADED = None


def _load():
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.core import terrain_height
    from utils.core.map_reference_roads import REFERENCE_ROADS
    from utils.core.map_water_reference import (
        GRASSLAND_POND, MAP_WATER_AREAS, MAP_WATER_ISLANDS)
    from utils.ui import map_height_layers
    from utils.ui.map_draw_cache import MapDrawCache, Viewport
    from utils.ui.map_presentation import draw_grid, draw_terrain_tints
    from utils.ui.map_surface_reference import draw_wooden_platforms

    _LOADED = {
        'terrain_height': terrain_height,
        'roads': REFERENCE_ROADS,
        'water_areas': MAP_WATER_AREAS,
        'water_islands': MAP_WATER_ISLANDS,
        'pond': GRASSLAND_POND,
        'layers': map_height_layers,
        'cache': MapDrawCache,
        'viewport': Viewport,
        'grid': draw_grid,
        'tints': draw_terrain_tints,
        'platforms': draw_wooden_platforms,
    }
    return _LOADED


def draw_base(cache):
    """Re-issue the standard view's static layers, one frame's worth."""
    loaded = _load()
    loaded['tints'](cache, True)
    for index, polygon in enumerate(loaded['water_areas']):
        key = ('water', index)
        cache.geometry.valid_keys.add(key)
        cache.polygon(key, tuple(v for point in polygon for v in point), -2,
                      fill='#619EA6', outline='#8FBDB3', width=3, tags='reference-water')
    for index, polygon in enumerate(loaded['water_islands']):
        key = ('island', index)
        cache.geometry.valid_keys.add(key)
        cache.polygon(key, tuple(v for point in polygon for v in point), -1.5,
                      fill='#A8B78A', outline='', tags='island')
    cache.geometry.valid_keys.add('pond')
    cache.polygon('pond', tuple(v for point in loaded['pond'] for v in point), -2,
                  fill='#619EA6', outline='#8FBDB3', width=3, tags='reference-water')
    for road in loaded['roads']:
        points = list(road['points'])
        if road.get('closed'):
            points.append(points[0])
        key = ('road', road['id'])
        cache.geometry.valid_keys.add(key)
        cache.polyline(key, tuple(v for point in points for v in point), -3, [
            {'fill': '#C7C29B', 'width': 10, 'capstyle': 'round', 'joinstyle': 'round'},
            {'fill': '#E0CBA0', 'width': 5, 'capstyle': 'round', 'joinstyle': 'round'}])
    loaded['platforms'](cache, cache.viewport.scale)


def run(mode, frames, anchor):
    import tkinter
    loaded = _load()

    bounds = loaded['terrain_height'].coverage()['bounds']
    dx = (bounds['x'][1] - bounds['x'][0]) / 2
    dz = (bounds['z'][1] - bounds['z'][0]) / 2
    scale = min((CANVAS_W - 76) / (2 * dx), (CANVAS_H - 76) / (2 * dz))
    viewport = loaded['viewport'](
        CANVAS_W, CANVAS_H, (bounds['x'][0] + bounds['x'][1]) / 2,
        (bounds['z'][0] + bounds['z'][1]) / 2, scale)

    root = tkinter.Tk()
    root.withdraw()
    canvas = tkinter.Canvas(root, width=CANVAS_W, height=CANVAS_H)
    cache = loaded['cache'](canvas, water_areas=loaded['water_areas'],
                            main_map_water=(loaded['pond'],))
    cache.begin(viewport)
    draw_base(cache)
    loaded['layers'].draw_height_layers(cache, viewport, mode, anchor)
    cache.finish()
    root.update_idletasks()

    samples = []
    for _ in range(frames):
        started = time.perf_counter()
        cache.begin(viewport)
        loaded['grid'](cache.live, viewport, 20)
        draw_base(cache)
        loaded['layers'].draw_height_layers(cache, viewport, mode, anchor)
        cache.finish()
        canvas.update_idletasks()
        samples.append((time.perf_counter() - started) * 1000)
    items = len(cache.fixed)
    root.destroy()
    samples.sort()
    return {
        'mode': mode,
        'anchor': anchor,
        'frames': frames,
        'median_ms': round(statistics.median(samples), 3),
        'p95_ms': round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 3),
        'max_ms': round(samples[-1], 3),
        'canvas_items': items,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames', type=int, default=150)
    parser.add_argument('--out', default='outputs/m4/benchmark.json')
    args = parser.parse_args()

    results = [run('standard', args.frames, None),
               run('terrain', args.frames, None),
               run('heightslice', args.frames, 17.5),
               run('heightslice', args.frames, 48.0)]
    base, base_p95 = results[0]['median_ms'], results[0]['p95_ms']
    for row in results:
        row['median_delta_pct'] = round((row['median_ms'] / base - 1) * 100, 2)
        row['p95_delta_pct'] = round((row['p95_ms'] / base_p95 - 1) * 100, 2)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"{'view':<20}{'median ms':>11}{'p95 ms':>9}{'d median':>10}{'d p95':>9}{'items':>7}")
    for row in results:
        label = row['mode'] + (f"@{row['anchor']:g}" if row['anchor'] is not None else '')
        print(f"{label:<20}{row['median_ms']:>11.3f}{row['p95_ms']:>9.3f}"
              f"{row['median_delta_pct']:>9.1f}%{row['p95_delta_pct']:>8.1f}%"
              f"{row['canvas_items']:>7}")


if __name__ == '__main__':
    main()
