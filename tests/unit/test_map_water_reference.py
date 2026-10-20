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


@pytest.mark.parametrize('pixel', [(477,400),(287,362),(257,597),(50,450),(840,553)])
def test_town_house_pointer_glow_ocean_and_dry_island_are_not_water(pixel):
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
