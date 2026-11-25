# Phase 5: the map, on live forecasts

Date: 2026-11-24. Checkpoint for review. The map runs on this machine. It is not deployed and not on a schedule; both need your go-ahead and your Cloudflare account.

## How to see it

```bash
uv run vailsnow forecast
```

```bash
python3 web/serve.py
```

Then open `http://localhost:8766`. Two forecasts are on disk: today's (Tue Nov 24, 5 am run) and a replay of Feb 14, 2025 for something with snow in it (`http://localhost:8766/?run=20250214`).

## What it does

One command pulls the newest 12z HRRR, GFS and GEFS runs, runs every layer from Phases 3 and 4 on a 30 m grid, and writes about 3 MB of static files. It takes 18 seconds.

| Brief | Status |
|---|---|
| MapLibre over hillshaded terrain, 3D toggle | Done. USGS relief and topo, USGS imagery for satellite, 3D from open terrain tiles |
| Blue contour bands in the eight bins, thin labelled lines, semi-transparent, legible on light and satellite | Done, checked on both. The ramp is one hue with lightness falling at every step, validated by script |
| Lifts, trails, zone outlines; zone cards with likely total and range | Done. Cards sort by amount and zoom to the zone when tapped |
| Next 24, 48, 72 h | Done |
| Full storm total | **Partly.** The 72 h total stands in for it; a storm that runs longer is cut off |
| 7-day outlook | **Partly.** Days 4 to 7 appear in the Why tab as raw ensemble numbers, labelled unverified. Today only days 4 and 5 were available when the forecast was built |
| Hourly scrubber | **Six-hourly**, 12 frames with play. Not hourly |
| Layers: as fallen, after wind, settled, density | Done. After wind is tagged "modelled, not verified" in the legend |
| Confidence layer | **Different from the brief.** Confidence does not vary across the mountain in this model, so shading it would show nothing. It is shown as Low end, Likely and High end views of the map, and as the range on every card |
| Click anywhere for a point forecast with an hourly chart | Done inside the ski area. The hourly shape is the same everywhere on the hill |
| Plain-English "why" panel with analog storms | Done: wind, temperatures, snow level when it matters, snow quality, moisture, wind effect, and the three most similar past forecast days with what the gauge then measured |
| Works on a phone | Done: bottom sheet with three heights, controls scroll sideways. Checked at 375 x 812 |

A fourth tab, Trust, states what is checked against instruments and what is only modelled, using the scores from Phases 3 and 4.

## Two changes the live forecast forced

**"Likely" is now the median, not the average.** Today's forecast is nearly dry: the median says no snow, the average says 0.08 in of water, and at 19 to 1 the average became "1.8 in likely" on every card. That was wrong on its face. Cards now show the median, so today reads 0 in likely with a high end of 5 in at Mid-Vail.

**Multi-day ranges are no longer the daily ranges added up.** Adding them held 92% of outcomes on held-out three-day totals, far wider than the 80% it claims. A blend tested on the same data holds 81%. For the Feb 14, 2025 replay that took Mid-Vail's 72 h range from 3.6 to 34 in down to 8 to 29 in around a likely 18 in.

## What I checked, and how

- Loaded both forecasts in the built-in browser at desktop and phone size. No console errors.
- Exercised every control: the three windows, four layers, low and high end, the scrubber, a tap for a point forecast, satellite, 3D, and the panel heights on a phone.
- The point lookup was checked against a known location (the SNOTEL site).
- 146 tests pass, including one that checks every published forecast's files for completeness and ordered ranges.

One thing to know: in the preview pane the map itself takes 10 to 18 seconds to appear at phone size. The panel with all the numbers appears at once, because it no longer waits for the map. I could not tell whether that delay is the pane or would also happen on a real phone.

## The replay as a reality check

The Feb 14, 2025 forecast called for 0.43 in of water on day one, range 0.09 to 0.87. The gauge measured 1.0 in. That is above the 90th percentile and is the big-day weakness from Phases 3 and 4 showing up in the first storm replayed. The map now says so in the Trust tab.

## Not done

- **Deployment.** `deploy/` holds a Worker, a `wrangler.toml`, an R2 upload script and a launchd job. They were written from memory of the Cloudflare and launchd documentation and **have never been run**. Bucket and worker names are placeholders.
- **The 5 am issue time.** The public HRRR copy the pipeline reads appears about 4.5 hours after the run, so a forecast stamped 5 am is ready about 10 am. An evening update from the 00z run is not built.
- **Contours outside the ski area.** The forecast grid is the mapped ski terrain only.
- **Label fonts** come from MapLibre's demo server, which makes no service promise.
- **Day 3** rests on GFS alone and borrows its temperature profile from day 2.
- **The Phase 1 zone draft is still unreviewed.** Every card and outline on the map uses it as drawn. This is now the most visible unreviewed thing in the project.

## Built

| Piece | Where |
|---|---|
| Final models fitted on all eight seasons, analog library | `model/final.py`, `uv run vailsnow train` |
| Live pipeline | `live/forecast.py`, `uv run vailsnow forecast` (`--run 2025-02-14` replays a past day) |
| 30 m forecast grid and contour writer | `live/grid.py`, `live/contours.py` |
| The map | `web/app/index.html`, `app.css`, `app.js` |
| Deployment and scheduling files, untested | `deploy/` |
| Tests | 146 pass |

## For Phase 6

1. **Decide on deployment first.** A public page changes what can be shown: nothing from Vail's snow report or cameras, and any scorecard must score against SNOTEL.
2. **Scorecard:** the pieces exist. Each morning's forecast files and the gauge are enough to publish forecast against observed.
3. **Powder alerts** should key on the 75th percentile and the upside flag, not the median, given the big-day weakness.
4. **Where to ski first** can rank zones by amount, density and wind exposure today, with the caveat that zone differences are mostly elevation.
