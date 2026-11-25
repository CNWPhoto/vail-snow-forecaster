# Phase 1: terrain grid and zones

Date: 2026-11-21. Checkpoint for review. No forecast or model code exists yet.

## What to do

Open the check map and tell me where the zones are wrong:

```bash
uv run vailsnow terrain
```

```bash
python3 -m http.server 8765 --directory web/check
```

Then open `http://localhost:8765`. Click a zone for its numbers, click a trail to see which zone it landed in, and switch terrain layers on the left.

## What was built

| Piece | Where | Notes |
|---|---|---|
| 10 m terrain grid | `data/terrain/vail/terrain_10m.tif` | 1,536 x 1,049 cells from your ski game's 2 m lidar grid. Ten layers: elevation, slope, aspect, curvature, position index at 150 m and 500 m, distance to ridgeline, sky view, canopy cover, canopy height |
| Wind shelter | `data/terrain/vail/sx_300m.tif` | Winstral maximum upwind slope for 16 wind directions, 300 m reach |
| Regional DEM | `data/cache/3dep/vail_region_30m.tif` | 30 m USGS terrain for the full box in the brief, fetched once and cached. Also pads the fine grid by 3 km so edge cells see real terrain |
| Zones | `zones.geojson`, `zone_bands.geojson`, `zones_10m.tif`, `zone_stats.json` | 17 zones, each split into four elevation bands |
| Zone definitions | `resorts/vail.toml` | The only place zones are defined. Editing a trail list and rebuilding moves the boundary |
| Check map | `web/check/index.html` | MapLibre, works on a phone |
| Tests | `tests/` | 36 pass: 27 on the terrain and zone maths, 9 against the real build |

The whole build takes about 15 seconds. It reads the game's files in place and does not change them.

## Checks against known facts

| Check | Expected | Got |
|---|---|---|
| Elevation at the SNOTEL site | 10,290 ft | 10,289 ft |
| Elevation at Patrol HQ | 11,248 ft | 11,234 ft |
| Elevation at the town COOP station | 8,297 ft | 8,260 ft |
| Front Side faces mostly north | yes | 61% north |
| Back Bowls wrap east, south and west | yes | 3% north, 48% south, 30% west, 19% east |
| Whole mountain by aspect (brief: 42 N, 21 W, 19 E, 18 S) | roughly | 38 N, 30 W, 12 E, 19 S |
| Back Bowls are more open than the Front Side | yes | 17% tree cover against 39% |

North and south shares match the published figures closely. East and west differ, most likely from where the quadrant lines are drawn and what ground is counted.

## What the terrain already says about wind

Under northwest flow, Vail's best pattern:

- **The Front Side and Blue Sky Basin sit exposed**: 77% and 80% of their ground looks down on everything upwind.
- **The Back Bowls sit in the lee**: only 19% exposed. Sun Down, Siberia and both Mongolia bowls are almost entirely sheltered.
- **Northwoods is the least exposed Front Side zone** (57%), against 86% for Lionshead. That fits your sense that Northwoods holds more snow, though this is terrain shape only, not a snowfall measurement.

This is the raw material for the wind-redistribution layer in Phase 4. It says where snow would tend to scour and load, not how much falls.

## Zones as drafted

| Zone | Acres | Elevation (ft) | Faces north | Trees | Exposed to NW wind |
|---|---|---|---|---|---|
| Lionshead / Born Free | 872 | 8,026 to 10,510 | 77% | 34% | 86% |
| Eagle's Nest / Avanti | 454 | 8,874 to 10,740 | 81% | 24% | 74% |
| Game Creek Bowl | 452 | 9,656 to 10,981 | 42% | 32% | 82% |
| Mid-Vail / Mountaintop | 511 | 9,716 to 11,255 | 51% | 33% | 75% |
| Northwoods | 442 | 9,050 to 11,248 | 71% | 43% | 57% |
| Riva Ridge / Vail Village | 456 | 8,139 to 10,002 | 74% | 46% | 71% |
| Golden Peak | 365 | 8,209 to 10,073 | 30% | 41% | 75% |
| Highline / Sourdough | 639 | 9,236 to 11,241 | 49% | 56% | 82% |
| Sun Down Bowl | 566 | 9,320 to 11,248 | 3% | 19% | 7% |
| Sun Up Bowl | 558 | 9,303 to 11,245 | 2% | 20% | 31% |
| Tea Cup Bowl | 483 | 9,486 to 11,242 | 1% | 24% | 13% |
| China Bowl | 661 | 9,720 to 11,485 | 8% | 18% | 39% |
| Siberia Bowl | 317 | 9,982 to 11,637 | 2% | 6% | 5% |
| Inner Mongolia Bowl | 169 | 10,183 to 11,633 | 4% | 7% | 2% |
| Outer Mongolia Bowl | 177 | 10,357 to 11,610 | 1% | 7% | 0% |
| Pete's Bowl | 406 | 9,902 to 11,622 | 16% | 43% | 87% |
| Earl's Bowl | 1,073 | 9,500 to 11,498 | 48% | 40% | 77% |

### How the zones were drawn

1. Each zone lists the mapped trails that belong to it (158 trail names in all).
2. Ground counts as part of the resort if it is inside the mapped boundary and within 150 m of a trail or lift. The mapped boundary alone is the permit area and takes in large tracts no lift reaches.
3. Every cell joins the zone whose trails are nearest over the ground. Crossing a ridge crest or a main creek is made expensive, so edges settle on ridgelines and valley floors.

### Where I expect to be wrong

- **I assigned trails to zones from general knowledge of the trail map, then corrected the ones whose mapped position contradicted me.** You know the mountain; I do not. The Front Side between Lionshead, Avanti and Mid-Vail is the least certain.
- **Highline / Sourdough is my addition.** Your list had no zone for the terrain east of Northwoods, and it matters for the east-versus-west question.
- **Eagle's Nest is drawn as the Avanti pod**, not as the top-of-gondola area. Upper Lionshead runs sit in Lionshead and are separated by elevation band instead.
- **Acreage is not skiable acreage.** The zones total 8,601 acres against the resort's 5,289, because they include the trees between runs. Snow falls there too, so this is deliberate.
- **Zone tops overshoot lift tops by up to 150 m of ground**, which is why Pete's reads 11,622 ft against the 11,570 ft summit.
- **171 of the 356 mapped trail segments seed nothing** (catwalks, roads, unnamed lines, and names I was unsure of). All of them still fall inside a zone.

## Not done in this phase

- Vail Pass is off the 10 m grid (about 1.4 km east). It is on the 30 m regional grid, which is enough for a reference point.
- Tree cover outside the lidar box. Not needed until the regional work.
- The wind-shelter reach is fixed at 300 m. Whether a longer reach predicts loading better is a Phase 4 question that needs snow data to answer.
- Still open from Phase 0: the reduced lead-time verification matrix, and which machine and NAS path this runs on.

## Next: Phase 2

Historical ingest and the storm catalogue, ending in `STORM_CLIMATOLOGY.md`:

1. Pull the full SNOTEL record for the seven sites, plus the valley observers.
2. Detect and rank snow events at site 842 and tag the top 200.
3. Attach ERA5 upper-air conditions to every event, and HRRR to those from late 2014.
4. Cluster into flow regimes and measure how totals differed between sites in each.
5. Test the three beliefs with whatever the data supports.
