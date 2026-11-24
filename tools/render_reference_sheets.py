"""Build the M4-R1 comparison sheets against the TapTap reference base map.

Produces, all from the calibrated base map plus the project's own world data:

* ``compare-m3-vs-taptap.png`` — M3 standard rendering beside the reference map
* ``region-*.png`` — the four regions the acceptance list names, cropped by world
  coordinates, so the crop itself proves the transform is usable
* ``alignment-check.png`` — world outlines drawn over the artwork (separate tool)

The base map is fetched at runtime by ``fetch_base_map.py``; nothing here
redistributes it.

Usage:
    python tools/render_reference_sheets.py [--out outputs/basemap]
"""
import argparse
import json
import sys
from pathlib import Path

# Acceptance regions, in game world XZ. Crop windows are deliberately generous so
# the surrounding context stays visible.
REGIONS = (
    ('北部山地', 10.0, 220.0, 90.0, 290.0),
    ('草原湖', -240.0, -110.0, -90.0, 40.0),
    ('东侧森林', 110.0, 300.0, -70.0, 110.0),
    ('中央城镇与住宅区', -70.0, 110.0, -60.0, 110.0),
)
CROP_PX = 760


def _load():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    return None


class Transform:
    def __init__(self, calibration, size):
        grid = calibration['grid']
        self.k = size / grid
        self.scale = calibration['scale'] * self.k
        self.cx, self.cz = calibration['world_center_of_water']
        self.dx = calibration['dx'] * self.k
        self.dy = calibration['dy'] * self.k
        self.half = size / 2

    def __call__(self, x, z):
        return (self.half + (x - self.cx) * self.scale + self.dx,
                self.half - (z - self.cz) * self.scale + self.dy)


def label(draw, text, font, box=(14, 12)):
    x, y = box
    width = int(len(text) * 15 + 22)
    draw.rectangle([x - 8, y - 6, x + width, y + 28], fill=(247, 245, 239, 235))
    draw.text((x, y), text, fill=(45, 58, 51), font=font)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--basemap', default='outputs/basemap/basemap-z11.png')
    parser.add_argument('--calibration', default='outputs/basemap/calibration.json')
    parser.add_argument('--m3', default='outputs/m4/standard.png')
    parser.add_argument('--out', default='outputs/basemap')
    args = parser.parse_args()

    from PIL import Image, ImageDraw, ImageFont

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    calibration = json.loads(Path(args.calibration).read_text(encoding='utf-8'))
    base = Image.open(args.basemap).convert('RGB')
    project = Transform(calibration, base.width)

    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    title_font = ImageFont.truetype(str(font_path), 30) if font_path.exists() \
        else ImageFont.load_default()
    small_font = ImageFont.truetype(str(font_path), 20) if font_path.exists() \
        else ImageFont.load_default()

    # Full-map sheet: M3 rendering beside the reference artwork.
    size = 900
    m3 = Image.open(args.m3).convert('RGB')
    m3.thumbnail((size, size), Image.LANCZOS)
    reference = base.resize((size, size), Image.LANCZOS)
    pad, header = 24, 56
    sheet = Image.new('RGB', (size * 2 + pad * 3, size + header + pad * 2), '#EFEBE2')
    sheet.paste(m3, (pad, header + pad // 2))
    sheet.paste(reference, (pad * 2 + size, header + pad // 2))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 14), 'M3 标准地图（当前）', fill='#2D3A33', font=small_font)
    draw.text((pad * 2 + size, 14), 'TapTap 参考底图（z=11 原生 4000px）',
              fill='#2D3A33', font=small_font)
    full_path = out / 'compare-m3-vs-taptap.png'
    sheet.save(full_path)
    print(full_path)

    # Regional crops, taken through the calibrated transform.
    for name, x0, x1, z0, z1 in REGIONS:
        left, top = project(x0, z1)
        right, bottom = project(x1, z0)
        box = (max(0, int(left)), max(0, int(top)),
               min(base.width, int(right)), min(base.height, int(bottom)))
        if box[2] <= box[0] or box[3] <= box[1]:
            print(f'跳过 {name}：世界窗口落在底图之外')
            continue
        crop = base.crop(box)
        ratio = CROP_PX / max(crop.width, crop.height)
        crop = crop.resize((max(1, int(crop.width * ratio)),
                            max(1, int(crop.height * ratio))), Image.LANCZOS)
        canvas = Image.new('RGB', (crop.width, crop.height + 44), '#F7F5EF')
        canvas.paste(crop, (0, 44))
        caption = ImageDraw.Draw(canvas)
        caption.text((12, 10), f'{name}  世界 X {x0:g}~{x1:g}  Z {z0:g}~{z1:g}',
                     fill='#2D3A33', font=small_font)
        path = out / f'region-{name}.png'
        canvas.save(path)
        print(path)


if __name__ == '__main__':
    main()
