"""Static high-resolution base map underlay.

The layer holds one calibrated raster and re-crops it only when the viewport
changes. It never runs per frame: ``draw`` exits on a matching signature, so a
steady map costs one comparison and zero image work, which is what keeps the
M3-P1 frame budget intact.

The image itself is a **local cache**, not a repository asset. It is produced by
``tools/fetch_base_map.py --install`` from TapTap-hosted artwork and stays out of
version control; the companion ``basemap.json`` carries the measured world
transform written by ``tools/calibrate_base_map.py --install``. Both live under
``runtime/`` — deliberately outside ``map/``, which the shipped-map validators
parse as route data. When either file
is missing the layer reports unavailable and the caller keeps the M3 rendering.
"""
from collections import OrderedDict
from pathlib import Path

DEFAULT_ROOT = Path('runtime') / 'basemap'
FALLBACK_FILL = '#6c9fb0'   # matches the reference map's declared bg_color

# Cropping the 4000 px master for a wide view means resampling ~15 Mpx every
# pan frame (measured 57.9 ms). Caching a small pyramid and picking the level
# closest above the needed resolution cuts that by roughly 8x.
LEVEL_STEP = 0.7071         # 1/sqrt(2): every step halves the pixel count
MIN_LEVEL = 500
MAX_CACHED_LEVELS = 3


class BaseMapLayer:
    """Draws the cached base map as a single canvas image item."""

    def __init__(self, canvas, root=None):
        self.canvas = canvas
        self.root = Path(root) if root else DEFAULT_ROOT
        self.master = None
        self.transform = None
        self.error = None
        self.photo = None
        self.item = None
        self.signature = None
        self.redraws = 0
        self.skipped = 0
        self._levels = OrderedDict()     # size -> resized master, capped below
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self):
        image_path = self.root / 'basemap.png'
        meta_path = self.root / 'basemap.json'
        if not image_path.exists() or not meta_path.exists():
            self.error = '缺少本地底图缓存'
            return
        try:
            import json
            from PIL import Image
            self.master = Image.open(image_path).convert('RGB')
            self.transform = WorldToImage(json.loads(meta_path.read_text(encoding='utf-8')),
                                          self.master.width)
        except Exception as error:                      # noqa: BLE001 - degrade, never crash the map
            self.master, self.transform, self.error = None, None, str(error)

    @property
    def available(self):
        return self.master is not None and self.transform is not None

    # ------------------------------------------------------------------ draw
    def draw(self, viewport, enabled=True):
        """Repaint the underlay if the viewport changed; True when it drew."""
        if not enabled or not self.available:
            if self.item is not None:
                self.canvas.itemconfigure(self.item, state='hidden')
            return False
        key = (round(viewport.x, 3), round(viewport.z, 3), round(viewport.scale, 4),
               viewport.width, viewport.height)
        if key == self.signature and self.item is not None:
            self.skipped += 1
            self.canvas.itemconfigure(self.item, state='normal')
            return False
        self.signature = key
        self._render(viewport)
        self.redraws += 1
        return True

    def _level(self, needed_px_per_unit):
        """Smallest cached level that still has enough resolution for this zoom."""
        full = self.master.width
        ppu_full = self.transform.scale          # px per world unit at full size
        size = full
        while size > MIN_LEVEL and ppu_full >= needed_px_per_unit * 2:
            ppu_full /= 2
            size //= 2
        size = max(MIN_LEVEL, size)
        cached = self._levels.get(size)
        if cached is None:
            cached = self.master if size == full else \
                self.master.resize((size, size), 3)   # 3 == Image.BICUBIC
            self._levels[size] = cached
            while len(self._levels) > MAX_CACHED_LEVELS:
                self._levels.popitem(last=False)
        else:
            self._levels.move_to_end(size)
        return cached, size

    def compose(self, viewport):
        """Return the base map as a PIL image sized to the viewport."""
        from PIL import Image

        left, right, low, high = viewport.bounds()
        master, size = self._level(viewport.scale)
        shrink = size / self.master.width
        x0, y0 = self.transform(left, high)
        x1, y1 = self.transform(right, low)
        box = (int(min(x0, x1) * shrink), int(min(y0, y1) * shrink),
               int(max(x0, x1) * shrink), int(max(y0, y1) * shrink))
        box = (max(0, box[0]), max(0, box[1]),
               min(size, box[2]), min(size, box[3]))
        canvas_image = Image.new('RGB', (viewport.width, viewport.height), FALLBACK_FILL)
        if box[2] > box[0] and box[3] > box[1]:
            crop = master.crop(box).resize((viewport.width, viewport.height), Image.BILINEAR)
            canvas_image.paste(crop, (0, 0))
        return canvas_image

    def _render(self, viewport):
        from PIL import ImageTk

        canvas_image = self.compose(viewport)
        if self.photo is None:
            self.photo = ImageTk.PhotoImage(canvas_image)
        else:
            # Re-using one PhotoImage avoids a full Tk image round-trip per pan
            # frame; paste updates the pixels in place.
            self.photo.paste(canvas_image)
        if self.item is None:
            self.item = self.canvas.create_image(0, 0, anchor='nw', image=self.photo,
                                                 tags='base-map')
        else:
            self.canvas.itemconfigure(self.item, image=self.photo, state='normal')
            self.canvas.coords(self.item, 0, 0)
        self.canvas.tag_lower(self.item)

    def invalidate(self):
        """Force the next draw to rebuild, e.g. after the cache is refreshed."""
        self.signature = None


class WorldToImage:
    """Affine world XZ -> base image pixel, from the measured calibration."""

    def __init__(self, calibration, size):
        scale = calibration['scale'] * size / calibration['grid']
        self.scale = scale
        self.cx, self.cz = calibration['world_center_of_water']
        self.dx = calibration['dx'] * size / calibration['grid']
        self.dy = calibration['dy'] * size / calibration['grid']
        self.half = size / 2

    def __call__(self, x, z):
        return (self.half + (x - self.cx) * self.scale + self.dx,
                self.half - (z - self.cz) * self.scale + self.dy)
