"""Registered water completeness, dry islands and retained zoom detail."""
import math

import pytest

from utils.core.coordinate_navigation import inside
from utils.core.map_water_reference import MAP_WATER_AREAS, MAP_WATER_ISLANDS
from utils.core.water_display_reference import (
    DISPLAY_WATER_AREAS, DISPLAY_WATER_ISLANDS, DISPLAY_WATER_DETAIL,
    DISPLAY_WATER_AREAS_LODS, DISPLAY_WATER_ISLANDS_LODS)
from utils.ui.map_draw_cache import MapDrawCache,Viewport
from tests.unit.test_map_draw_cache import Canvas


def world(pixel):
    return (pixel[0]-511)/1.55,(452-pixel[1])/1.55


def wet(pixel,areas,islands):
    point=world(pixel)
    return any(inside(point,p) for p in areas) and not any(inside(point,p) for p in islands)


@pytest.mark.parametrize('level',[0,1,2])
@pytest.mark.parametrize('pixel',[(325,270),(333,269),(340,270),(640,300),(352,625),(218,470)])
def test_river_and_icon_occluded_lake_remain_wet_at_every_zoom(level,pixel):
    assert wet(pixel,DISPLAY_WATER_AREAS_LODS[level],DISPLAY_WATER_ISLANDS_LODS[level])


@pytest.mark.parametrize('level',[0,1,2])
@pytest.mark.parametrize('pixel',[(477,400),(287,362),(257,597),(50,450),(840,553)])
def test_land_icons_and_known_island_are_never_painted_as_water(level,pixel):
    assert not wet(pixel,DISPLAY_WATER_AREAS_LODS[level],DISPLAY_WATER_ISLANDS_LODS[level])


def test_new_source_completes_the_lake_under_the_old_bus_icon():
    assert not wet((218,470),MAP_WATER_AREAS,MAP_WATER_ISLANDS)
    assert wet((218,470),DISPLAY_WATER_AREAS,DISPLAY_WATER_ISLANDS)


def test_lods_keep_region_count_and_reduce_far_geometry_without_runtime_simplification():
    for key in ('water_lods','island_lods','deep_lods','deep_island_lods'):
        groups=DISPLAY_WATER_DETAIL[key]
        assert len(groups)==3 and all(len(g)==len(groups[0]) for g in groups)
        assert all(len(p)>=3 and all(math.isfinite(v) for point in p for v in point) for g in groups for p in g)
    assert sum(map(len,DISPLAY_WATER_AREAS_LODS[2])) < .4*sum(map(len,DISPLAY_WATER_AREAS_LODS[0]))


def test_hidden_zoom_levels_are_reused_and_all_water_layers_hide_together():
    canvas=Canvas()
    cache=MapDrawCache(canvas,DISPLAY_WATER_AREAS,water_islands=DISPLAY_WATER_ISLANDS,water_detail=DISPLAY_WATER_DETAIL)
    def frame(scale,show=True):
        viewport=Viewport(1800,1400,0.,30.,scale)
        cache.begin(viewport)
        if show:
            for kind in ('water','water-deep','water-deep-island','water-island'):
                entries=cache.geometry.visible_water_layer(kind,viewport)
                assert entries is cache.geometry.visible_water_layer(kind,viewport)
                for key,coords,_ in entries:cache.polygon(key,coords,-4,fill='blue',tags=kind)
        cache.finish()
    for scale in (3.,1.,.5):frame(scale)
    created=canvas.created
    for scale in (3.,1.,.5,1.,3.):frame(scale)
    assert canvas.created==created
    frame(3.,False)
    assert all(item['state']=='hidden' for item in canvas.items.values())
    frame(3.)
    assert canvas.created==created and cache.visible_tags


def test_water_roles_resolve_for_the_v2_and_current_palettes():
    from utils.ui.map_presentation import MAP_STYLE, water_colors, shade
    v2 = {'ground': '#17251f', 'water': '#183e4b', 'shore': '#47929a', 'shore_outer': '#254f52'}
    colors = water_colors(v2)
    assert colors['shallow'] == v2['water'] and colors['edge'] == v2['shore']
    assert colors['deep'] == shade(v2['water'], .72)
    assert colors['bank'] == shade(v2['water'], .6)
    current = water_colors(MAP_STYLE)
    assert current['shallow'] == MAP_STYLE['water']
    assert current['deep'] == shade(MAP_STYLE['water'], .72)
    assert all(value.startswith('#') and len(value) == 7 for value in colors.values())


def test_deep_layer_uses_a_coarser_contour_and_rounds_only_its_own_corners():
    from utils.ui.map_presentation import smooth_coords
    cache = MapDrawCache(Canvas(), DISPLAY_WATER_AREAS, water_islands=DISPLAY_WATER_ISLANDS,
                         water_detail=DISPLAY_WATER_DETAIL)
    viewport = Viewport(1800, 1400, 0., 30., 1.)
    fine = {entry[0][1]: len(entry[1]) for entry in cache.geometry.visible_water_layer('water-deep', viewport)}
    coarse = {entry[0][1]: len(entry[1]) for entry in cache.geometry.visible_water_layer('water-deep', viewport, 1)}
    assert fine is not coarse and set(fine) == set(coarse)
    # 逐个多边形的顶点数并不保证单调（极小轮廓会退回细档），但总量必须明显下降。
    assert sum(coarse.values()) <= sum(fine.values()) * .8
    rounded = smooth_coords((0., 0., 10., 0., 10., 10., 0., 10.), 1)
    assert len(rounded) == 16 and min(rounded) >= 0. and max(rounded) <= 10.
