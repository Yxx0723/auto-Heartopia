"""Calibrate the TapTap base map against the project's world coordinates.

The base map arrives as a square tile mosaic with no world metadata, so the
world-to-image transform has to be measured. Water is the reliable signal: it is
large, high-contrast and the project already stores it in world XZ
(``MAP_WATER_AREAS`` plus the grassland pond).

Method: rasterise the world water polygons for a range of scales, then use FFT
cross-correlation to find the translation that best overlaps the image's own
water mask at each scale. The winner is refined and reported with an explicit
pixel/unit error so the result can be checked, not trusted.

Usage:
    python tools/calibrate_base_map.py [--basemap PATH] [--json OUT]

Project imports stay inside functions so importing this module does nothing.
"""
import argparse
import json
import sys
from pathlib import Path

GRID = 500          # comparison raster size (px per side)
SCALES = [round(0.40 + 0.02 * i, 3) for i in range(46)]   # px per world unit @ GRID


def _load():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.core.map_water_reference import (
        GRASSLAND_POND, MAP_WATER_AREAS, MAP_WATER_ISLANDS)
    return MAP_WATER_AREAS, GRASSLAND_POND, MAP_WATER_ISLANDS


def image_water_mask(path, size=GRID):
    """Water pixels of the base map: blue/teal dominates red and green."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        small = image.convert('RGB').resize((size, size), Image.LANCZOS)
    pixels = np.asarray(small).astype('int16')
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return (blue > green) & (blue > red) & (blue > 110)


def world_water_mask(scale, size=GRID):
    """Rasterise the world water polygons into image space at one scale."""
    import numpy as np
    from PIL import Image, ImageDraw
    areas, pond, _islands = _load()
    polygons = [tuple(p) for p in areas] + [tuple(pond)]
    xs = [x for poly in polygons for x, _ in poly]
    zs = [z for poly in polygons for _, z in poly]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    canvas = Image.new('1', (size, size), 0)
    draw = ImageDraw.Draw(canvas)
    for poly in polygons:
        draw.polygon([(size / 2 + (x - cx) * scale, size / 2 - (z - cz) * scale)
                      for x, z in poly], fill=1)
    return np.array(canvas, dtype=bool)


def best_shift(image_mask, world_mask):
    """Translation with the largest overlap, via FFT cross-correlation."""
    import numpy as np
    spectrum = np.fft.rfft2(image_mask.astype('float32')) * \
        np.conj(np.fft.rfft2(world_mask.astype('float32')))
    correlation = np.fft.irfft2(spectrum, s=image_mask.shape)
    dy, dx = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    if dy > image_mask.shape[0] // 2:
        dy -= image_mask.shape[0]
    if dx > image_mask.shape[1] // 2:
        dx -= image_mask.shape[1]
    return int(dy), int(dx)


def iou(a, b):
    import numpy as np
    union = int(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b)) / union if union else 0.0


def calibrate(basemap):
    import numpy as np
    reference = image_water_mask(basemap)
    best = None
    curve = []
    for scale in SCALES:
        world = world_water_mask(scale)
        dy, dx = best_shift(reference, world)
        shifted = np.roll(np.roll(world, dy, axis=0), dx, axis=1)
        score = iou(reference, shifted)
        curve.append((round(score, 4), scale))
        if best is None or score > best['iou']:
            best = {'scale': scale, 'dy': dy, 'dx': dx, 'iou': score}
    curve.sort(reverse=True)
    print('IoU 前 5:', curve[:5], flush=True)
    # Refine around the winner at quarter-step resolution.
    fine = None
    for scale in [round(best['scale'] + 0.005 * i, 3) for i in range(-3, 4)]:
        world = world_water_mask(scale)
        dy, dx = best_shift(reference, world)
        shifted = np.roll(np.roll(world, dy, axis=0), dx, axis=1)
        score = iou(reference, shifted)
        if fine is None or score > fine['iou']:
            fine = {'scale': scale, 'dy': dy, 'dx': dx, 'iou': score}
    best = fine

    areas, pond, _ = _load()
    polygons = [tuple(p) for p in areas] + [tuple(pond)]
    xs = [x for poly in polygons for x, _ in poly]
    zs = [z for poly in polygons for _, z in poly]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    return {
        **best,
        'grid': GRID,
        'world_center_of_water': [round(cx, 2), round(cz, 2)],
        'note': ('scale 为 GRID 栅格的像素/世界单位；dy/dx 为栅格位移。'
                 '换算到原图需乘以 原图边长/GRID。'),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--basemap', default='outputs/basemap/basemap-z11.png')
    parser.add_argument('--json', default='outputs/basemap/calibration.json')
    parser.add_argument('--install', metavar='PATH', default='runtime/basemap/basemap.json',
                        help='把标定写入程序读取的位置（与底图缓存同目录）')
    args = parser.parse_args()

    report = calibrate(args.basemap)
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.install:
        target = Path(args.install)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已安装到 {target}')


if __name__ == '__main__':
    main()
