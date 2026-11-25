import numpy as np
import pytest

from vailsnow.resort import load_resort
from vailsnow.terrain import zones
from vailsnow.terrain.grid import Grid, block_mean


def test_block_mean_averages_and_drops_ragged_edge():
    a = np.arange(7 * 11, dtype=np.float32).reshape(7, 11)
    b = block_mean(a, 5)
    assert b.shape == (1, 2)
    assert b[0, 0] == pytest.approx(a[:5, :5].mean())


def test_grid_core_geometry():
    g = Grid(np.zeros((30, 40), dtype=np.float32), cell=10.0, west=1000.0, north=5000.0, pad=5, epsg=26913)
    assert g.core_shape == (20, 30)
    assert g.core_bounds() == (1050.0, 4750.0, 1350.0, 4950.0)
    r, c = g.core_rc(1055.0, 4945.0)
    assert (int(r), int(c)) == (0, 0)
    r, c = g.core_rc(1345.0, 4755.0)
    assert (int(r), int(c)) == (19, 29)


def test_zones_grow_to_the_nearest_seed():
    seeds = np.zeros((20, 40), dtype=np.int16)
    seeds[10, 5], seeds[10, 34] = 1, 2
    mask = np.ones_like(seeds, dtype=bool)
    out = zones.grow_zones(seeds, mask, np.zeros_like(mask), 10.0)
    assert (out[:, :19] == 1).all() and (out[:, 21:] == 2).all()


def test_a_ridge_pulls_the_zone_edge_onto_itself():
    seeds = np.zeros((20, 40), dtype=np.int16)
    seeds[10, 5], seeds[10, 34] = 1, 2
    mask = np.ones_like(seeds, dtype=bool)
    ridge = np.zeros_like(mask)
    ridge[:, 12] = True                       # a crest much closer to seed 1
    out = zones.grow_zones(seeds, mask, ridge, 10.0, barrier_cost=25.0)
    assert (out[:, :12] == 1).all() and (out[:, 13:] == 2).all()


def test_cells_outside_the_mask_get_no_zone():
    seeds = np.zeros((10, 10), dtype=np.int16)
    seeds[5, 5] = 3
    mask = np.zeros_like(seeds, dtype=bool)
    mask[3:8, 3:8] = True
    out = zones.grow_zones(seeds, mask, np.zeros_like(mask), 10.0)
    assert (out[mask] == 3).all() and (out[~mask] == 0).all()


def test_bands_split_on_the_configured_elevations():
    resort = load_resort()
    ft = np.array([[8500.0, 9000.0, 9999.0, 10000.0, 10999.0, 11500.0]]) / 3.280839895
    assert zones.band_raster(resort, ft).tolist() == [[1, 2, 2, 3, 3, 4]]


def test_aspect_shares_and_acres():
    n = 100
    layers = {
        "elevation": np.full((n, n), 3000.0, dtype=np.float32),
        "slope": np.full((n, n), 20.0, dtype=np.float32),
        "aspect": np.tile(np.array([350.0, 10.0, 100.0, 190.0], dtype=np.float32).repeat(25), (n, 1)),
        "canopy_cover": np.full((n, n), 0.5, dtype=np.float32),
        "sky_view": np.ones((n, n), dtype=np.float32),
        "sx_nw": np.tile(np.array([-5.0, 5.0], dtype=np.float32).repeat(50), (n, 1)),
    }
    st = zones.cell_stats(np.ones((n, n), dtype=bool), layers, 10.0)
    assert st["aspect_share"] == {"north": 0.5, "east": 0.25, "south": 0.25, "west": 0.0}
    assert st["acres"] == pytest.approx(n * n * 100 / 4046.8564, abs=0.1)
    assert st["sx_nw_exposed_share"] == 0.5


def test_resort_config_is_consistent():
    resort = load_resort()
    assert len(resort.zones) == 17
    assert [b.id for b in resort.bands] == ["base", "lower", "upper", "top"]
    assert {z.group for z in resort.zones} == {"Front Side", "Back Bowls", "Blue Sky Basin"}
