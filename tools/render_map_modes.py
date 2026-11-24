"""Render the three map views off-screen for M4 acceptance.

The game does not have to be running. Everything drawn here comes from static,
already-verified references in the repository:

* ``utils.core.map_water_reference``  — main-map water polygons
* ``utils.core.map_reference_roads``  — reference road centrelines
* ``utils.ui.map_surface_reference``  — verified wooden platform footprints
* ``utils.core.terrain_height``       — measured wall bands

House plots are **not** drawn: they come from a live scene snapshot, so an
offline image cannot show them. ``render_report.json`` states that explicitly.

This is a second rasteriser over the project's own geometry and palette, not a
screenshot of the running application. Colours and coordinates match
``map_height_layers`` / ``map_presentation``; the renderer exists so the three
views can be compared without launching the game.

Usage:
    python tools/render_map_modes.py [--out DIR] [--anchors 17.5,48]

Project imports stay inside functions on purpose: importing this module must not
perform any work (``tests/test_architecture.py`` enforces that).
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GROUND = '#A8B78A'
WATER = '#619EA6'
SHORE = '#8FBDB3'
ROAD = '#E0CBA0'
ROAD_EDGE = '#C7C29B'
PLATFORM = '#B88752'
PLATFORM_EDGE = '#68452C'

FONT_CANDIDATES = ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
                   '/System/Library/Fonts/PingFang.ttc')

# The application canvas is roughly 500 px wide. Stroke weights in the project
# are tuned for that size, so the off-screen renderer scales them up to keep the
# same on-screen proportions instead of drawing hairlines on a large canvas.
APP_CANVAS_WIDTH = 500

_LOADED = None


def _load():
    """Import the project modules once, on first use, with the root on sys.path."""
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
    from utils.ui.map_presentation import _TERRAIN_TINTS
    from utils.ui.map_surface_reference import WOODEN_PLATFORM_FOOTPRINTS

    _LOADED = {
        'terrain_height': terrain_height,
        'roads': REFERENCE_ROADS,
        'water_areas': MAP_WATER_AREAS,
        'water_islands': MAP_WATER_ISLANDS,
        'pond': GRASSLAND_POND,
        'layers': map_height_layers,
        'tints': _TERRAIN_TINTS,
        'platforms': WOODEN_PLATFORM_FOOTPRINTS,
    }
    return _LOADED


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


class View:
    """Same projection as ``utils.ui.map_draw_cache.Viewport``."""

    def __init__(self, width, height, x, z, scale):
        self.width, self.height, self.x, self.z, self.scale = width, height, x, z, scale
        self.stroke = width / APP_CANVAS_WIDTH

    def screen(self, x, z):
        return (self.width / 2 + (x - self.x) * self.scale,
                self.height / 2 - (z - self.z) * self.scale)

    def polygon(self, coords):
        return [self.screen(x, z) for x, z in coords]


def fit_view(size=1200, padding=64):
    bounds = _load()['terrain_height'].coverage()['bounds']
    dx = (bounds['x'][1] - bounds['x'][0]) / 2
    dz = (bounds['z'][1] - bounds['z'][0]) / 2
    scale = min((size - padding) / (2 * dx), (size - padding) / (2 * dz))
    return View(size, size, (bounds['x'][0] + bounds['x'][1]) / 2,
                (bounds['z'][0] + bounds['z'][1]) / 2, scale)


def draw_base(draw, view):
    draw.rectangle([0, 0, view.width, view.height], fill=GROUND)
    for _name, fill, pixels in _load()['tints']:
        coords = [((x - 511) / 1.55, (459 - y) / 1.55) for x, y in pixels]
        draw.polygon(view.polygon(coords), fill=fill)


def draw_height(draw, view, mode, anchor, detail=True):
    layers = _load()['layers']
    for (state, bucket, _index), coords in layers.band_chains(
            anchor if mode == 'heightslice' else None, detail):
        if len(coords) < 4:
            continue
        points = list(zip(coords[::2], coords[1::2]))
        draw.line(view.polygon(points), fill=layers.color_for(state, bucket),
                  width=max(1, round(layers.width_for(state) * view.stroke)),
                  joint='curve')


def draw_water(draw, view):
    loaded = _load()
    edge = max(1, round(1.2 * view.stroke))
    for polygon in loaded['water_areas']:
        draw.polygon(view.polygon(polygon), fill=WATER, outline=SHORE, width=edge)
    draw.polygon(view.polygon(loaded['pond']), fill=WATER, outline=SHORE, width=edge)
    for polygon in loaded['water_islands']:
        draw.polygon(view.polygon(polygon), fill=GROUND)


def draw_roads(draw, view):
    for road in _load()['roads']:
        points = list(road['points'])
        if road.get('closed'):
            points.append(points[0])
        path = view.polygon(points)
        draw.line(path, fill=ROAD_EDGE, width=max(1, round(10 * view.stroke)), joint='curve')
        draw.line(path, fill=ROAD, width=max(1, round(5 * view.stroke)), joint='curve')


def draw_platforms(draw, view):
    for coords in _load()['platforms']:
        pairs = list(zip(coords[::2], coords[1::2]))
        draw.polygon(view.polygon(pairs), fill=PLATFORM, outline=PLATFORM_EDGE,
                     width=max(1, round(1.5 * view.stroke)))


def draw_legend(draw, view, items, font):
    """Small swatch legend so the views explain themselves without the app."""
    pad, row, box = 16, 26, 18
    width = max(len(label) for _, label in items) * 13 + 70
    top = view.height - pad - row * len(items) - 10
    draw.rectangle([pad, top, pad + width, view.height - pad], fill='#F7F5EF',
                   outline='#C9C4B6')
    for index, (color, label) in enumerate(items):
        y = top + 8 + index * row
        draw.rectangle([pad + 10, y, pad + 10 + box, y + box - 4], fill=color)
        draw.text((pad + 10 + box + 8, y - 2), label, fill='#2D3A33', font=font)


def caption(draw, view, text, sub, font, small):
    draw.rectangle([0, 0, view.width, 74], fill='#F7F5EF')
    draw.text((18, 12), text, fill='#2D3A33', font=font)
    draw.text((18, 44), sub, fill='#5C6B60', font=small)


def render(mode, anchor, out_dir):
    view = fit_view()
    image = Image.new('RGB', (view.width, view.height), GROUND)
    draw = ImageDraw.Draw(image)
    draw_base(draw, view)
    if mode != 'standard':
        draw_height(draw, view, mode, anchor)
    draw_water(draw, view)
    draw_roads(draw, view)
    draw_platforms(draw, view)

    layers = _load()['layers']
    title = {'standard': '标准地图（M3 样式，未叠加高度层）',
             'terrain': '地势地图（山壁高度带 + 法向明暗）',
             'heightslice': '高度分层（墙段相对参考高度的上下关系）'}[mode]
    if mode == 'heightslice':
        low, high = _load()['terrain_height'].height_band(anchor)
        sub = (f'参考 Y {anchor:.1f}（{low:.1f} ~ {high:.1f}），仅视图参考、非承重面；'
               f'桥面与地表无可靠 Y 数据，故不显示')
    elif mode == 'terrain':
        sub = '仅山壁实测高度带；无地表高程、无等高线（碰撞网格只有 17 个水平面）'
    else:
        sub = '标准视图基线，用于对比重绘耗时与视觉差异'
    caption(draw, view, title, sub, load_font(24), load_font(15))

    if mode == 'terrain':
        report = _load()['terrain_height'].coverage()
        draw_legend(draw, view, [
            (layers.CLASS_COLOR[name], f'{layers.CLASS_LABEL[name]}  '
                                       f'{report["classes"][name]["edges"]} 段')
            for name in ('deep', 'main', 'low')], load_font(15))
    elif mode == 'heightslice':
        draw_legend(draw, view, [
            (layers.SLICE_COLOR['above'], '整墙位于参考带上方'),
            (layers.SLICE_COLOR['across'], '墙体跨越参考带'),
            (layers.SLICE_COLOR['below'], '整墙位于参考带下方')], load_font(15))

    suffix = '' if mode != 'heightslice' else f'-y{anchor:g}'
    path = out_dir / f'{mode}{suffix}.png'
    image.save(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='outputs/m4')
    parser.add_argument('--anchors', default='17.5,48')
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    produced = [render('standard', None, out_dir), render('terrain', None, out_dir)]
    produced += [render('heightslice', float(a), out_dir)
                 for a in args.anchors.split(',') if a.strip()]

    report = _load()['terrain_height'].coverage()
    matrix = {
        'views': [str(p) for p in produced],
        'height_data': {
            'wall_edges': report['edges'],
            'classes': {k: v['edges'] for k, v in report['classes'].items()},
            'y_range': report['y'],
            'ground_elevation_available': report['ground_elevation'],
            'ground_faces_in_collider': report['ground_faces'],
        },
        'not_rendered': ['等高线（无地表）', '地表高程色阶（无地表）',
                         '住宅地皮（来自运行时场景快照）', '桥面上下层（无世界变换）'],
        'disclaimer': '离屏渲染对比图，非应用内截图',
    }
    (out_dir / 'render_report.json').write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2), encoding='utf-8')
    for path in produced:
        print(path)


if __name__ == '__main__':
    main()
