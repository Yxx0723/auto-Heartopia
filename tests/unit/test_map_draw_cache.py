"""Retained item identity, transforms and invalidation without a desktop."""
import pytest

from utils.ui.map_draw_cache import MapDrawCache, Viewport, object_key


class Canvas:
    def __init__(self):
        self.items, self.created, self.deleted = {}, 0, []

    def create(self, *coords, **options):
        self.created += 1
        self.items[self.created] = {'coords': list(coords), 'tags': set(), 'state': 'normal'}
        return self.created

    create_oval = create_polygon = create_line = create

    def targets(self, value):
        return [self.items[value]] if isinstance(value, int) and value in self.items else [
            item for item in self.items.values() if value in item['tags']]

    def addtag_withtag(self, tag, item):
        for value in self.targets(item):
            value['tags'].add(tag)

    def coords(self, item, *coords):
        if coords:
            self.items[item]['coords'] = list(coords)
        return self.items[item]['coords']

    def move(self, tag, x, z):
        for item in self.targets(tag):
            item['coords'] = [v + (x if i % 2 == 0 else z) for i, v in enumerate(item['coords'])]

    def scale(self, tag, x, z, sx, sz):
        for item in self.targets(tag):
            item['coords'] = [(v - (x if i % 2 == 0 else z)) * (sx if i % 2 == 0 else sz)
                              + (x if i % 2 == 0 else z) for i, v in enumerate(item['coords'])]

    def itemconfigure(self, tag, **options):
        for item in self.targets(tag):
            item.update(options)

    def delete(self, *items):
        assert 'all' not in items
        for item in items:
            self.items.pop(item, None)
            self.deleted.append(item)

    def tag_raise(self, tag):
        pass


def scene(*positions):
    return {'objects': [{'position': [x, 0., z], 'category': 'bush'} for x, z in positions],
            'colliders': [{'polygon': [[8., 8.], [9., 8.], [9., 9.], [8., 9.]]}]}


def frame(cache, source, viewport, show=True):
    cache.begin(viewport)
    objects, boxes = cache.geometry.select(source, (viewport.x, 0., viewport.z), 80., viewport)
    for key, coords, _ in boxes:
        cache.polygon(key, coords, -1, tags='scene-collider')
    if show:
        for obj in objects:
            cache.symbol(object_key(obj), (obj['position'][0], obj['position'][2]), 1,
                         lambda painter, x, z: painter.create_oval(x-3, z-3, x+3, z+3, tags='scene-bush'))
    cache.finish()


def test_identical_refresh_and_repeated_frames_reuse_fixed_items():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source, viewport = scene((10, 5), (-5, 0)), Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, viewport)
    ids, created = set(canvas.items), canvas.created
    for _ in range(10):
        frame(cache, source, viewport)
    assert cache.geometry.preparations == 1
    frame(cache, scene((10, 5), (-5, 0)), viewport)
    assert set(canvas.items) == ids and canvas.created == created
    assert not canvas.deleted


def test_spatial_selection_matches_brute_force_at_expanded_radii_and_cell_edges():
    import math
    from utils.ui.map_draw_cache import SceneGeometryCache, intersects
    source = scene(*((x, z) for x in range(-512, 513, 32) for z in range(-512, 513, 32)))
    source['colliders'] = [{'polygon': [[x, z], [x+20, z], [x+20, z+12], [x, z+12]]}
                           for x in range(-500, 501, 64) for z in range(-500, 501, 64)]
    cache = SceneGeometryCache((), {})
    for radius in (80, 150, 200, 300, 400):
        for x, z in ((0., 0.), (-64., 64.), (125., -129.)):
            viewport = Viewport(900, 500, x, z, .7)
            objects, boxes = cache.select(source, (x, 0, z), radius, viewport)
            bounds = viewport.bounds(12)
            expected = [o for o in source['objects'] if bounds[0] <= o['position'][0] <= bounds[1]
                        and bounds[2] <= o['position'][2] <= bounds[3]
                        and math.hypot(o['position'][0]-x, o['position'][2]-z) <= radius]
            expected_boxes = [b for b in cache.boxes if intersects(b[2], bounds)
                              and math.hypot(min(max(x, b[2][0]), b[2][1])-x,
                                             min(max(z, b[2][2]), b[2][3])-z) <= radius]
            assert objects == expected and boxes == expected_boxes
    assert cache.preparations == 1


def test_large_world_polygon_does_not_create_an_unbounded_spatial_index():
    from utils.ui.map_draw_cache import SceneGeometryCache
    cache = SceneGeometryCache((), {})
    source = {'objects': [], 'colliders': [{'polygon': [
        [-99999., -99999.], [99999., -99999.], [99999., 99999.], [-99999., 99999.]]}]}
    _, boxes = cache.select(source, (0, 0, 0), 400, Viewport(900, 500, 0, 0, .7))
    assert len(boxes) == 1 and cache.wide_boxes == [0] and cache.box_cells == {}


def test_cached_reference_style_reuses_items_and_is_replaced_by_live_geometry():
    from utils.ui.map_draw_cache import muted_color
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source = scene((10, 5))
    source['objects'][0]['map_cached'] = True
    original = Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, original)
    cached_key = object_key(source['objects'][0])
    old = cache.fixed[cached_key].commands[0][0]
    frame(cache, source, Viewport(1000, 600, 0, 0, 12))
    assert cache.fixed[cached_key].commands[0][0] == old
    assert muted_color('#cef18b') != '#cef18b'
    frame(cache, scene((10, 5)), original)
    assert cached_key not in cache.fixed and old not in canvas.items


def test_zoom_pan_and_resize_keep_symbols_fixed_size_and_geometry_scaled():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source = scene((10, 5))
    frame(cache, source, Viewport(1000, 600, 0, 0, 6))
    icon = cache.fixed[object_key(source['objects'][0])].commands[0][0]
    box = next(entry.commands[0][0] for key, entry in cache.fixed.items() if key[0] == 'collider')
    before_box = canvas.coords(box)[:]
    created = canvas.created
    frame(cache, source, Viewport(1000, 600, 0, 0, 12))
    assert canvas.coords(icon) == pytest.approx([617, 237, 623, 243])
    assert canvas.coords(box)[2] - canvas.coords(box)[0] == pytest.approx(2 * (before_box[2] - before_box[0]))
    frame(cache, source, Viewport(1200, 800, 3, 4, 12))
    assert canvas.coords(icon) == pytest.approx([681, 385, 687, 391])
    assert canvas.created == created


def test_filter_hides_and_restores_the_same_ids_without_ghost_items():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source, viewport = scene((10, 5)), Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, viewport)
    icon = cache.fixed[object_key(source['objects'][0])].commands[0][0]
    created = canvas.created
    frame(cache, source, viewport, show=False)
    assert canvas.items[icon]['state'] == 'hidden' and 'scene-bush' not in cache.visible_tags
    frame(cache, source, viewport)
    assert canvas.items[icon]['state'] == 'normal' and canvas.created == created


def test_moving_object_is_updated_but_unchanged_object_is_retained():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    viewport = Viewport(1000, 600, 0, 0, 6)
    source = scene((10, 5), (-5, 0))
    frame(cache, source, viewport)
    old_moving = cache.fixed[object_key(source['objects'][0])].commands[0][0]
    fixed = cache.fixed[object_key(source['objects'][1])].commands[0][0]
    frame(cache, scene((11, 5), (-5, 0)), viewport)
    assert old_moving not in canvas.items and fixed in canvas.items
    assert 'scene-bush' in cache.visible_tags


def test_offscreen_zoom_pool_is_bounded_and_hidden_icons_restore_correctly():
    canvas = Canvas()
    cache = MapDrawCache(canvas, max_entries=3)
    source = scene((0, 0), (10, 0), (20, 0), (30, 0))
    original = Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, original)
    frame(cache, source, Viewport(100, 100, 0, 0, 12))
    assert len(cache.fixed) <= 3
    frame(cache, source, original)
    for obj in source['objects']:
        icon = cache.fixed[object_key(obj)].commands[0][0]
        x, z = original.screen(obj['position'][0], obj['position'][2])
        assert canvas.coords(icon) == pytest.approx([x-3, z-3, x+3, z+3])


def test_scene_unload_discards_old_geometry_and_reconnect_rebuilds_it():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source, viewport = scene((10, 5)), Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, viewport)
    ids = set(canvas.items)
    frame(cache, {'objects': [], 'colliders': []}, viewport)
    assert not cache.fixed and not canvas.items
    frame(cache, source, viewport)
    assert not ids & set(canvas.items)


def test_repeated_zoom_round_trip_does_not_drift_world_positions_or_icon_size():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    source, original = scene((10, 5)), Viewport(1000, 600, 0, 0, 6)
    frame(cache, source, original)
    initial = {item: value['coords'][:] for item, value in canvas.items.items()}
    for _ in range(40):
        frame(cache, source, Viewport(1000, 600, 2, 3, 9))
        frame(cache, source, original)
    for item, coords in initial.items():
        assert canvas.coords(item) == pytest.approx(coords, abs=1e-7)


def test_cached_reference_roads_preserve_open_gaps_close_only_explicit_loops_and_transform_together():
    canvas = Canvas()
    roads = [{'points': ((-10., 0.), (0., 0.)), 'closed': False},
             {'points': ((4., 0.), (8., 0.), (8., 4.)), 'closed': True}]
    cache = MapDrawCache(canvas, roads=roads)
    source = {'objects': [], 'colliders': []}
    def draw(viewport, visible=True):
        cache.begin(viewport)
        cache.geometry.select(source, (viewport.x, 0., viewport.z), 80., viewport)
        if visible:
            for key, coords, _ in cache.geometry.visible_roads(viewport):
                cache.polyline(key, coords, -3, [{'fill': 'grey', 'width': 5}, {'fill': 'white', 'width': 1}])
        cache.finish()
    original = Viewport(1000, 600, 0, 0, 6)
    draw(original)
    assert len(cache.fixed) == 2 and len(canvas.items) == 4
    first = cache.fixed['road', 0].commands[0][0]
    second = cache.fixed['road', 1].commands[0][0]
    assert len(canvas.coords(first)) == 4
    assert canvas.coords(second)[:2] == canvas.coords(second)[-2:]
    identifiers = set(canvas.items)
    moved = Viewport(800, 500, 3, 2, 9)
    draw(moved, visible=False)
    draw(moved)
    assert set(canvas.items) == identifiers
    expected = [v for x, z in roads[0]['points'] for v in moved.screen(x, z)]
    assert canvas.coords(first) == pytest.approx(expected)
    source = {'objects': [], 'colliders': []}
    draw(moved)
    assert set(canvas.items) == identifiers


def test_water_shore_layers_reuse_the_same_projected_geometry_and_visibility():
    canvas = Canvas()
    cache = MapDrawCache(canvas, water_areas=(((0., 0.), (5., 0.), (5., 5.)),))
    def draw(viewport, visible=True):
        cache.begin(viewport)
        cache.geometry.select({'objects': [], 'colliders': []}, (0., 0., 0.), 80., viewport)
        if visible:
            for key, coords, _ in cache.geometry.visible_water(viewport):
                cache.polygon(key, coords, -2, underlay={'fill': '', 'outline': 'grey', 'width': 5},
                              fill='blue', outline='white')
        cache.finish()
    first, zoomed = Viewport(1000, 600, 0, 0, 6), Viewport(1000, 600, 1, 2, 12)
    draw(first)
    ids = set(canvas.items)
    assert len(ids) == 2
    draw(zoomed, visible=False)
    draw(zoomed)
    assert set(canvas.items) == ids
    a, b = ids
    assert canvas.coords(a) == pytest.approx(canvas.coords(b))
