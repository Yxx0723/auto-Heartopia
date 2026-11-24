"""Height classification is display-only: these tests pin the measured bands."""
import math

import pytest

from utils.core import thresholds as policy
from utils.core.terrain_height import (
    DEEP_LOW, LOW_HIGH, TERRAIN_EDGES, class_edges, coverage, edge_touches_band,
    edges_in_band, facing_light, height_band, shade, wall_class, wall_normal)
from utils.ui.map_height_layers import band_chains, slice_state

MAIN_BAND = (-12.781, 48.094)


def test_edges_are_the_verified_reference():
    assert len(TERRAIN_EDGES) == 3398
    assert len({(e[4], e[5]) for e in TERRAIN_EDGES}) == 73


@pytest.mark.parametrize('low, high, expected', [
    (-30.0, 48.0, 'deep'),      # below the deep base line wins
    (-12.781, 48.094, 'main'),  # the 87% main wall band
    (-12.0, 10.45, 'low'),      # tops out below the kerb line
    (DEEP_LOW, 40.0, 'main'),   # boundary belongs to main, not deep
    (-12.0, LOW_HIGH, 'main'),  # boundary belongs to main, not low
])
def test_wall_class_boundaries(low, high, expected):
    assert wall_class((0., 0., 1., 0., low, high)) == expected


def test_class_edges_partition_every_edge():
    groups = class_edges()
    assert set(groups) == {'deep', 'main', 'low'}
    assert sum(len(v) for v in groups.values()) == len(TERRAIN_EDGES)
    assert len(groups['main']) == 2956
    assert len(groups['deep']) == 411
    assert len(groups['low']) == 31


def test_wall_normal_is_unit_and_perpendicular():
    for edge in TERRAIN_EDGES[:64]:
        nx, nz = wall_normal(edge)
        assert math.isclose(math.hypot(nx, nz), 1.0, rel_tol=1e-9)
        dx, dz = edge[2] - edge[0], edge[3] - edge[1]
        assert math.isclose(nx * dx + nz * dz, 0.0, abs_tol=1e-6)


def test_degenerate_edge_has_no_normal():
    assert wall_normal((5., 5., 5., 5., 0., 1.)) == (0., 0.)


def test_facing_light_stays_in_range():
    values = [facing_light(e) for e in TERRAIN_EDGES[:400]]
    assert min(values) >= 0.35 - 1e-9
    assert max(values) <= 1.0 + 1e-9


def test_shade_clamps_and_keeps_short_colours():
    assert shade('#808080', 1.0) == '#808080'
    assert shade('#FFFFFF', 2.0) == '#ffffff'
    assert shade('#FFFFFF', 0.0) == '#000000'
    assert shade('red', 0.5) == 'red'


def test_height_band_uses_the_nav_refresh_policy():
    low, high = height_band(10.0)
    half = policy.COORD_NAV_HEIGHT_REFRESH * 2
    assert math.isclose(low, 10.0 - half)
    assert math.isclose(high, 10.0 + half)


@pytest.mark.parametrize('bad', [None, '1', float('nan'), float('inf')])
def test_height_band_rejects_unusable_anchor(bad):
    with pytest.raises(ValueError):
        height_band(bad)


def test_edge_touches_band_is_inclusive():
    edge = (0., 0., 1., 0., MAIN_BAND[0], MAIN_BAND[1])
    assert edge_touches_band(edge, (MAIN_BAND[1], MAIN_BAND[1] + 1))
    assert edge_touches_band(edge, (MAIN_BAND[0] - 1, MAIN_BAND[0]))
    assert not edge_touches_band(edge, (MAIN_BAND[1] + 1, MAIN_BAND[1] + 2))


def test_edges_in_band_returns_pairs_with_classes():
    pairs = edges_in_band(17.5)
    assert pairs
    assert all(len(pair) == 2 for pair in pairs)
    assert {name for _, name in pairs} <= {'deep', 'main', 'low'}


def test_coverage_never_claims_ground_elevation():
    report = coverage()
    assert report['edges'] == 3398
    assert report['ground_elevation'] is False
    assert report['ground_faces'] == 0
    assert math.isclose(sum(c['share'] for c in report['classes'].values()), 1.0)


def test_slice_state_reports_wall_position_only():
    band = (0.0, 2.0)
    assert slice_state((0., 0., 1., 0., 5., 9.), band) == 'above'
    assert slice_state((0., 0., 1., 0., -5., -1.), band) == 'below'
    assert slice_state((0., 0., 1., 0., -5., 5.), band) == 'across'


def test_band_chains_never_invent_points():
    known = {(round(e[0], 6), round(e[1], 6)) for e in TERRAIN_EDGES}
    known |= {(round(e[2], 6), round(e[3], 6)) for e in TERRAIN_EDGES}
    for _key, coords in band_chains(None, True):
        for x, z in zip(coords[::2], coords[1::2]):
            assert (round(x, 6), round(z, 6)) in known


def test_band_chains_preserve_every_segment():
    segments = sum(len(coords) // 2 - 1 for _key, coords in band_chains(None, True))
    assert segments == len(TERRAIN_EDGES)


def test_band_chains_collapse_to_a_baseline_sized_layer():
    chains = band_chains(None, True)
    # MapDrawCache.finish walks every entry per frame, so the layer has to stay
    # close to the M3 baseline item count rather than one item per edge.
    assert len(chains) < 150


def test_band_chains_are_cached_per_anchor():
    band_chains.cache_clear()
    first = band_chains(None, True)
    assert band_chains(None, True) is first
    assert band_chains(17.5, True) is not first
    band_chains.cache_clear()
