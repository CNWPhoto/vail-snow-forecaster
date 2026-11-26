# Mountain state

The forecast says what will fall. This says what is on the ground now, cell by cell, stepped every hour, so the Vail ski game can show real conditions: snowing hard when it is snowing hard at Vail, deep fresh snow after a storm, and after a dry spell no powder left except in the trees.

Written 2026-11-25. Everything here is a modelled estimate. It is not an official snow report and has no connection to Vail Resorts.

## Where it is published

Once the branch is merged and the hourly job is running, the base URL for the game is:

`https://cnwphoto.github.io/vail-snow-forecaster/mountain/`

It is served by GitHub Pages, which sends `Access-Control-Allow-Origin: *` (so the game can read it from any origin, including a page opened from disk) and caches every file for 10 minutes. That cache time cannot be changed on Pages.

| File | What it is |
|---|---|
| `grid.json` | The shared grid, the zone and terrain-class lists, and the scale of every texture channel |
| `terrain.webp` | What never changes: terrain class, groomed share, zone |
| `state/latest.json` | The newest hour: weather now, a summary per zone, the conditions line, and the paths of its textures |
| `state/YYYY/MM/DD/HH/` | That hour's `state.json` and three textures. Kept for 72 hours, then removed |
| `state/index.json` | Which hours are on the site (for a time-lapse) |
| `state/tomorrow.json` | The same for tomorrow at first chair, built from the median forecast |
| `days.json`, `days/<id>/` | Notable past days rebuilt from the record |
| `scores.jsonl` | One line per morning: what the state held at the gauge beside what the depth sensor read. The Score tab is built from it |

`latest.json` is the only file rewritten in place. It names the textures inside its own hour's folder, and an hour's folder is never rewritten. A game that reads `latest.json` and then the textures it names cannot end up with images from two different hours.

## The grid

The game's Vail level is a 2 m height grid in NAD83 / UTM zone 13N (EPSG:26913). The state grid is that same box in 16 m cells.

| | |
|---|---|
| West, north | 376200, 4390000 (the game's `E0` and `N_TOP`) |
| East, south | 391560, 4379504 |
| Size | 960 x 656 cells of 16 m; one state cell is 8 x 8 game cells |
| Lookup | `u = x / 15360`, `v = z / 10496`, with x metres east of `E0` and z metres south of `N_TOP` |
| Rows | Row 0 is the north edge, column 0 the west edge. Do not flip on load |

The forecast's own 10 m grid was not reused: it sits 1 m off the game's box and stops 7 m short of its south edge. The static layers are cut from the game's 2 m arrays (`vailsnow state-grid`), and the forecast's fields are resampled onto them.

## Textures

Lossless WebP, 8-bit RGB, no alpha channel. Every pixel decodes to exactly the value written. A value is `channel / 255 * max` unless the table says otherwise. Load them with no colour-space conversion and no premultiplied alpha. All textures cover the whole box; `history` has half as many pixels each way, so sample by u, v.

| Texture | Channel | Field | Scale |
|---|---|---|---|
| `surface` | R | Depth of the fresh layer, inches | 0 to 51, in steps of 0.4 in |
| | G | Share of that layer already skied, on open ground | 0 untouched, 1 fully tracked (16 steps) |
| | B | The same under trees | 0 to 1 (16 steps) |
| `character` | R | Density of the fresh layer, kg per cubic metre | 0 to 408; 0 where there is no fresh layer |
| | G | Wind effect | `(channel - 128) / 112`: -1 scoured, 0 untouched, +1 loaded to twice what fell (15 steps) |
| | B | Crust | 0 none, 128 sun crust, 255 melt-freeze crust |
| `history` | R, G, B | New snow in the last 24 h, 72 h and 7 days, inches | 0 to 51, 102 and 153 |
| `terrain` | R | Terrain class | index x 40 (list in `grid.json`) |
| | G | Share of the cell's open ground groomed nightly | 0 to 1 |
| | B | Zone | index x 10 (list in `grid.json`; 0 is no zone) |

Untracked fresh depth is `fresh * (1 - tracked)`. The game should pick G or B with its own 2 m canopy mask: that is what keeps a stash sharp at the tree line when the cell is half run and half trees.

Size: about 270 KB for the three textures on a quiet day and 420 to 460 KB on the deep and windy days rebuilt so far. The target was 500 KB. Fresh depth is stored in 0.4 in steps and the shares in 16 steps to get there.

Total snow depth is carried for the scorecard only and appears in nothing the game reads. Nothing published implies bare ground, thin cover or closed terrain; `open` on a zone only says whether skiers are tracking it out.

## What drives it

Observations first, the model in between.

| Quantity | Source |
|---|---|
| Water falling at mid-mountain | The Vail Mountain SNOTEL gauge. Checked daily totals shared out by the hourly record; the newest day or so is provisional and is averaged with the model's short-range amount, then run again once the checked total is in |
| How deep that water lands | The forecast's snow-to-liquid model, then scaled by 0.6 to 1.4 so the day's settled gain agrees with the SNOTEL depth sensor |
| Temperature | The SNOTEL sensor at its own elevation; the model's change with height above and below it |
| Spread across the mountain | Exactly as the forecast does it: the long-term PRISM ratio and the model's pattern for those hours, rain or snow by wet-bulb temperature at each cell |
| Wind | HRRR free-air wind corrected to ridge level (half the speed, 35 degrees backed, as measured at the Copper summit station) |
| Cloud, visibility, gusts | HRRR, newest run, usually 2 to 3 hours old |

The wind rules are switched off in the nine cells around the gauge. The loading rule puts the gauge's clearing in the lee of a ridge and would give it about 1.3 times what the gauge measures; but the gauge is the reference everything is scaled from.

Each hour, in order: snowfall sorted by wind as it lands, wind moving loose snow already down, settling, melt, ageing, skiers, grooming, crust. Water enters only as snowfall and leaves only as melt.

## Powder survival

Each cell keeps two untracked depths, one for its open ground and one for ground under trees. Skiers use them up only while the lifts turn, only on open terrain, faster near mapped runs and lift tops, slower on very flat and very steep ground, slower in shaded trees.

| Ground | Lift-served hours to use up about two thirds, at the busiest spots on a weekday |
|---|---|
| Groomed run | 1.5 |
| Ungroomed run | 2.5 |
| Open bowl | 3.5 |
| Glade | 10 |
| Tight trees | 30, up to 45 where shaded |
| Outside the ski-area boundary | 150 |

Weekends count 1.5 times, holidays 1.9 times. Groomed ground is reset to corduroy at 4 am. Untouched snow also stops skiing as fresh: after 48 hours without new snow it fades into the base over about 3 days in the open and up to 10 days in shaded trees.

All of this is in `resorts/vail.toml` under `[state]`. None of it is measured. The place to calibrate it is a set of hand readings (time, place, fresh depth, how tracked); there is no logger at present, since the depth logger was dropped in Phase 6.

## What was assumed

- **Groomed runs.** Nothing public and permitted reports grooming, and only 19 of 356 mapped runs carry a grooming tag. So every green and blue run is taken as groomed nightly (111 named runs), along a strip 40 m wide on its mapped line; black runs and bowls are not. `vailsnow state-grid` prints the list.
- **What is open.** The Front Side from opening day, 2026-11-13 (from news coverage). The Back Bowls from 2026-12-11 and Blue Sky Basin from 2026-12-18 are guesses: edit `[state.operations.opens]` when they open. Until then those zones hold their snow untouched, as closed terrain should.
- **Lift hours.** 9:00 to 3:30 until 2026-12-12 (opening-day hours, from news coverage), then 8:30 to 4:00 (assumed). Closing day 2027-04-18 is assumed.
- **Holidays.** Thanksgiving, Dec 19 to Jan 3, the MLK weekend and Presidents' week.

## What differs from the brief

- **On-mountain stations.** The resort's four stations are not public data this project may read. Temperature is the SNOTEL sensor's; wind is the model's.
- **Stakes.** The resort's stake readings cannot be republished, and no public sensor measures snow in Blue Sky Basin. The scorecard compares the state with the SNOTEL depth sensor, which stands beside the resort's mid-mountain station.
- **Hosting.** GitHub Pages instead of R2, by decision on 2026-11-25. So there is a rolling 72 hours of hourly states instead of a full archive, and one cache time (10 minutes) for everything.
- **Jan 31, 2014.** It is before the HRRR archive begins. It is rebuilt from the gauge and ERA5 daily winds; cloud and visibility are not known.

## Notable days

`days.json` lists them. The two deep days were named in the brief. The other three were picked from the SNOTEL record since 2018 and the archived model runs:

| Day | Why this one |
|---|---|
| Feb 7, 2020, 9 am | Named in the brief |
| Jan 31, 2014, 9 am | Named in the brief. The depth sensor rose 19 in the day before |
| Mar 4, 2019, 10 am | The largest two-day storm since 2018 (1.8 in of water) that was followed by a dry day with a wide day-to-night temperature swing |
| Jan 31, 2024, noon | Fifteen dry days in a row in midwinter. Run from 24 days back, so the last storm is in it and what survives in the trees is there to find |
| Dec 22, 2022, 2 pm | Among the three windiest days in the model archive (27 m/s free-air wind from the northwest) with fresh snow down to move |

Past days are run as if every zone were open, with this season's survival rules.

## Checks

- `tests/test_state.py`: no negative depths; water is conserved through wind (below freezing the only way in is snowfall); melt is the only way out; fresh snow never increases in dry, calm hours; closed terrain is untouched and groomers are tracked first; textures are RGB and decode to what was written; nothing published speaks of bare ground.
- The Score tab shows the state beside the depth sensor at 5 am each day. This is not an independent test: the state is driven by that gauge and scaled by that sensor. It shows the state stays tied to its one instrument.

## Running it

```bash
vailsnow state-grid      # cut the grid and static layers (needs the ski game's data folder)
vailsnow state           # bring the state up to now and publish latest and tomorrow
vailsnow state-days      # rebuild the notable past days
```

On GitHub the hourly job is `.github/workflows/state.yml`. It needs a release asset named `state-static-v1` holding `state_static.tar.gz` (the two `vail_static` files from `data/state/`). It keeps a checkpoint in the Actions cache; if that is lost it rebuilds from ten days of data. A late or skipped run changes nothing, because each run steps from the checkpoint to the present.

The state job has its own queue and never waits behind the forecast jobs. Both publish by replacing the one commit on `gh-pages`, so the state job takes the site as it stands when it publishes, swaps in only `mountain/`, and pushes on the condition that nobody else has published in between; if somebody has, it starts again from the newer site. It therefore cannot overwrite a forecast. A forecast job that was already running can set `mountain/` back by one run, which the next hour puts right.
