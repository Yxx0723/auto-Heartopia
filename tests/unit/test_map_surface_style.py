"""World-space textures retain geometry and follow the map projection."""
import pytest

from tests.unit.test_map_draw_cache import Canvas
from utils.core.coordinate_navigation import inside
from utils.core.map_water_reference import GRASSLAND_POND
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from utils.ui.map_presentation import draw_map_texture, draw_terrain_tints, texture_strokes


def test_water_texture_stays_inside_verified_water():
    coords = tuple(value for point in GRASSLAND_POND for value in point)
    strokes = texture_strokes(coords, 4, 1.5)
    assert 1 <= len(strokes) <= 10
    for x, z, end, _ in strokes:
        for fraction in (0, .25, .5, .75, 1):
            assert inside((x + (end-x)*fraction, z), GRASSLAND_POND)


def test_texture_cache_reprojects_and_hides_with_scene():
    canvas = Canvas()
    cache = MapDrawCache(canvas)
    coords = tuple(value for point in GRASSLAND_POND for value in point)
    key = ('pond', 'texture', False)
    for viewport in (Viewport(800, 500, -185, -18, 2), Viewport(950, 620, -183, -15, 8)):
        cache.begin(viewport)
        draw_map_texture(cache, ('pond',), coords, -1.5, '#78AEB2', 4, 1.5)
        cache.finish()
        x, z, end, _ = texture_strokes(coords, 4, 1.5)[0]
        item, _ = cache.fixed[key].commands[0]
        assert canvas.coords(item) == pytest.approx((*viewport.screen(x,z), *viewport.screen(end,z)))
    cache.begin(viewport)
    draw_terrain_tints(cache, False)
    cache.finish()
    assert not cache.fixed[key].visible
