"""Retained Tk map layers. Only drawing is cached; navigation stays live."""
from collections import OrderedDict
from dataclasses import dataclass, field
import math

from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy
from utils.core.scene_map import barrier_strokes
from utils.core.map_resources import combine_resource_markers
from utils.ui.collision_display import display_corners, collision_faces


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


def nearby_model_key(obj):
    return ('nearby-model', obj.get('model_type', obj.get('kind', 'object')), tuple(obj['position']))


def polygon_distance(x, z, coords):
    """Horizontal distance to the footprint, including a point inside it."""
    points = list(zip(coords[::2], coords[1::2]))
    inside, distance = False, math.inf
    for (ax, az), (bx, bz) in zip(points, points[1:] + points[:1]):
        if (az > z) != (bz > z) and x < (bx-ax)*(z-az)/(bz-az)+ax:
            inside = not inside
        dx, dz = bx-ax, bz-az
        length = dx*dx+dz*dz
        t = max(0., min(1., ((x-ax)*dx+(z-az)*dz)/length)) if length else 0.
        distance = min(distance, math.hypot(ax+t*dx-x, az+t*dz-z))
    return 0. if inside else distance


def object_key(obj):
    category = obj.get('category') or 'other'
    key = ('object', category, obj['position'][0], obj['position'][2])
    return (*key, 'cached') if obj.get('map_cached') else key


def muted_color(color):
    if isinstance(color, str) and len(color) == 7 and color.startswith('#'):
        return '#' + ''.join(f'{round(int(color[i:i+2], 16)*.55 + base*.45):02x}'
                             for i, base in zip((1, 3, 5), (23, 34, 28)))
    return color


def cells_for(bounds):
    step = policy.COORD_SCENE_CACHE_CELL_SIZE
    return (range(math.floor(bounds[0]/step), math.floor(bounds[1]/step)+1),
            range(math.floor(bounds[2]/step), math.floor(bounds[3]/step)+1))


class SceneGeometryCache:
    """Prepare bounds once per immutable worker snapshot, not on every frame."""
    def __init__(self, water_areas, ranks, roads=(), water_islands=()):
        self.ranks = ranks
        self.water = [(('water', index), *polygon_data(polygon))
                      for index, polygon in enumerate(water_areas) if len(polygon) >= 3]
        self.water_keys = {entry[0] for entry in self.water}
        self.water_islands = [(('water-island', index), *polygon_data(polygon))
                              for index, polygon in enumerate(water_islands) if len(polygon) >= 3]
        self.roads = []
        for index, road in enumerate(roads):
            points = tuple(road['points'])
            if len(points) < 2:
                continue
            if road.get('closed') and points[-1] != points[0]:
                points += (points[0],)
            self.roads.append((('road', index), *polygon_data(points)))
        self.reference_keys = self.water_keys | {entry[0] for entry in self.water_islands} \
            | {entry[0] for entry in self.roads}
        self.valid_keys = self.reference_keys.copy()
        self.source, self.source_key, self.selection_key = None, None, None
        self.objects, self.boxes, self.selection = [], [], ([], [])
        self.barrier_kinds = {}
        self.cached_keys = set()
        self.object_cells, self.box_cells, self.wide_boxes = {}, {}, []
        self.preparations = 0
        self.global_indexes = []
        self.models, self.model_cells, self.box_details = [], {}, []

    def candidates(self, index, bounds):
        xs, zs = cells_for(bounds)
        return sorted({i for x in xs for z in zs for i in index.get((x, z), ())})

    def select(self, scene, position, radius, viewport):
        objects, boxes = scene.get('objects', []), scene.get('colliders', [])
        definitions = scene.get('global_resources', ())
        models = scene.get('models', ())
        source_key = (id(scene), id(objects), len(objects), id(boxes), len(boxes), id(definitions), len(definitions),
                      id(models), len(models))
        if source_key != self.source_key:
            self.source, self.source_key = scene, source_key
            self.objects = sorted(combine_resource_markers(objects, definitions),
                                  key=lambda obj: self.ranks.get(obj.get('category'), 0))
            self.boxes = []
            self.barrier_kinds = {}
            self.cached_keys = set()
            self.object_cells, self.box_cells, self.wide_boxes = {}, {}, []
            self.models, self.model_cells, self.box_details = [], {}, []
            seen_models = set()
            for obj in [*models, *(obj for obj in objects if obj.get('category') in (None, 'other'))]:
                if obj.get('map_cached') or not valid_position(obj.get('position')):
                    continue
                key = nearby_model_key(obj)
                if key in seen_models:
                    continue
                seen_models.add(key)
                x, _, z = obj['position']
                cell = math.floor(x/policy.COORD_SCENE_CACHE_CELL_SIZE), math.floor(z/policy.COORD_SCENE_CACHE_CELL_SIZE)
                self.model_cells.setdefault(cell, []).append(len(self.models))
                self.models.append(obj)
            for i, obj in enumerate(self.objects):
                x, _, z = obj['position']
                cell = math.floor(x/policy.COORD_SCENE_CACHE_CELL_SIZE), math.floor(z/policy.COORD_SCENE_CACHE_CELL_SIZE)
                self.object_cells.setdefault(cell, []).append(i)
            self.global_indexes = [i for i, obj in enumerate(self.objects) if obj.get('map_global')]
            for box in boxes:
                coords, bounds = polygon_data(box['polygon'])
                kind = box.get('category') if box.get('source') == 'verified-solid-collider' else None
                kind = kind if kind in ('fence', 'handrail') else None
                if box.get('category') == 'mountain' and box.get('source') == 'verified-main-terrain-mesh':
                    kind = 'mountain'
                key = ('collider', coords, kind)
                if box.get('map_cached'):
                    key = (*key, 'cached')
                    self.cached_keys.add(key)
                i = len(self.boxes)
                self.boxes.append((key, coords, bounds))
                self.box_details.append((('nearby-collider', coords, tuple(box.get('height', ())), display_corners(box)), box))
                xs, zs = cells_for(bounds)
                if len(xs)*len(zs) > 256:
                    self.wide_boxes.append(i)
                else:
                    for x in xs:
                        for z in zs:
                            self.box_cells.setdefault((x, z), []).append(i)
                if kind:
                    self.barrier_kinds[key] = kind
            self.valid_keys = self.reference_keys | {object_key(obj) for obj in self.objects} | {box[0] for box in self.boxes}
            self.valid_keys.update(nearby_model_key(obj) for obj in self.models)
            self.valid_keys.update(key for key, _ in self.box_details)
            self.selection_key = None
            self.preparations += 1
        selection_key = (source_key, tuple(position) if position is not None else None, radius, viewport)
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
                box_indexes = sorted(set(self.candidates(self.box_cells, query)) | set(self.wide_boxes))
                visible_objects = [obj for i in object_indexes
                                   for obj in (self.objects[i],)
                                   if bounds[0] <= obj['position'][0] <= bounds[1]
                                   and bounds[2] <= obj['position'][2] <= bounds[3]
                                   and (obj.get('map_global') or
                                        math.hypot(obj['position'][0] - x, obj['position'][2] - z) <= radius)]
                visible_boxes = [self.boxes[i] for i in box_indexes for box in (self.boxes[i],) if intersects(box[2], bounds)
                                 and math.hypot(min(max(x, box[2][0]), box[2][1]) - x,
                                                min(max(z, box[2][2]), box[2][3]) - z) <= radius]
                self.selection = visible_objects, visible_boxes
        return self.selection

    def nearby_details(self, position):
        """Live display only. Never adds geometry to the navigation snapshot."""
        if not valid_position(position):
            return [], []
        x, _, z = position
        radius = policy.COORD_MAP_DETAIL_RADIUS
        bounds = (x-radius, x+radius, z-radius, z+radius)
        models = [self.models[i] for i in self.candidates(self.model_cells, bounds)
                  if math.hypot(self.models[i]['position'][0]-x, self.models[i]['position'][2]-z) <= radius]
        indexes = sorted(set(self.candidates(self.box_cells, bounds)) | set(self.wide_boxes))
        boxes = [self.box_details[i] for i in indexes
                 if not self.box_details[i][1].get('map_cached')
                 and intersects(self.boxes[i][2], bounds)
                 and polygon_distance(x, z, self.boxes[i][1]) <= radius]
        return models, boxes

    def visible_water(self, viewport):
        bounds = viewport.bounds()
        return [entry for entry in self.water if intersects(entry[2], bounds)]

    def visible_water_islands(self, viewport):
        bounds = viewport.bounds()
        return [entry for entry in self.water_islands if intersects(entry[2], bounds)]

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
    def __init__(self, canvas, water_areas=(), ranks=None, max_entries=4096, roads=(), water_islands=()):
        self.canvas = canvas
        self.geometry = SceneGeometryCache(water_areas, ranks or {}, roads, water_islands)
        self.live = CanvasLayer(canvas)
        self.fixed = OrderedDict()
        self.viewport, self.used = None, set()
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
            for entry in self.fixed.values():
                if entry.visible:
                    entry.viewport = viewport
        else:
            self.canvas.scale('map-fixed', 0, 0, ratio, ratio)
            self.canvas.move('map-fixed', dx, dz)
            for entry in self.fixed.values():
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
        if len(coords) == 4:
            layers = []
            for layer in ([underlay] if underlay else []) + [options]:
                line = {k: v for k, v in layer.items() if k in ('width', 'dash', 'tags', 'state')}
                line['fill'] = layer.get('outline') or layer.get('fill') or '#858585'
                layers.append(line)
            return self.polyline(key, coords, rank, layers)
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

    def collision_detail(self, key, box):
        if box.get('projection_kind') == 'segment' and len(box.get('polygon', ())) == 2:
            return self.polyline(key, tuple(v for point in box['polygon'] for v in point), -1,
                                 [{'fill':'#858585', 'width':2,
                                   'tags':('scene-collider', 'nearby-collision', 'collision-side')}])
        if self.existing(key) is not None:
            return
        faces = collision_faces(box)
        entry = self.new_entry(key, -1, None)
        if faces is None:
            coords = [v for point in box['polygon'] for v in self.viewport.screen(*point)]
            tags = ('scene-collider', 'nearby-collision', 'collision-unknown')
            item = self.canvas.create_polygon(*coords, fill='', outline='#aaa9a0', width=1.5,
                                              dash=(4, 3), tags=tags)
            entry.commands.append((item, ()))
            self.register(entry, item, tags)
            return
        # Fill candidate top faces first; projected side edges stay visible.
        for kind, polygon in sorted(faces, key=lambda f: f[0] != 'top'):
            projected = [self.viewport.screen(*point) for point in polygon]
            coords = [v for point in projected for v in point]
            tags = ('scene-collider', 'nearby-collision', 'collision-'+kind)
            if kind == 'top':
                item = self.canvas.create_polygon(*coords, fill='#bfdac0', stipple='gray25',
                                                  outline='#9ab69b', width=1, tags=tags)
            else:
                item = self.canvas.create_line(*coords, *projected[0], fill='#858585', width=2, tags=tags)
            entry.commands.append((item, ()))
            self.register(entry, item, tags)

    def barrier(self, key, coords, color, kind):
        """Retain each true collision footprint and its rail/post detail."""
        if len(coords) == 4:
            return self.polyline(key, coords, 0, [{'fill':color, 'width':1.5,
                                                 'tags':('scene-barrier','scene-'+kind)}])
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
        for key, entry in list(self.fixed.items()):
            if key not in self.geometry.valid_keys:
                removed.extend(item for item, _ in entry.commands)
                del self.fixed[key]
            elif key not in self.used and entry.visible:
                self.set_visible(entry, False)
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
            for rank in (-3, -2, -1, 0, 1, 2):
                self.canvas.tag_raise(f'map-rank-{rank}')

    @property
    def visible_tags(self):
        return {tag for entry in self.fixed.values() if entry.visible for tag in entry.tags}
