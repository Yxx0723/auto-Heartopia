"""Fetch and stitch the TapTap reference base map for local display only.

Why a fetcher instead of a bundled image: the tiles are TapTap-hosted artwork for
``心动小镇`` with no published redistribution licence. They are therefore fetched
at runtime and cached **locally**, never committed to the repository and never
shipped inside the distributed build. Deleting the cache restores the previous
behaviour.

Tile contract, read from ``/map/index/index?game_id=45213`` on 2026-11-24:

    {"name": "main", "path": "xz/tiles/main_v1", "type": "raster",
     "pattern": "/{z}/{x}_{y}", "min_zoom": 8, "max_zoom": 11,
     "auto_webp": 1, "extension": "jpg", "tileSize": 250,
     "bounds": [-1.4, -1.4, 1.4, 1.4]}

Measured grid (2026-11-24): z=8 covers tiles x127..128 / y127..128, so the whole
map is 2x2 tiles at z=8 and 16x16 at z=11, i.e. 4000x4000 px at the highest
native zoom.

Usage:
    python tools/fetch_base_map.py [--zoom 11] [--out DIR] [--force]

Project imports stay inside functions so importing this module does nothing.
"""
import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TILE_URL = ('https://assets.tapimg.com/cupid-apps/game-map/xz/tiles/main_v1/'
            '{z}/{x}_{y}.jpg?x-oss-process=image/format,webp/quality,q_85')
TILE_SIZE = 250
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) fhoe-rail-map/1.0'

# Grid origin measured at z=8 (x127..128, y127..128); every other level is the
# same footprint scaled by 2^(z-8).
ANCHOR_ZOOM = 8
ANCHOR_MIN_X, ANCHOR_MIN_Y = 127, 127
ANCHOR_TILES = 2


def grid_for(zoom):
    """Tile range covering the whole map at one zoom level."""
    factor = 1 << (zoom - ANCHOR_ZOOM)
    start_x = ANCHOR_MIN_X * factor
    start_y = ANCHOR_MIN_Y * factor
    count = ANCHOR_TILES * factor
    return start_x, start_x + count - 1, start_y, start_y + count - 1


def tile_path(cache_dir, zoom, x, y):
    return cache_dir / f'{zoom}' / f'{x}_{y}.jpg'


def download(cache_dir, zoom, x, y, force=False):
    """Fetch one tile into the local cache; returns (path, fetched)."""
    path = tile_path(cache_dir, zoom, x, y)
    if path.exists() and path.stat().st_size > 1024 and not force:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        TILE_URL.format(z=zoom, x=x, y=y), headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise SystemExit(f'瓦片下载失败 {zoom}/{x}_{y}: {error}')
    path.write_bytes(data)
    return path, True


def fetch_zoom(cache_dir, zoom, force=False, workers=12):
    """Download every tile of one zoom level, then report the grid."""
    from_x, to_x, from_y, to_y = grid_for(zoom)
    jobs = [(zoom, x, y) for x in range(from_x, to_x + 1)
            for y in range(from_y, to_y + 1)]
    fetched = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _path, is_new in pool.map(lambda job: download(cache_dir, *job, force=force), jobs):
            fetched += 1 if is_new else 0
    return {'zoom': zoom, 'tiles': len(jobs), 'downloaded': fetched,
            'x': (from_x, to_x), 'y': (from_y, to_y),
            'pixels': (len(range(from_x, to_x + 1)) * TILE_SIZE,
                       len(range(from_y, to_y + 1)) * TILE_SIZE)}


def stitch(cache_dir, zoom):
    """Compose every cached tile of one level into a single image."""
    from PIL import Image
    from_x, to_x, from_y, to_y = grid_for(zoom)
    width = (to_x - from_x + 1) * TILE_SIZE
    height = (to_y - from_y + 1) * TILE_SIZE
    sheet = Image.new('RGB', (width, height), '#6c9fb0')
    for x in range(from_x, to_x + 1):
        for y in range(from_y, to_y + 1):
            path = tile_path(cache_dir, zoom, x, y)
            if not path.exists():
                continue
            with Image.open(path) as tile:
                sheet.paste(tile.convert('RGB'),
                            ((x - from_x) * TILE_SIZE, (y - from_y) * TILE_SIZE))
    return sheet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zoom', type=int, default=11,
                        help='最高细节层（8..11），默认取官方 max_zoom')
    parser.add_argument('--out', default='outputs/basemap')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--install', metavar='PATH', default='runtime/basemap/basemap.png',
                        help='把拼好的底图写入程序读取的位置（该路径不入库）')
    args = parser.parse_args()
    if not 8 <= args.zoom <= 11:
        raise SystemExit('zoom 必须在 8..11 之间（官方 min_zoom/max_zoom）')

    out = Path(args.out)
    cache = out / 'tiles'
    report = fetch_zoom(cache, args.zoom, force=args.force)
    print(f"z={report['zoom']}  瓦片 {report['tiles']} 块（新下载 {report['downloaded']}）")
    print(f"  网格 x {report['x'][0]}..{report['x'][1]}  y {report['y'][0]}..{report['y'][1]}")
    print(f"  成图 {report['pixels'][0]}×{report['pixels'][1]} px")

    sheet = stitch(cache, args.zoom)
    target = out / f'basemap-z{args.zoom}.png'
    sheet.save(target)
    print(f'  {target}')

    if args.install:
        install = Path(args.install)
        install.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(install)
        print(f'  已安装到 {install}（本地缓存，不入库）')


if __name__ == '__main__':
    main()
