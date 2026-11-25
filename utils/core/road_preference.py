"""Calibrated main-map road raster used only as a soft travel cost."""
from functools import lru_cache
import json
import math
from pathlib import Path


ROAD_DISCOUNT = .25


class WorldToImage:
    """Affine world XZ to map image pixels."""

    def __init__(self, calibration, size):
        factor = size / calibration['grid']
        self.scale = calibration['scale'] * factor
        self.cx, self.cz = calibration['world_center_of_water']
        self.dx = calibration['dx'] * factor
        self.dy = calibration['dy'] * factor
        self.half = size / 2
        if self.scale <= 0 or not all(math.isfinite(v) for v in
                                      (self.scale, self.cx, self.cz, self.dx, self.dy)):
            raise ValueError('地图坐标标定无效')

    def __call__(self, x, z):
        return (self.half + (x - self.cx) * self.scale + self.dx,
                self.half - (z - self.cz) * self.scale + self.dy)


def load_road_mask(root):
    from PIL import Image

    root = Path(root)
    with Image.open(root / 'road-mask.png') as image:
        if image.size != (4000, 4000):
            raise ValueError('道路图尺寸与标定不匹配')
        mask = image.convert('L')
    calibration = json.loads((root / 'road-mask-calibration.json').read_text(encoding='utf-8'))
    return mask, WorldToImage(calibration, mask.width)


@lru_cache(maxsize=1)
def load_road_preference(root):
    try:
        mask, transform = load_road_mask(root)
        return RoadPreference(mask, transform)
    except (ImportError, OSError, ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        return None


class RoadPreference:
    def __init__(self, mask, transform):
        self.mask = mask
        self.pixels = mask.tobytes()
        self.size = mask.width
        self.transform = transform

    def contains(self, point):
        x, y = self.transform(*point)
        return (0 <= x < self.size and 0 <= y < self.size
                and self.pixels[int(y) * self.size + int(x)] >= 128)

    def covers(self, start, end):
        if not self.contains(start) or not self.contains(end):
            return False
        length = math.dist(start, end)
        return self.road_length(start, end, length) >= length - 1e-8

    def contains_with_margin(self, point, margin):
        x, z = point
        return all(self.contains(p) for p in (
            point, (x - margin, z), (x + margin, z), (x, z - margin), (x, z + margin)))

    def near_segment(self, start, end, length):
        """Look for roads anywhere a cheaper path could reach."""
        x, y = self.transform(*start)
        u, v = self.transform(*end)
        # A path containing cheaper road pixels can be at most this long.
        max_length = length / (1 - ROAD_DISCOUNT)
        pad = .5 * math.sqrt(max(0., max_length**2 - length**2)) * self.transform.scale
        left = max(0, math.floor(min(x, u) - pad))
        top = max(0, math.floor(min(y, v) - pad))
        right = min(self.size, math.floor(max(x, u) + pad) + 1)
        bottom = min(self.size, math.floor(max(y, v) + pad) + 1)
        return left < right and top < bottom and self.mask.crop((left, top, right, bottom)).getbbox() is not None

    def road_length(self, start, end, length):
        """Sum the portion of the segment inside each white raster cell."""
        x, y = self.transform(*start)
        u, v = self.transform(*end)
        size = self.size
        if not length or min(x, u) >= size or min(y, v) >= size or max(x, u) < 0 or max(y, v) < 0:
            return 0.
        dx, dy = u - x, v - y
        col, row = math.floor(x), math.floor(y)
        col_step, row_step = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        col_delta = abs(1 / dx) if dx else math.inf
        row_delta = abs(1 / dy) if dy else math.inf
        next_col = ((col + (dx > 0)) - x) / dx if dx else math.inf
        next_row = ((row + (dy > 0)) - y) / dy if dy else math.inf
        position = covered = 0.
        while position < 1.:
            boundary = min(1., next_col, next_row)
            if 0 <= col < size and 0 <= row < size and self.pixels[row * size + col] >= 128:
                covered += boundary - position
            position = boundary
            if next_col == boundary:
                col += col_step
                next_col += col_delta
            if next_row == boundary:
                row += row_step
                next_row += row_delta
        return length * min(1., covered)
