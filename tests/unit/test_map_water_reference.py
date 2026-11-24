"""World-aligned display water must not be cut by the old extraction ROIs."""
import math
import pytest

from utils.core.coordinate_navigation import inside
from utils.core.map_water_reference import MAP_WATER_AREAS, MAP_WATER_ISLANDS
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from tests.unit.test_map_draw_cache import Canvas


def world(pixel):
    return (pixel[0]-511)/1.55,(452-pixel[1])/1.55


def wet(pixel):
    point=world(pixel)
    return any(inside(point,p) for p in MAP_WATER_AREAS) \
        and not any(inside(point,p) for p in MAP_WATER_ISLANDS)


@pytest.mark.parametrize('pixel', [(325,270),(333,269),(340,270),(640,300),(352,625)])
def test_river_sections_cross_old_roi_edges_without_disappearing(pixel):
    assert wet(pixel)


@pytest.mark.parametrize('pixel', [(477,400),(287,362),(257,597),(840,553)])
def test_town_house_pointer_glow_and_dry_island_are_not_water(pixel):
    assert not wet(pixel)


def test_reference_vertices_are_finite_and_small_islands_are_preserved():
    assert len(MAP_WATER_AREAS)>=50 and MAP_WATER_ISLANDS
    assert all(len(p)>=3 and all(math.isfinite(v) for vertex in p for v in vertex)
               for p in (*MAP_WATER_AREAS,*MAP_WATER_ISLANDS))


def test_water_and_island_items_survive_zoom_and_visibility_changes_in_cache():
    canvas=Canvas()
    cache=MapDrawCache(canvas,water_areas=(((-10,-10),(10,-10),(10,10),(-10,10)),),
                       water_islands=(((-2,-2),(2,-2),(2,2),(-2,2)),))
    for scale,visible in ((2.,True),(3.,True),(3.,False),(2.,True)):
        viewport=Viewport(200,200,0,0,scale)
        cache.begin(viewport)
        if visible:
            for key,coords,_ in cache.geometry.visible_water(viewport):
                cache.polygon(key,coords,-2,fill='blue',tags='water')
            for key,coords,_ in cache.geometry.visible_water_islands(viewport):
                cache.polygon(key,coords,-1,fill='green',tags='island')
        cache.finish()
        assert canvas.created==2
        assert all(item['state']==('normal' if visible else 'hidden') for item in canvas.items.values())
    assert ('water-island',0) in cache.geometry.reference_keys

@pytest.mark.parametrize('pixel', [(50,450),(980,450),(600,770),(50,150),(770,110)])
def test_coastal_sea_is_present_across_map(pixel):
    assert wet(pixel)


@pytest.mark.parametrize('pixel', [(120,170),(195,230),(920,365),(925,615),(740,750)])
def test_offshore_islands_remain_land(pixel):
    assert not wet(pixel)


@pytest.mark.parametrize('point', [(-187, -12), (-188, -17), (-182, -24), (-184, -21)])
def test_grassland_pond_fills_reference_water_and_keeps_narrow_link(point):
    from utils.core.map_water_reference import GRASSLAND_POND
    assert inside(point, GRASSLAND_POND)
    assert not any(inside(point, island) for island in MAP_WATER_ISLANDS)


@pytest.mark.parametrize('point', [(-175, -20), (-194, -16), (-181, -7), (-182, -29), (-179, -18)])
def test_grassland_pond_dry_banks_are_not_filled(point):
    from utils.core.map_water_reference import GRASSLAND_POND
    assert not inside(point, GRASSLAND_POND)


def test_grassland_pond_ring_is_simple_and_closed_by_canvas():
    from utils.core.map_water_reference import GRASSLAND_POND
    from utils.core.coordinate_navigation import segments_cross
    ring = GRASSLAND_POND
    assert len(ring) == len(set(ring)) >= 35
    edges = list(zip(ring, ring[1:] + ring[:1]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges[i+2:], i+2):
            if i == 0 and j == len(edges)-1:
                continue
            assert not segments_cross(a, b, c, d)
    assert edges[-1][-1] == edges[0][0]


def test_grassland_pond_cache_tracks_scene_zoom_pan_and_resize():
    from utils.core.map_water_reference import GRASSLAND_POND
    canvas = Canvas()
    cache = MapDrawCache(canvas, MAP_WATER_AREAS, water_islands=MAP_WATER_ISLANDS,
                         main_map_water=(GRASSLAND_POND,))
    pond_key = ('water', MAP_WATER_AREAS.index(GRASSLAND_POND))
    for width, height, x, z, scale, main_map in (
            (600, 400, -179, -15, 3., True),
            (600, 400, -185, -20, 7., False),
            (850, 525, -183, -16, 10., True),
            (850, 525, 80, 80, 10., True),
            (780, 520, -180, -10, 5., True)):
        viewport = Viewport(width, height, x, z, scale)
        cache.begin(viewport)
        for key, coords, _ in cache.geometry.visible_water(viewport, main_map=main_map):
            cache.polygon(key, coords, -2, fill='blue')
        cache.finish()
        entry = cache.fixed[pond_key]
        assert entry.visible == (main_map and x < 0)
        item, _ = entry.commands[0]
        if entry.visible:
            assert canvas.coords(item) == pytest.approx([
                value for point in GRASSLAND_POND for value in viewport.screen(*point)])
        assert len(entry.commands) == 1
    assert len(cache.geometry.main_map_water_keys) == 1
