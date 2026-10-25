"""Bounded, per-world fixed landmarks for display only, never navigation."""
from collections import OrderedDict
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import valid_position


def freeze(value):
    return tuple(freeze(v) for v in value) if isinstance(value, (tuple, list)) else value


def item_bounds(item, kind):
    if kind == 'objects':
        p = item.get('position')
        if not valid_position(p):
            return None
        return p[0], p[0], p[2], p[2]
    polygon = item.get('polygon', ())
    if len(polygon) < 3 or any(len(p) != 2 or not all(
            type(v) in (int, float) and math.isfinite(v) for v in p) for p in polygon):
        return None
    xs, zs = zip(*polygon)
    return min(xs), max(xs), min(zs), max(zs)


def distance_to_bounds(position, bounds):
    x, z = position[0], position[2]
    return math.hypot(min(max(x, bounds[0]), bounds[1])-x,
                      min(max(z, bounds[2]), bounds[3])-z)


class SceneDisplayCache:
    def __init__(self, max_items=policy.COORD_SCENE_CACHE_MAX_ITEMS,
                 max_cells=policy.COORD_SCENE_CACHE_MAX_CELLS):
        self.max_items, self.max_cells = max_items, max_cells
        self.context, self.count = None, 0
        self.cells, self.locations = OrderedDict(), {}

    def reset(self):
        self.context, self.count = None, 0
        self.cells.clear()
        self.locations.clear()

    def remove(self, key):
        cell = self.locations.pop(key)
        del self.cells[cell][key]
        self.count -= 1
        if not self.cells[cell]:
            del self.cells[cell]

    def update(self, scene, now):
        context, position = freeze(scene.get('cache_context')), scene.get('position')
        quality = scene.get('quality')
        # Partial observation is not a confirmed absence. Until a display
        # retention policy is approved, publish fresh items without history.
        parts = quality.get('parts') if isinstance(quality, dict) else None
        entity_part = parts.get('entities') if isinstance(parts, dict) else None
        entities_partial = 'quality' in scene and (not isinstance(entity_part, dict)
                                                   or entity_part.get('state') != 'complete')
        if entities_partial or scene.get('state') != 'ready' or scene.get('world_state') != 'ready' \
                or not context or not valid_position(position):
            self.reset()
            return scene
        if context != self.context:
            self.reset()
            self.context = context
        current, live = {}, {'objects': [], 'colliders': []}
        for kind in live:
            for item in scene.get(kind, []):
                identity = freeze(item.get('map_id'))
                # Only the reader can establish a stationary landmark's identity.
                # Animals, unknown entities and home-built colliders stay live.
                eligible = item.get('map_static') is True and identity and (
                    kind == 'colliders' or item.get('category') in (
                        'tree', 'bush', 'blueberry', 'raspberry', 'shrub', 'rock', 'ore'))
                bounds = item_bounds(item, kind) if eligible else None
                if bounds is None:
                    live[kind].append(item)
                    continue
                key = (kind, identity)
                current[key] = (item, bounds)
        for key in list(self.locations):
            entry = self.cells[self.locations[key]][key]
            if now-entry['seen'] > policy.COORD_SCENE_CACHE_TTL or (
                    key not in current and distance_to_bounds(position, entry['bounds']) <= policy.COORD_SCENE_LIVE_RADIUS):
                self.remove(key)
        for key, (item, bounds) in current.items():
            old_cell = self.locations.get(key)
            old = self.cells[old_cell][key] if old_cell is not None else None
            near = distance_to_bounds(position, bounds) <= policy.COORD_SCENE_LIVE_RADIUS
            if old and not near and now-old['updated'] < policy.COORD_SCENE_CACHE_FAR_SECONDS:
                old['seen'] = now
                self.cells.move_to_end(old_cell)
                continue
            cell = (math.floor((bounds[0]+bounds[1])/2/policy.COORD_SCENE_CACHE_CELL_SIZE),
                    math.floor((bounds[2]+bounds[3])/2/policy.COORD_SCENE_CACHE_CELL_SIZE))
            if old:
                self.remove(key)
            self.cells.setdefault(cell, {})[key] = {
                'item': item, 'bounds': bounds, 'seen': now, 'updated': now}
            self.cells.move_to_end(cell)
            self.locations[key] = cell
            self.count += 1
        while self.cells and (len(self.cells) > self.max_cells or self.count > self.max_items):
            _, entries = self.cells.popitem(last=False)
            for key in entries:
                del self.locations[key]
            self.count -= len(entries)
        cached = 0
        for entries in self.cells.values():
            for (kind, _), entry in entries.items():
                item = entry['item']
                if distance_to_bounds(position, entry['bounds']) > policy.COORD_SCENE_LIVE_RADIUS:
                    item = {**item, 'map_cached': True}
                    cached += 1
                live[kind].append(item)
        # If limits evicted a populated cell, its current observations remain
        # visible. Capacity affects retained history, never the fresh snapshot.
        for key, (item, _) in current.items():
            if key not in self.locations:
                live[key[0]].append(item)
        return {**scene, **live, 'display_cache': {
            'fixed_items': self.count, 'cached_items': cached, 'cells': len(self.cells)}}
