"""Retained Tk map layers. Only drawing is cached; navigation stays live."""
from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import chain
import math

from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy
from utils.core.scene_map import barrier_strokes
from utils.core.map_resources import combine_resource_markers, RESOURCE_CATEGORIES


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int
    x: float
    z: float
    scale: float

    def screen(self, x, z):
        return self.width / 2 + (x - self.x) * self.scale, self.height / 2 - (z - self.z) * self.scale

    def bounds(self, padding=0.):
        return (self.x - (self.width / 2 + padding) / self.scale,
                self.x + (self.width / 2 + padding) / self.scale,
                self.z - (self.height / 2 + padding) / self.scale,
                self.z + (self.height / 2 + padding) / self.scale)


def polygon_data(polygon):
    xs, zs = zip(*polygon)
    return tuple(v for point in polygon for v in point), (min(xs), max(xs), min(zs), max(zs))


def intersects(a, b):
    return a[1] >= b[0] and a[0] <= b[1] and a[3] >= b[2] and a[2] <= b[3]


def object_key(obj):
    category = obj.get('category') or 'other'
    key = ('object', category, obj['position'][0], obj['position'][2])
    return (*key, 'cached') if obj.get('map_cached') else key


def muted_color(color):
    if isinstance(color, str) and len(color) == 7 and color.startswith('#'):
        return '#' + ''.join(f'{round(int(color[i:i+2], 16)*.55 + base*.45):02x}'
                             for i, base in zip((1, 3, 5), (6, 14, 22)))
    return color


def cells_for(bounds):
    step = policy.COORD_SCENE_CACHE_CELL_SIZE
    return (range(math.floor(bounds[0]/step), math.floor(bounds[1]/step)+1),
            range(math.floor(bounds[2]/step), math.floor(bounds[3]/step)+1))


def water_levels(kind, polygons, lods=()):
    if not lods:
        return [[((kind,index),*polygon_data(p)) for index,p in enumerate(polygons) if len(p)>=3]]
    return [[((kind,index,level),*polygon_data(p)) for index,p in enumerate(group) if len(p)>=3]
            for level,group in enumerate(lods)]


class SceneGeometryCache:
    """Prepare bounds once per immutable worker snapshot, not on every frame."""
    def __init__(self, water_areas, ranks, roads=(), water_islands=(), water_detail=None):
        self.ranks = ranks
        detail = water_detail or {}
        self.water_layers = {
            'water': water_levels('water',water_areas,detail.get('water_lods',())),
            'water-island': water_levels('water-island',water_islands,detail.get('island_lods',())),
            'water-deep': water_levels('water-deep',detail.get('deep_areas',()),detail.get('deep_lods',())),
            'water-deep-island': water_levels('water-deep-island',detail.get('deep_islands',()),detail.get('deep_island_lods',())),
        }
        self.water, self.water_islands = self.water_layers['water'][0], self.water_layers['water-island'][0]
        self.water_keys = {entry[0] for group in self.water_layers.values() for level in group for entry in level}
        self.water_selection = {}
        self.roads = []
        for index, road in enumerate(roads):
            points = tuple(road['points'])
            if len(points) < 2:
                continue
            if road.get('closed') and points[-1] != points[0]:
                points += (points[0],)
            self.roads.append((('road', index), *polygon_data(points)))
        self.reference_keys = self.water_keys | {entry[0] for entry in self.roads}
        self.valid_keys = self.reference_keys.copy()
        self.source, self.source_key, self.selection_key = None, None, None
        self.objects, self.boxes, self.selection = [], [], ([], [])
        self.barrier_kinds = {}
        self.cached_keys = set()
        self.object_cells, self.box_cells, self.wide_boxes = {}, {}, []
        self.preparations = 0
        self.global_indexes = []
        self.global_box_indexes = []
        self.object_sources, self.box_sources = None, None
        self.object_preparations, self.box_preparations = 0, 0

    def candidates(self, index, bounds):
        xs, zs = cells_for(bounds)
        return sorted({i for x in xs for z in zs for i in index.get((x, z), ())})

    def select(self, scene, position, radius, viewport):
        objects, boxes = scene.get('objects', []), scene.get('colliders', [])
        definitions = scene.get('global_resources', ())
        special = scene.get('global_special_resources', ())
        resources = scene.get('global_map_resources', ())
        bridges = scene.get('bridge_surfaces', ())
        source_key = (id(scene), id(objects), len(objects), id(boxes), len(boxes), id(definitions), len(definitions), id(special), len(special), id(resources), len(resources), id(bridges), len(bridges))
        if source_key != self.source_key:
            self.source, self.source_key = scene, source_key
            object_sources = (objects, definitions, special, resources)
            box_sources = (boxes, bridges)
            objects_changed = object_sources != self.object_sources
            boxes_changed = box_sources != self.box_sources
            if objects_changed:
                self.prepare_objects(object_sources)
            if boxes_changed:
                self.prepare_boxes(box_sources)
            if objects_changed or boxes_changed:
                self.valid_keys = self.reference_keys | {object_key(obj) for obj in self.objects} | {box[0] for box in self.boxes}
            self.selection_key = None
            self.preparations += 1
        selection_key = (source_key, (position[0], position[2]) if valid_position(position) else None, radius, viewport)
        if selection_key != self.selection_key:
            self.selection_key = selection_key
            if not valid_position(position):
                self.selection = ([], [])
            else:
                x, z = position[0], position[2]
                bounds = viewport.bounds(12)
                query = (max(bounds[0], x-radius), min(bounds[1], x+radius),
                         max(bounds[2], z-radius), min(bounds[3], z+radius))
                object_indexes = sorted(set(self.candidates(self.object_cells, query)) | {
                    i for i in self.global_indexes if bounds[0] <= self.objects[i]['position'][0] <= bounds[1]
                    and bounds[2] <= self.objects[i]['position'][2] <= bounds[3]})
                global_boxes = {i for i in self.global_box_indexes if intersects(self.boxes[i][2], bounds)}
                box_indexes = sorted(set(self.candidates(self.box_cells, query)) | set(self.wide_boxes) | global_boxes)
                visible_objects = [obj for i in object_indexes
                                   for obj in (self.objects[i],)
                                   if bounds[0] <= obj['position'][0] <= bounds[1]
                                   and bounds[2] <= obj['position'][2] <= bounds[3]
                                   and (obj.get('map_global') or obj.get('category') in RESOURCE_CATEGORIES or
                                        math.hypot(obj['position'][0] - x, obj['position'][2] - z) <= radius)]
                visible_boxes = [self.boxes[i] for i in box_indexes for box in (self.boxes[i],) if intersects(box[2], bounds)
                                 and (i in global_boxes or math.hypot(min(max(x, box[2][0]), box[2][1]) - x,
                                                min(max(z, box[2][2]), box[2][3]) - z) <= radius)]
                self.selection = visible_objects, visible_boxes
        return self.selection

    def prepare_objects(self, sources):
        # Worker snapshots are immutable after publication. Compare their
        # contents so a new timestamp/session wrapper cannot rebuild an
        # unchanged spatial index. Keep live fauna and availability fresh.
        self.object_sources = sources
        self.object_preparations += 1
        objects, definitions, special, resources = sources
        self.objects = sorted(combine_resource_markers(objects, [*definitions, *special, *resources]),
                              key=lambda obj: self.ranks.get(obj.get('category'), 0))
        self.object_cells = {}
        for i, obj in enumerate(self.objects):
            x, _, z = obj['position']
            cell = math.floor(x/policy.COORD_SCENE_CACHE_CELL_SIZE), math.floor(z/policy.COORD_SCENE_CACHE_CELL_SIZE)
            self.object_cells.setdefault(cell, []).append(i)
        self.global_indexes = [i for i, obj in enumerate(self.objects)
                               if obj.get('map_global') or obj.get('category') in RESOURCE_CATEGORIES]

    def prepare_boxes(self, sources):
        self.box_sources = sources
        self.box_preparations += 1
        boxes, bridges = sources
        self.boxes = []
        self.barrier_kinds = {}
        self.global_box_indexes = []
        self.cached_keys = set()
        self.box_cells, self.wide_boxes = {}, []
        for box in chain(boxes, bridges):
            coords, bounds = polygon_data(box['polygon'])
            kind = box.get('category') if box.get('source') == 'verified-solid-collider' else None
            kind = kind if kind in ('fence', 'handrail') else None
            if box.get('category') == 'mountain' and box.get('source') == 'verified-main-terrain-mesh':
                kind = 'mountain'
            if box.get('category') == 'bridge' and box.get('source') == 'bridge-mesh-reference':
                kind = 'bridge'
            key = ('collider', coords, kind)
            if box.get('map_cached'):
                key = (*key, 'cached')
                self.cached_keys.add(key)
            i = len(self.boxes)
            self.boxes.append((key, coords, bounds))
            xs, zs = cells_for(bounds)
            if len(xs)*len(zs) > 256:
                self.wide_boxes.append(i)
            else:
                for x in xs:
                    for z in zs:
                        self.box_cells.setdefault((x, z), []).append(i)
            if kind:
                self.barrier_kinds[key] = kind
            if kind in ('mountain', 'bridge') and not box.get('map_cached'):
                self.global_box_indexes.append(i)

    def visible_water(self, viewport):
        return self.visible_water_layer('water',viewport)

    def visible_water_islands(self, viewport):
        return self.visible_water_layer('water-island',viewport)

    def visible_water_layer(self,kind,viewport,level_offset=0):
        key = (kind, level_offset)
        cached = self.water_selection.get(key)
        if cached is not None and cached[0] == viewport:
            return cached[1]
        levels = self.water_layers[kind]
        level = min(len(levels)-1, level_offset + (
            0 if viewport.scale >= 2. else 1 if viewport.scale >= .85 else 2))
        bounds = viewport.bounds(5.)
        result = [entry for entry in levels[level] if intersects(entry[2],bounds)]
        self.water_selection[key] = viewport,result
        return result

    def visible_roads(self, viewport):
        bounds = viewport.bounds(8)
        return [entry for entry in self.roads if intersects(entry[2], bounds)]


class CanvasLayer:
    def __init__(self, canvas):
        self.canvas, self.items = canvas, []

    def clear(self):
        if self.items:
            self.canvas.delete(*self.items)
            self.items.clear()

    def __getattr__(self, name):
        method = getattr(self.canvas, name)
        if not name.startswith('create_'):
            return method
        def create(*coords, **options):
            item = method(*coords, **options)
            self.items.append(item)
            return item
        return create


@dataclass
class FixedEntry:
    key: tuple
    rank: int
    anchor: tuple | None
    viewport: Viewport
    commands: list = field(default_factory=list)
    tags: set = field(default_factory=set)
    visible: bool = True


class SymbolPainter:
    def __init__(self, owner, entry):
        self.owner, self.entry = owner, entry

    def __getattr__(self, name):
        if not name.startswith('create_'):
            raise AttributeError(name)
        def create(*coords, **options):
            if self.entry.key[-1] == 'cached':
                options = {k: muted_color(v) if k in ('fill', 'outline') else v for k, v in options.items()}
            item = getattr(self.owner.canvas, name)(*coords, **options)
            x, z = self.owner.viewport.screen(*self.entry.anchor)
            offsets = tuple(v - (x if i % 2 == 0 else z) for i, v in enumerate(coords))
            self.entry.commands.append((item, offsets))
            self.owner.register(self.entry, item, options.get('tags'))
            return item
        return create


class MapDrawCache:
    """Reuse items across zoom/pan and refresh only changed scene objects.

    Offscreen entries form a bounded pool for zooming back out. Items absent
    from the next source snapshot are deleted immediately, including animals.
    """
    def __init__(self, canvas, water_areas=(), ranks=None, max_entries=4096, roads=(), water_islands=(), water_detail=None):
        self.canvas = canvas
        self.geometry = SceneGeometryCache(water_areas, ranks or {}, roads, water_islands,water_detail)
        self.live = CanvasLayer(canvas)
        self.fixed = OrderedDict()
        self.viewport, self.used = None, set()
        self.visible, self.validated_keys = set(), None
        self.created, self.hits = 0, 0
        self.max_entries, self.changed = max_entries, False

    def project_symbol(self, entry):
        x, z = self.viewport.screen(*entry.anchor)
        for item, offsets in entry.commands:
            self.canvas.coords(item, *(v + (x if i % 2 == 0 else z) for i, v in enumerate(offsets)))
        entry.viewport = self.viewport

    def begin(self, viewport):
        self.live.clear()
        self.used.clear()
        self.changed = False
        old, self.viewport = self.viewport, viewport
        if old is None or old == viewport or not self.fixed:
            return
        ratio = viewport.scale / old.scale
        dx = viewport.width / 2 - old.width / 2 * ratio + (old.x - viewport.x) * viewport.scale
        dz = viewport.height / 2 - old.height / 2 * ratio + (viewport.z - old.z) * viewport.scale
        if ratio == 1.:
            self.canvas.move('map-fixed', dx, dz)
            for key in self.visible:
                self.fixed[key].viewport = viewport
        else:
            self.canvas.scale('map-fixed', 0, 0, ratio, ratio)
            self.canvas.move('map-fixed', dx, dz)
            for key in self.visible:
                entry = self.fixed[key]
                if entry.anchor is not None and entry.visible:
                    # Numeric item IDs avoid scanning every canvas tag for
                    # each glyph; keep screen size while its anchor moves.
                    x, z = viewport.screen(*entry.anchor)
                    for item, _ in entry.commands:
                        self.canvas.scale(item, x, z, 1 / ratio, 1 / ratio)
                    entry.viewport = viewport

    def existing(self, key):
        self.used.add(key)
        entry = self.fixed.get(key)
        if entry is not None:
            self.fixed.move_to_end(key)
            self.hits += 1
            if not entry.visible:
                self.set_visible(entry, True)
            if entry.anchor is not None and entry.viewport != self.viewport:
                self.project_symbol(entry)
        return entry

    def new_entry(self, key, rank, anchor):
        entry = FixedEntry(key, rank, anchor, self.viewport)
        self.fixed[key] = entry
        self.created += 1
        self.changed = True
        return entry

    def register(self, entry, item, tags):
        self.canvas.addtag_withtag('map-fixed', item)
        self.canvas.addtag_withtag(f'map-rank-{entry.rank}', item)
        if tags:
            entry.tags.update((tags,) if isinstance(tags, str) else tags)

    def set_visible(self, entry, visible):
        for item, _ in entry.commands:
            self.canvas.itemconfigure(item, state='normal' if visible else 'hidden')
        entry.visible = visible

    def polygon(self, key, coords, rank, underlay=None, **options):
        if self.existing(key) is not None:
            return
        entry = self.new_entry(key, rank, None)
        projected = [v for x, z in zip(coords[::2], coords[1::2]) for v in self.viewport.screen(x, z)]
        for layer in ([underlay] if underlay else []) + [options]:
            item = self.canvas.create_polygon(*projected, **layer)
            entry.commands.append((item, ()))
            self.register(entry, item, layer.get('tags'))

    def polyline(self, key, coords, rank, layers):
        if self.existing(key) is not None:
            return
        entry = self.new_entry(key, rank, None)
        projected = [v for x, z in zip(coords[::2], coords[1::2]) for v in self.viewport.screen(x, z)]
        for options in layers:
            item = self.canvas.create_line(*projected, **options)
            entry.commands.append((item, ()))
            self.register(entry, item, options.get('tags'))

    def barrier(self, key, coords, color, kind):
        """Retain each true collision footprint and its rail/post detail."""
        if self.existing(key) is not None:
            return
        entry = self.new_entry(key, 0, None)
        tags = ('scene-barrier', 'scene-' + kind)
        polygon = list(zip(coords[::2], coords[1::2]))
        projected = [v for point in polygon for v in self.viewport.screen(*point)]
        item = self.canvas.create_polygon(*projected, fill='', outline=color, width=1.5, tags=tags)
        entry.commands.append((item, ()))
        self.register(entry, item, tags)
        for stroke in barrier_strokes(polygon):
            projected = [v for point in stroke for v in self.viewport.screen(*point)]
            item = self.canvas.create_line(*projected, fill=color, width=1.5, tags=tags)
            entry.commands.append((item, ()))
            self.register(entry, item, tags)

    def symbol(self, key, anchor, rank, draw):
        if self.existing(key) is not None:
            return
        entry = self.new_entry(key, rank, anchor)
        x, z = self.viewport.screen(*anchor)
        draw(SymbolPainter(self, entry), x, z)

    def finish(self):
        removed = []
        # Geometry replaces valid_keys only when resource/collision contents
        # change. Avoid rehashing every retained polygon on every moving frame.
        if self.validated_keys is not self.geometry.valid_keys:
            self.validated_keys = self.geometry.valid_keys
            for key in self.fixed.keys() - self.validated_keys:
                entry = self.fixed.pop(key)
                removed.extend(item for item, _ in entry.commands)
                self.visible.discard(key)
        for key in self.visible - self.used:
            self.set_visible(self.fixed[key], False)
        self.visible = self.used.intersection(self.fixed)
        if len(self.fixed) > self.max_entries:
            for key, entry in list(self.fixed.items()):
                if len(self.fixed) <= self.max_entries:
                    break
                if not entry.visible:
                    removed.extend(item for item, _ in entry.commands)
                    del self.fixed[key]
        if removed:
            self.canvas.delete(*removed)
        if self.changed:
            # 由下往上：碰撞(-8) → 水面分层(-6/-5/-4) → 道路(-3) → 桥梁(-1) → 点位/符号。
            for rank in (-8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2):
                self.canvas.tag_raise(f'map-rank-{rank}')

    @property
    def visible_tags(self):
        return {tag for entry in self.fixed.values() if entry.visible for tag in entry.tags}
