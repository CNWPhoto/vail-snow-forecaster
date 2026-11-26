# Vail pinpoint snow forecaster

Zone-level, terrain-aware snowfall forecasting for Vail Mountain: a map of new snow by slope, a 10-day outlook, base depth, and a public record of how every forecast turned out. A hobby project built on free public data only.

**Live map:** https://purposebuilt.studio (build 005), or directly at https://cnwphoto.github.io/vail-snow-forecaster/

## Disclaimer

This is an independent hobby project. It is **not affiliated with, endorsed by or connected to Vail Resorts or Vail Mountain**. It is not an official forecast, not the resort's snow report, and **not avalanche advice**; for avalanche conditions see the Colorado Avalanche Information Center. Forecasts can be wrong. Do not rely on this for safety or travel decisions. It is non-commercial.

## How it runs

Nothing needs to run at home. GitHub Actions (`.github/workflows/forecast.yml`) fetches the public model data, builds the forecast and publishes the files:

| Job | When (UTC) |
|---|---|
| Daily map, zones, wind loading, scorecard, base depth | 17:50 |
| 10-day outlook and base depth | 05:55, 11:55, 20:15, 23:55 |
| Evening alert | 04:45 |
| 5 am alert (what the gauge measured overnight) | 12:05 |

- `main` holds the code. `gh-pages` holds the published map and forecast files, as a single commit replaced on each run. `state` holds the forecast logs behind the scorecard.
- The fitted models and terrain the jobs need (about 110 MB) are the `runtime-v2` release asset. The 58 GB of model history used for training is not here; retraining is done on a computer that has it (`hrrr-fetch`, `baseline-fetch`, `outlook-fetch`, `european-fetch`, then `table`, `hindcast`, `train`, `outlook-train`), and a new runtime asset is uploaded.
- A failed run sends GitHub's usual failure email to the repository owner.

Start with `PROJECT_SUMMARY.md` for what the project is and how well it works. Work proceeded in phases with a written checkpoint after each:

| Phase | Status | Read |
|---|---|---|
| 0. Data inventory and feasibility | Done | `PHASE0_FEASIBILITY.md`, `DATA_SOURCES.md` |
| 1. Terrain grid and zones | Done, zones awaiting review | `PHASE1_SUMMARY.md` |
| 2. Historical ingest and storm catalogue | Done; dates corrected 2026-11-24 | `STORM_CLIMATOLOGY.md`, `PHASE2_SUMMARY.md` |
| 3. Model layers 1 to 4 with hindcast scores | Done, with gaps listed in the summary | `PHASE3_SUMMARY.md` |
| 4. Wind, settlement, uncertainty | Done; wind and sun are unverifiable | `PHASE4_SUMMARY.md` |
| 5. Map UI on live forecasts | Done; live at https://cnwphoto.github.io/vail-snow-forecaster/ | `PHASE5_SUMMARY.md` |
| 6. Scorecard, alerts and the other extras | Done; runs on GitHub Actions since 2026-11-24 (the Mac schedule was removed that day). Alerts go to the map banner and the `state` branch, not to a phone. Depth logger dropped by decision | `PHASE6_SUMMARY.md`, `ADDING_A_RESORT.md`, `TEN_DAY_OUTLOOK.md`, `GAUGE_TICKS.md` (corrected scores), `WIND_LOADING.md` |
| 7. Mountain state for the ski game | Built 2026-11-25; hourly on GitHub Actions once merged. What is on the ground now, cell by cell, on the game's own grid: fresh snow, what has been tracked out, wind, crust, weather now. Survival rates are priors, not measurements | `MOUNTAIN_STATE.md` |

## Setup

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv fetches it if missing).

```bash
uv sync
```

Terrain comes from the ski game at `~/Games/Copper Ski Resort Game`. To point elsewhere, copy `.env.example` to `.env` and edit it.

## Commands

```bash
uv run vailsnow terrain
```

```bash
uv run vailsnow catalog
```

```bash
uv run vailsnow upper-fetch
```

```bash
uv run vailsnow upper
```

```bash
uv run vailsnow climatology
```

```bash
uv run vailsnow hrrr-fetch
```

```bash
uv run vailsnow baseline-fetch
```

```bash
uv run vailsnow table
```

```bash
uv run vailsnow hindcast
```

```bash
uv run vailsnow train
```

```bash
uv run vailsnow forecast
```

```bash
python3 web/serve.py
```

Phase 6. `forecast` now also logs the forecast, scores the log against the gauge, ranks zones for first chair and rates lift wind holds. The rest:

```bash
uv run vailsnow score
```

```bash
uv run vailsnow alert evening --dry-run
```

```bash
uv run vailsnow alert morning --dry-run
```

```bash
uv run vailsnow alert-backtest --likely 4 --high 8
```

```bash
uv run vailsnow analogs 2020-02-06
```

```bash
uv run vailsnow wind-check
```

```bash
uv run vailsnow check-resort
```

Running it on a Mac instead of GitHub: four launchd jobs in `deploy/` do the same on a local schedule (forecast at 10:45 am, 10-day refresh at 4:55 and 10:55 am and pm, alerts at 9:45 pm and 5:05 am). They are not installed: the GitHub jobs replaced them on 2026-11-24. Do not run both. The forecast log of record is on the `state` branch of the repository; `data/log/` on a computer is a copy that stops growing once the local jobs are off.

```bash
uv run vailsnow status
```

```bash
sh deploy/install_schedule.sh
```

```bash
sh deploy/uninstall_schedule.sh
```

Disk use. The daily forecast job cleans up after itself: map forecasts older than 14 days are removed (a folder holding a `.keep` file stays), each day's SNODAS file is cut down to a 28 KB window, spent National Blend cycles go after 14 days, and job logs are trimmed. The forecast logs in `data/log/` are never touched.

```bash
uv run vailsnow disk
```

```bash
uv run vailsnow prune --dry-run
```

Base depth (an estimate from SNODAS tied to the SNOTEL depth sensor; refreshed with every forecast and outlook run):

```bash
uv run vailsnow depth
```

The 10-day outlook (see `TEN_DAY_OUTLOOK.md`). `forecast` refreshes it; `outlook` refreshes it alone, after any of the four daily ensemble runs:

```bash
uv run vailsnow outlook
```

```bash
uv run vailsnow outlook-fetch
```

```bash
uv run vailsnow outlook-train
```

```bash
uv run pytest
```

```bash
python3 -m http.server 8765 --directory web/check
```

## Layout

```
resorts/vail.toml      everything resort-specific: grid, bands, zones, stations, map view, alert and lift-hold thresholds
src/vailsnow/          the package
  resort.py            config loader
  fetch.py             cached downloads (nothing is fetched twice)
  terrain/             grid, derived layers, wind shelter, zones, build pipeline
  obs/                 SNOTEL and valley-observer loaders
  catalog/             snow events, flow regimes, NWS case library, climatology tables
  upper/               ERA5 download, storm upper-air features, daily flow direction
  grids/               PRISM normals and SNODAS snapshots
  fcst/                forecasts as issued: HRRR (chunked copy), NBM, NWS grids, GFS, GEFS, the European ensemble
  model/               the four layers, the training table, hindcasts and zone output
  verify/              scores
  live/                the daily forecast: newest runs in, map files out
  extras/              forecast log and scorecard, first-chair ranking, lift wind, alerts, analog finder, resort checklist
tests/                 unit tests, plus checks against the real build
web/check/             the Phase 1 review map
web/app/               the snow map (static page; reads web/app/data/<run>/)
deploy/                launchd jobs for running on a Mac instead of GitHub, with install and uninstall scripts
.github/workflows/     the GitHub Actions schedule that builds and publishes the forecast
data/                  generated outputs and the download cache (not committed). data/log/forecasts.jsonl is the forecast log: back it up
phase0/probes/         raw endpoint responses from the Phase 0 tests
```

## Ground rules

Free public data only, nothing paid, and no one else's model or code. Sources and what each asks for are listed in `PHASE0_FEASIBILITY.md`.

## Credits

Elevation and lidar: U.S. Geological Survey, 3D Elevation Program. Trails, lifts and boundary: © OpenStreetMap contributors (ODbL).
Snow observations: USDA NRCS SNOTEL; NWS COOP and CoCoRaHS (CC BY 4.0) via RCC-ACIS.
Reanalysis: ERA5, Copernicus Climate Change Service (CC BY 4.0), via the WeatherBench 2 public copy.
Precipitation normals: PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu (accessed 2026-11-23).
Snow model snapshots: SNODAS, NOAA NOHRSC, archived at NSIDC (G02158). Storm write-ups: NWS Grand Junction.
Forecasts: NOAA HRRR (via the University of Utah's chunked copy on AWS Open Data), GFS, GEFS, National Blend of Models and NDFD.
European ensemble: this forecast is based in part on data and products of the European Centre for Medium-Range Weather Forecasts (ECMWF), from ECMWF open data and the WeatherBench 2 public copy (CC BY 4.0). ECMWF has no part in this project and does not endorse it.
Ridge wind check: FAA/NWS airport observations via the Iowa Environmental Mesonet, Iowa State University.
Orographic precipitation theory: Smith and Barstad (2004), reimplemented from the paper.
