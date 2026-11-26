"""The mountain state: the hourly estimate of what is on the ground, published for the ski game."""
import json

import numpy as np
import pandas as pd
import pytest

from vailsnow.resort import load_resort
from vailsnow.state import engine, grid, publish

RATES = (0.04, 0.02, 0.0008)
SHAPE = (48, 64)


@pytest.fixture(scope="module")
def config() -> dict:
    return load_resort().state


@pytest.fixture(scope="module")
def ground(config) -> engine.Ground:
    """A made-up mountain: a north-south ridge down the middle, trees on the east half, one zone
    in the middle with a groomed strip, everything inside the boundary except the outer ring."""
    rng = np.random.default_rng(0)
    rows, cols = np.mgrid[0: SHAPE[0], 0: SHAPE[1]]
    ridge = np.abs(cols - SHAPE[1] / 2) * 16.0
    inside = (rows > 3) & (rows < SHAPE[0] - 4) & (cols > 3) & (cols < SHAPE[1] - 4)
    zone = np.where(inside & (rows < SHAPE[0] / 2), 1, np.where(inside, 9, 0)).astype(np.uint8)       # zone 1 Front Side, zone 9 Back Bowls
    sx = np.stack([np.where(cols > SHAPE[1] / 2, 12.0, -12.0) * np.cos(np.radians(d - 270.0)) for d in range(0, 360, 360 // 16)]).astype(np.float32)
    layers = {"elev": (3400.0 - ridge * 0.4).astype(np.float32), "slope": np.full(SHAPE, 22.0, np.float32),
              "aspect": np.where(cols > SHAPE[1] / 2, 90.0, 270.0).astype(np.float32),
              "canopy": np.where(cols > SHAPE[1] * 0.7, 0.8, 0.05).astype(np.float32), "tree_share": np.where(cols > SHAPE[1] * 0.7, 0.9, 0.0).astype(np.float32),
              "groomed": np.where((cols > 10) & (cols < 14), 1.0, 0.0).astype(np.float32), "run": np.zeros(SHAPE, np.float32), "glade_run": np.zeros(SHAPE, np.float32),
              "inside": inside, "d_run": (np.abs(cols - 12) * 16.0).astype(np.float32), "d_top": np.full(SHAPE, 400.0, np.float32),
              "ridge_distance": ridge.astype(np.float32), "height": np.clip(1.0 - ridge / ridge.max(), 0, 1).astype(np.float32),
              "zone": zone, "sx": sx, "lat": np.float32(39.6), "lon": np.float32(-106.35), "prism": rng.uniform(0.8, 1.2, SHAPE).astype(np.float32)}
    return engine.ground(layers, config, {1: "Front Side", 9: "Back Bowls"}, cell=16.0)


def weather(g: engine.Ground, time: pd.Timestamp, water_in: float = 0.0, ratio: float = 15.0, temp_c: float = -8.0, wind_ms: float = 0.0, clear: float = 0.0) -> engine.Forcing:
    water = (water_in * g.layers["prism"]).astype(np.float32)
    return engine.Forcing(time=time, snow=water * ratio, water=water, temp_c=np.full(g.shape, temp_c, np.float32), ridge_wind_ms=wind_ms, ridge_from=270.0, clear=clear)


def run(g: engine.Ground, config: dict, start: str, hours: list[dict], base_in: float = 40.0):
    """Step through a list of per-hour weather settings, yielding (before, forcing, after)."""
    s = engine.empty(pd.Timestamp(start), g.shape, g.history_block, base_in)
    for kw in hours:
        f = weather(g, s.time + pd.Timedelta(hours=1), **kw)
        n = engine.step(s, f, g, config, RATES)
        yield s, f, n
        s = n


def total_water(s: engine.State) -> float:
    return float(s.fresh_w.sum(dtype=np.float64) + s.base_w.sum(dtype=np.float64))


STORM = [dict(water_in=0.08, wind_ms=w) for w in (2, 6, 10, 14, 18, 22, 9, 4)] + [dict(wind_ms=w, clear=c) for w, c in ((16, 0.2), (20, 0.9), (3, 1.0))] * 12


def test_no_negative_depths(ground, config):
    for _, _, n in run(ground, config, "2027-01-12 06:00", STORM + [dict(temp_c=4.0, clear=1.0)] * 40, base_in=2.0):
        for name in engine.FLOAT_FIELDS:
            if name != "wind":
                assert float(getattr(n, name).min()) >= 0.0, name
        assert (n.open <= n.fresh + 1e-6).all() and (n.trees <= n.fresh + 1e-6).all()


def test_water_is_conserved_through_wind(ground, config):
    """Below freezing the only way water enters is snowfall, however hard the wind blows."""
    for s, f, n in run(ground, config, "2027-01-12 06:00", STORM):
        added = float(f.water.sum(dtype=np.float64))
        assert total_water(n) == pytest.approx(total_water(s) + added, rel=1e-6)
        assert n.melted == 0.0


def test_wind_moves_snow_without_making_any(ground, config):
    """A gale after a storm strips the windward side and loads the lee, and the books still balance."""
    steps = list(run(ground, config, "2027-01-12 06:00", [dict(water_in=0.1)] * 6 + [dict(wind_ms=22.0)] * 6))
    calm, blown = steps[5][2], steps[-1][2]
    assert float(blown.fresh_w.sum(dtype=np.float64)) == pytest.approx(float(calm.fresh_w.sum(dtype=np.float64)), rel=1e-6)
    west, east = (slice(None), slice(20, 30)), (slice(None), slice(34, 44))
    assert (blown.fresh_w[east].mean() - calm.fresh_w[east].mean()) > 0 > (blown.fresh_w[west].mean() - calm.fresh_w[west].mean())
    assert engine.wind_effect(blown)[west].mean() < 0 < engine.wind_effect(blown)[east].mean()


def test_melt_is_the_only_way_out(ground, config):
    steps = list(run(ground, config, "2027-03-20 06:00", [dict(water_in=0.1)] * 4 + [dict(temp_c=5.0)] * 30))
    start, end = steps[0][0], steps[-1][2]
    fell = sum(float(f.water.sum(dtype=np.float64)) for _, f, _ in steps)
    assert end.melted > 0
    assert total_water(end) + end.melted == pytest.approx(total_water(start) + fell, rel=1e-6)


def test_fresh_snow_only_decreases_when_nothing_is_falling(ground, config):
    """Dry and calm: untracked snow, and the fresh layer it is part of, never grow, whatever the hour or the sun."""
    dry = [dict(water_in=0.1)] * 8 + [dict(clear=c, temp_c=t) for c, t in ((0.0, -10.0), (1.0, -3.0), (0.6, 1.0))] * 60
    for i, (s, f, n) in enumerate(run(ground, config, "2027-01-12 06:00", dry)):
        if i >= 8:
            for name in ("fresh", "open", "trees"):
                assert (getattr(n, name) <= getattr(s, name) + 1e-6).all(), (i, name)
            assert (engine.untracked(n, ground) <= engine.untracked(s, ground) + 1e-6).all()


def test_closed_terrain_keeps_its_snow_and_groomers_lose_theirs_first(ground, config):
    """Tuesday 2026-12-01: the Front Side is open, the Back Bowls (zone 9) are not until the 11th."""
    hours = [dict(water_in=0.1)] * 6 + [dict()] * 30                    # snow overnight, then a full lift day
    *_, (_, _, end) = run(ground, config, "2026-12-01 02:00", hours)
    L = ground.layers
    front, back = L["zone"] == 1, L["zone"] == 9
    t_open, t_trees = engine.tracked(end)
    assert t_open[back].max() < 0.01                                    # closed: untouched
    groomed, bowl, trees = front & (L["groomed"] > 0.5), front & (L["groomed"] == 0) & (L["tree_share"] == 0), front & (L["tree_share"] > 0.5)
    assert t_open[groomed].mean() > t_open[bowl].mean() > t_trees[trees].mean()
    assert t_open[groomed].mean() > 0.95 and t_trees[trees].mean() < 0.5


def test_lift_hours_and_crowds(config):
    quiet = engine.lifts(config, pd.Timestamp("2026-12-01 18:00"))      # Tuesday 11 am in Denver
    assert quiet.share == 1.0 and quiet.crowd == 1.0
    assert engine.lifts(config, pd.Timestamp("2026-12-01 16:00")).share == 0.0          # 8 to 9 am: before the early-season 9:00 start
    assert engine.lifts(config, pd.Timestamp("2026-12-01 23:00")).share == pytest.approx(0.5)   # 3 to 4 pm: lifts stop at 3:30
    assert engine.lifts(config, pd.Timestamp("2026-12-05 18:00")).crowd == config["operations"]["weekend"]
    assert engine.lifts(config, pd.Timestamp("2026-12-25 18:00")).crowd == config["operations"]["holiday"]
    assert engine.lifts(config, pd.Timestamp("2026-12-15 16:00")).share == 0.5          # extended hours: 8:30 start
    assert engine.lifts(config, pd.Timestamp("2026-10-20 18:00")).share == 0.0          # before the season
    assert engine.lifts(config, pd.Timestamp("2026-12-01 12:00")).groom                 # the 4 am hour


def test_crust_forms_and_new_snow_buries_it(ground, config):
    thaw = [dict(water_in=0.1)] * 4 + [dict(temp_c=4.0)] * 6 + [dict(temp_c=-6.0)] * 3
    *_, (_, _, crusted) = run(ground, config, "2027-03-01 08:00", thaw)
    assert (crusted.crust == engine.CRUST_MELT).all()
    s = crusted
    for _ in range(6):
        s = engine.step(s, weather(ground, s.time + pd.Timedelta(hours=1), water_in=0.05), ground, config, RATES)
    assert (s.crust == engine.CRUST_NONE).all()


def test_new_snow_totals_roll_off(ground, config):
    hours = [dict(water_in=0.1)] * 5 + [dict()] * 80
    states = [n for _, _, n in run(ground, config, "2027-01-12 06:00", hours)]
    fell = engine.new_snow(states[4], 24).mean()
    assert fell > 5.0
    assert engine.new_snow(states[20], 24).mean() == pytest.approx(fell, rel=1e-3)
    assert engine.new_snow(states[30], 24).mean() == 0.0
    assert engine.new_snow(states[30], 72).mean() == pytest.approx(fell, rel=1e-3)
    assert engine.new_snow(states[80], 72).mean() == 0.0 and engine.new_snow(states[80], 168).mean() == pytest.approx(fell, rel=1e-3)


def test_state_survives_a_save(ground, config, tmp_path):
    *_, (_, _, s) = run(ground, config, "2027-01-12 06:00", STORM[:14])
    engine.save(s, tmp_path / "carry.npz")
    back = engine.load(tmp_path / "carry.npz")
    assert back.time == s.time and back.last_groomed == s.last_groomed
    assert np.array_equal(back.history, s.history) and np.array_equal(back.crust, s.crust)
    assert np.allclose(back.fresh, s.fresh, rtol=2e-3, atol=1e-3)       # kept as half-precision floats


def test_sun_rises_in_the_east_at_about_the_right_time():
    rise, sset = engine.sun_times(pd.Timestamp("2026-12-21"), 39.6, -106.35)
    assert 14.2 < rise.hour + rise.minute / 60 < 14.6                   # about 7:22 am Mountain Standard Time
    assert 23.5 < sset.hour + sset.minute / 60 < 24.0                   # about 4:42 pm
    elev, az = engine.sun_position(pd.Timestamp("2026-12-21 19:00"), 39.6, -106.35)
    assert 25 < elev < 28 and 170 < az < 190                            # low in the south at local noon


def test_textures_are_rgb_and_decode_to_what_was_written(ground, config, tmp_path):
    *_, (_, _, s) = run(ground, config, "2027-01-12 06:00", STORM[:14])
    from PIL import Image
    sizes = publish.textures(s, tmp_path)
    assert set(sizes) == {"surface", "character", "history"}
    surface = Image.open(tmp_path / f"surface.{publish.EXT}")
    assert surface.mode == "RGB" and surface.size == (SHAPE[1], SHAPE[0])       # no alpha channel
    a = np.asarray(surface).astype(float)
    assert np.allclose(a[..., 0] / 255 * publish.FRESH_MAX_IN, np.clip(s.fresh, 0, publish.FRESH_MAX_IN), atol=0.21)
    assert np.allclose(a[..., 1] / 255, engine.tracked(s)[0], atol=0.034)
    wind = np.asarray(Image.open(tmp_path / f"character.{publish.EXT}")).astype(float)[..., 1]
    assert np.allclose((wind - 128) / publish.WIND_SPAN, engine.wind_effect(s), atol=0.075)


def test_published_state_never_speaks_of_bare_ground(ground, config, tmp_path):
    """The game keeps a full base everywhere: total depth and anything like it stay out of what it reads."""
    resort = load_resort()
    *_, (_, _, s) = run(ground, config, "2027-01-12 06:00", STORM[:14], base_in=0.0)
    info = {"time": s.time, "snow_in_hr": 1.2, "rain_in_hr": 0.0, "temp_c": {"mid": -8.0, "ridge": -11.0, "base": -4.0}, "temp_measured": True,
            "ridge": {"speed_ms": 9.0, "from_deg": 250.0, "gust_ms": 15.0}, "mid": {"speed_ms": 4.0, "from_deg": 260.0, "gust_ms": 8.0},
            "cloud": 1.0, "vis_m": 800.0, "model_run": s.time - pd.Timedelta(hours=2), "model_lead_h": 2, "sources": {"water": "gauge", "temp": "x", "wind": "y"}}
    block = publish.weather_block(info, info, resort, ground, s.time + pd.Timedelta(minutes=20), s.time)
    doc = publish.state_json(s, ground, resort, block, "live", s.time, {})
    text = json.dumps(doc).lower()
    for word in ("base_in", "depth_in", "total_depth", "bare", "thin cover") :
        assert word not in text.replace("bare ground, thin cover or closed terrain", "")
    assert block["snowfall"]["class"] == "heavy" and block["wind"]["ridge"]["from"] == "WSW" and block["visibility_mi"]["now"] == 0.5
    assert block["snowfall"]["stale_min"] == 20 and block["wind"]["stale_min"] == 140


@pytest.mark.data
def test_grid_is_the_games_box():
    resort = load_resort()
    s = grid.spec(resort)
    assert (s["width"], s["height"], s["cell_m"]) == (960, 656, 16.0)
    assert (s["west"], s["north"], s["east"], s["south"]) == (376200.0, 4390000.0, 391560.0, 4379504.0)
    layers = grid.load(resort)
    assert layers["elev"].shape == (656, 960) and layers["sx"].shape == (16, 656, 960)
    r, c = grid.gauge_cell(resort)
    assert abs(float(layers["elev"][r, c]) - 10290 / 3.28084) < 15.0 and float(layers["prism"][r, c]) == pytest.approx(1.0, abs=0.03)
