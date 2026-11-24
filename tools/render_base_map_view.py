"""Render the program's map using the real drawing code, without the game.

Everything here goes through the same modules the application uses on screen:
``BaseMapLayer`` for the base raster, ``MapDrawCache`` for the world layers, and
the project's own water / road / platform references. The only thing that is not
the live window is the Tk surface, which is created, painted and exported here.

Only the base raster is exported: ``compose`` returns the image the layer hands
to Tk, and canvas items drawn above it (player, route, plots) live in the canvas
rather than in that image, so they cannot be captured off-screen. Overlay
alignment is proven separately by ``verify_base_map_alignment.py``, which draws
the world outlines with PIL through the same transform.

That distinction matters for acceptance: these images show **what the program
draws**, but they are still not a screenshot of the running EXE with live game
data. Frame timings in ``benchmark_base_map.py`` also separate Tk-window numbers
from off-screen numbers.

Usage:
    python tools/render_base_map_view.py [--out outputs/basemap] [--size 900x700]
"""
import argparse
import sys
from pathlib import Path

DEFAULT_SIZE = (900, 700)


def _load():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.core.map_reference_roads import REFERENCE_ROADS
    from utils.core.map_water_reference import (
        GRASSLAND_POND, MAP_WATER_AREAS, MAP_WATER_ISLANDS)
    from utils.ui.base_map_layer import BaseMapLayer, WorldToImage
    from utils.ui.map_draw_cache import MapDrawCache, Viewport
    from utils.ui.map_presentation import draw_grid, draw_terrain_tints
    from utils.ui.map_surface_reference import draw_wooden_platforms
    return {
        'roads': REFERENCE_ROADS, 'water': MAP_WATER_AREAS, 'pond': GRASSLAND_POND,
        'islands': MAP_WATER_ISLANDS, 'BaseMapLayer': BaseMapLayer,
        'WorldToImage': WorldToImage, 'MapDrawCache': MapDrawCache,
        'Viewport': Viewport, 'grid': draw_grid, 'tints': draw_terrain_tints,
        'platforms': draw_wooden_platforms,
    }


def draw_world_layers(cache, viewport, loaded, include_roads=True):
    """Re-issue the static reference layers the map shows above the base raster."""
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
    if include_roads:
        for road in loaded['roads']:
            points = list(road['points'])
            if road.get('closed'):
                points.append(points[0])
            key = ('road', road['id'])
            cache.geometry.valid_keys.add(key)
            cache.polyline(key, tuple(v for point in points for v in point), -3, [
                {'fill': '#C7C29B', 'width': 10, 'capstyle': 'round', 'joinstyle': 'round'},
                {'fill': '#E0CBA0', 'width': 5, 'capstyle': 'round', 'joinstyle': 'round'}])
    loaded['platforms'](cache, viewport.scale)


def render(out_path, size, centre, scale, with_base=True):
    import tkinter
    from PIL import Image

    loaded = _load()
    width, height = size
    viewport = loaded['Viewport'](width, height, centre[0], centre[1], scale)
    root = tkinter.Tk()
    root.withdraw()
    canvas = tkinter.Canvas(root, width=width, height=height)
    cache = loaded['MapDrawCache'](canvas, water_areas=loaded['water'],
                                   main_map_water=(loaded['pond'],))
    layer = loaded['BaseMapLayer'](canvas)

    cache.begin(viewport)
    reported = layer.draw(viewport, with_base and layer.available)
    if not reported:
        loaded['tints'](cache, True)
        draw_world_layers(cache, viewport, loaded)
    root.update_idletasks()

    if reported:
        image = layer.compose(viewport).copy()
    else:
        image = Image.new('RGB', (width, height), '#A8B78A')
    root.destroy()
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return {'path': str(target), 'base_map': bool(reported),
            'layer_error': layer.error, 'scale': scale}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='outputs/basemap')
    parser.add_argument('--size', default='900x700')
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split('x'))
    out = Path(args.out)

    # Whole-map view, framed on the terrain reference like the app does.
    _load()                      # puts the repository root on sys.path
    from utils.core.terrain_height import coverage
    bounds = coverage()['bounds']
    cx = (bounds['x'][0] + bounds['x'][1]) / 2
    cz = (bounds['z'][0] + bounds['z'][1]) / 2
    scale = min(width / (bounds['x'][1] - bounds['x'][0]),
                height / (bounds['z'][1] - bounds['z'][0]))
    print(render(out / 'program-full-map.png', (width, height), (cx, cz), scale))

    regions = (('north-mountain', 110.0, 190.0, 3.2),
               ('grassland-lake', -180.0, -20.0, 3.2),
               ('east-forest', 200.0, 20.0, 3.2),
               ('centre-town', 20.0, 25.0, 3.2))
    for name, x, z, region_scale in regions:
        print(render(out / f'program-{name}.png', (width, height), (x, z), region_scale))


if __name__ == '__main__':
    main()
