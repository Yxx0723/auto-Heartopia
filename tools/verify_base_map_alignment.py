"""Overlay world-coordinate features on the calibrated base map.

This is the calibration check that matters: if the coastlines, lakes, roads and
wooden platforms the project already stores land on top of the artwork's own
features, the transform is right. Numbers alone (water IoU) cannot show that.

Usage:
    python tools/verify_base_map_alignment.py [--size 1200] [--out PATH]
"""
import argparse
import json
import sys
from pathlib import Path

DEFAULT_CALIBRATION = 'outputs/basemap/calibration.json'
DEFAULT_BASEMAP = 'outputs/basemap/basemap-z11.png'


def _load():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.core.map_reference_roads import REFERENCE_ROADS
    from utils.core.map_water_reference import (
        GRASSLAND_POND, MAP_WATER_AREAS, MAP_WATER_ISLANDS)
    from utils.ui.map_surface_reference import WOODEN_PLATFORM_FOOTPRINTS
    return REFERENCE_ROADS, MAP_WATER_AREAS, GRASSLAND_POND, MAP_WATER_ISLANDS, \
        WOODEN_PLATFORM_FOOTPRINTS


class Transform:
    """World XZ -> base-map pixel, using the measured calibration."""

    def __init__(self, calibration, size):
        grid = calibration['grid']
        self.size = size
        self.k = size / grid
        self.scale = calibration['scale'] * self.k
        self.cx, self.cz = calibration['world_center_of_water']
        self.dx = calibration['dx'] * self.k
        self.dy = calibration['dy'] * self.k
        self.half = size / 2

    def __call__(self, x, z):
        return (self.half + (x - self.cx) * self.scale + self.dx,
                self.half - (z - self.cz) * self.scale + self.dy)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--basemap', default=DEFAULT_BASEMAP)
    parser.add_argument('--calibration', default=DEFAULT_CALIBRATION)
    parser.add_argument('--size', type=int, default=1200)
    parser.add_argument('--out', default='outputs/basemap/alignment-check.png')
    args = parser.parse_args()

    from PIL import Image, ImageDraw

    calibration = json.loads(Path(args.calibration).read_text(encoding='utf-8'))
    roads, areas, pond, islands, platforms = _load()
    project = Transform(calibration, args.size)

    with Image.open(args.basemap) as base:
        canvas = base.convert('RGB').resize((args.size, args.size), Image.LANCZOS)
    draw = ImageDraw.Draw(canvas, 'RGBA')

    def outline(points, color, width=2):
        draw.line([project(x, z) for x, z in points] + [project(*points[0])],
                  fill=color, width=width, joint='curve')

    for polygon in areas:
        outline(polygon, (255, 80, 60, 210), 2)
    outline(pond, (255, 214, 0, 235), 3)
    for polygon in islands:
        outline(polygon, (120, 255, 160, 200), 2)
    for road in roads:
        points = list(road['points'])
        draw.line([project(x, z) for x, z in points],
                  fill=(70, 120, 255, 220), width=3, joint='curve')
    for coords in platforms:
        pairs = list(zip(coords[::2], coords[1::2]))
        draw.line([project(x, z) for x, z in pairs] + [project(*pairs[0])],
                  fill=(255, 0, 200, 235), width=2)

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    print(target)


if __name__ == '__main__':
    main()
