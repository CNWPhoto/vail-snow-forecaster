# Adding a resort

Status: the code reads everything resort-specific from one file, but **only Vail has been built**. Nothing below has been run for a second mountain. The checklist command tells you where a new resort stands at each step.

```bash
uv run vailsnow check-resort beaver_creek
```

## What you supply

One file, `resorts/<id>.toml`, with the same keys as `resorts/vail.toml`:

| Section | What it holds |
|---|---|
| top level | `id`, `name`, map projection (`epsg`), and the ski-area name in OpenStreetMap |
| `[region]`, `[fine]` | the box for regional work and the fine grid |
| `[sources]` | paths to the terrain inputs: a height grid, a tree-canopy grid, a regional elevation model and an OpenStreetMap extract |
| `[[bands]]` | elevation bands |
| `[[zones]]` | each zone's name, lifts and the trails that seed it |
| `[[snotel]]` | SNOTEL sites; mark one `target = true` (the gauge the forecast is fitted to) |
| `[[valley]]` | daily valley observers (COOP or CoCoRaHS ids) |
| `[view]` | map centre, zoom, headline zone, and which valley site to quote |
| `[alerts]`, `[lift_wind]`, `[wind_check]` | alert thresholds, lift-hold thresholds, and a ridge-level airport station for the gust check |

## Keeping two resorts apart

Built tables, models and map files live in one folder per resort. For a second resort, set three variables (in the shell or `.env`); the download cache is shared.

```bash
export VAILSNOW_RESORT=beaver_creek VAILSNOW_DATA_DIR=data-beaver_creek VAILSNOW_WEB_DATA_DIR=web/app/data-beaver_creek
```

## Steps, in order

1. `uv run vailsnow terrain`, then review the zones on the check map.
2. `uv run vailsnow catalog`, `upper-fetch`, `upper`, `climatology`.
3. `uv run vailsnow hrrr-fetch`, `baseline-fetch`, `table`, `hindcast`, `train`.
4. `uv run vailsnow wind-check`, then `forecast` and `score`.

## What is not ready for a second resort

- **Terrain inputs.** Vail's height and canopy grids come from the ski game's lidar. Copper is in that project; Beaver Creek is not. For a mountain without them, the 10 m USGS elevation model would do for heights (`fetch.fetch_3dep` already downloads it), but there is no reader for a public tree-canopy grid yet.
- **OpenStreetMap extract.** The lifts, trails and boundary for Vail were downloaded by the ski game. A new resort needs its own extract in the same format.
- **The HRRR window.** Stored forecasts cover about 126 by 156 km around Vail. Beaver Creek and Copper are inside it; a resort outside it needs the window widened in `fcst/hrrr.py` and every run fetched again.
- **A different target gauge means a new model.** Steps 3 and 4 refit everything to the new gauge. Expect about the same compute as Vail took. Beaver Creek's gauges sit at 8,530 and 9,500 ft, well below most of its terrain, which will make its mid-mountain numbers weaker than Vail's.
- **The map page** reads its centre and headline zone from the forecast files, but `web/app/static/` (zone outlines, lifts, trails) is one resort's. A second resort needs its own copy of the page folder or a small change to load those per resort.
