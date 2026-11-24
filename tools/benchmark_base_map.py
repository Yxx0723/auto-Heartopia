"""Measure map repaint cost with the base map underlay, on a real Tk canvas.

Two phases are reported separately, because they answer different questions:

* **steady** — the viewport does not move. This is what proves the requirement
  "不得每帧重新生成完整地图底图": ``base_map.redraws`` must stay at 1 across all
  frames while ``skipped`` grows.
* **interactive** — the viewport moves every frame (a pan). This is the honest
  cost of re-cropping the raster, and it is supposed to be measurably higher.

The baseline is the M3 path on the same canvas: flat terrain tints plus the
static water/road/platform layers, which the base map replaces.

Numbers here come from a real Tk canvas running the application's own draw
functions. They are still not a full-application-window measurement: the live
window also draws scene objects, symbols and labels from game memory.

Usage:
    python tools/benchmark_base_map.py [--frames 200] [--out outputs/basemap/benchmark.json]
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

CANVAS_W, CANVAS_H = 760, 560
PAN_STEP = 1.0


def _load():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.core.map_reference_roads import REFERENCE_ROADS
    from utils.core.map_water_reference import (
        GRASSLAND_POND, MAP_WATER_AREAS, MAP_WATER_ISLANDS)
    from utils.core.terrain_height import coverage
    from utils.ui.base_map_layer import BaseMapLayer
    from utils.ui.map_draw_cache import MapDrawCache, Viewport
    from utils.ui.map_presentation import draw_grid, draw_terrain_tints
    from utils.ui.map_surface_reference import draw_wooden_platforms
    return {
        'roads': REFERENCE_ROADS, 'water': MAP_WATER_AREAS, 'pond': GRASSLAND_POND,
        'islands': MAP_WATER_ISLANDS, 'coverage': coverage,
        'BaseMapLayer': BaseMapLayer, 'cache': MapDrawCache, 'viewport': Viewport,
        'grid': draw_grid, 'tints': draw_terrain_tints, 'platforms': draw_wooden_platforms,
    }


def draw_m3_static(cache, loaded):
    loaded['tints'](cache, True)
    for index, polygon in enumerate(loaded['water']):
        key = ('water', index)
        cache.geometry.valid_keys.add(key)
        cache.polygon(key, tuple(v for point in polygon for v in point), -2,
                      fill='#619EA6', outline='#8FBDB3', width=3, tags='reference-water')
    for index, polygon in enumerate(loaded['islands']):
        key = ('island', index)
        cache.geometry.valid_keys.add(key)
        cache.polygon(key, tuple(v for point in polygon for v in point), -1.5,
                      fill='#A8B78A', outline='', tags='island')
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


def measure(mode, frames, pan):
    import tkinter
    loaded = _load()
    bounds = loaded['coverage']()['bounds']
    cx = (bounds['x'][0] + bounds['x'][1]) / 2
    cz = (bounds['z'][0] + bounds['z'][1]) / 2
    scale = min(CANVAS_W / (bounds['x'][1] - bounds['x'][0]),
                CANVAS_H / (bounds['z'][1] - bounds['z'][0]))

    root = tkinter.Tk()
    root.withdraw()
    canvas = tkinter.Canvas(root, width=CANVAS_W, height=CANVAS_H)
    cache = loaded['cache'](canvas, water_areas=loaded['water'],
                            main_map_water=(loaded['pond'],))
    layer = loaded['BaseMapLayer'](canvas) if mode == 'base' else None
    if layer is not None and not layer.available:
        raise SystemExit(f'底图不可用：{layer.error}')

    samples = []
    for index in range(frames):
        offset = index * PAN_STEP if pan else 0.0
        viewport = loaded['viewport'](CANVAS_W, CANVAS_H, cx + offset, cz, scale)
        started = time.perf_counter()
        cache.begin(viewport)
        loaded['grid'](cache.live, viewport, 20)
        if layer is not None:
            layer.draw(viewport, True)
        else:
            draw_m3_static(cache, loaded)
        cache.finish()
        canvas.update_idletasks()
        samples.append((time.perf_counter() - started) * 1000)

    samples.sort()
    result = {
        'mode': mode,
        'pan': pan,
        'frames': frames,
        'median_ms': round(statistics.median(samples), 3),
        'p95_ms': round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 3),
        'max_ms': round(samples[-1], 3),
        'canvas_items': len(cache.fixed),
    }
    if layer is not None:
        result['base_map_redraws'] = layer.redraws
        result['base_map_skipped'] = layer.skipped
    root.destroy()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames', type=int, default=200)
    parser.add_argument('--out', default='outputs/basemap/benchmark.json')
    args = parser.parse_args()

    results = [
        measure('m3', args.frames, pan=False),
        measure('base', args.frames, pan=False),
        measure('m3', max(60, args.frames // 3), pan=True),
        measure('base', max(60, args.frames // 3), pan=True),
    ]
    for row in results:
        row['mode'] = row['mode'] + ('-pan' if row['pan'] else '-steady')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"{'phase':<16}{'median ms':>11}{'p95 ms':>9}{'max ms':>9}{'items':>7}{'redraws':>9}{'skipped':>9}")
    for row in results:
        print(f"{row['mode']:<16}{row['median_ms']:>11.3f}{row['p95_ms']:>9.3f}"
              f"{row['max_ms']:>9.3f}{row['canvas_items']:>7}"
              f"{row.get('base_map_redraws', '-'):>9}{row.get('base_map_skipped', '-'):>9}")


if __name__ == '__main__':
    main()
