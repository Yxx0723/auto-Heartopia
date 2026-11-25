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
import sys
from utils.core.road_preference import WorldToImage, load_road_mask

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

    tags = 'base-map'

    def __init__(self, canvas, root=None):
        self.canvas = canvas
        default_root = (Path(sys.executable).resolve().parent / DEFAULT_ROOT
                        if getattr(sys, 'frozen', False) else DEFAULT_ROOT)
        self.root = Path(root) if root is not None else default_root
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

    def _viewport_raster(self, viewport, fill):
        from PIL import Image

        left, _, _, high = viewport.bounds()
        master, size = self._level(viewport.scale)
        shrink = size / self.master.width
        x, y = self.transform(left, high)
        step = self.transform.scale * shrink / viewport.scale
        return master.transform((viewport.width, viewport.height), Image.AFFINE,
                                (step, 0, x * shrink, 0, step, y * shrink),
                                resample=Image.BILINEAR, fillcolor=fill)

    def compose(self, viewport):
        """Return a calibrated image, filling exposed space beyond the map."""
        return self._viewport_raster(viewport, FALLBACK_FILL)

    def _render(self, viewport):
        from PIL import ImageTk

        canvas_image = self.compose(viewport)
        if (self.photo is None or
                (self.photo.width(), self.photo.height()) != canvas_image.size):
            self.photo = ImageTk.PhotoImage(canvas_image)
        else:
            # Re-using one PhotoImage avoids a full Tk image round-trip per pan
            # frame; paste updates the pixels in place.
            self.photo.paste(canvas_image)
        if self.item is None:
            self.item = self.canvas.create_image(0, 0, anchor='nw', image=self.photo,
                                                 tags=self.tags)
        else:
            self.canvas.itemconfigure(self.item, image=self.photo, state='normal')
            self.canvas.coords(self.item, 0, 0)
        self.canvas.tag_lower(self.item)

    def invalidate(self):
        """Force the next draw to rebuild, e.g. after the cache is refreshed."""
        self.signature = None


class RoadMaskLayer(BaseMapLayer):
    """Render the same calibrated road mask used for soft navigation costs."""

    tags = ('reference-road', 'map-rank--1')

    def _load(self):
        try:
            self.master, self.transform = load_road_mask(self.root)
        except (ImportError, OSError, ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError) as error:
            self.error = str(error)

    def compose(self, viewport):
        from PIL import Image
        from utils.ui.theme import MAP_STYLE

        alpha = self._viewport_raster(viewport, 0).point(lambda value: value * .40)
        overlay = Image.new('RGBA', alpha.size, MAP_STYLE['road'])
        overlay.putalpha(alpha)
        return overlay
