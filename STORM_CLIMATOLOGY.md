# Storm climatology for Vail Mountain

Built 2026-11-23, **corrected 2026-11-24**. Every number here comes from `data/catalog/climatology.json`, which `uv run vailsnow climatology` regenerates.

> **Correction.** The first version dated every SNOTEL gain one day early. The SNOTEL service returns end-of-day readings unless asked otherwise, and the code read them as start-of-day. Each day's snow was therefore matched to the wind of the day before, when storms are usually still blowing from further southwest. The error was found in Phase 3 when forecasts lined up with the gauge only after a one-day shift. Everything below is recomputed. What changed is listed at the end.

## Summary

1. **West and west-northwest flow carry most of Vail's water. Northwest flow brings less per day, and it is when Vail most outperforms Beaver Creek.** Per day, the yield is about level from south-southwest round to west, then falls off toward north.
2. **Top beats bottom, in every regime.** Mid-mountain collects 1.9 times the town's precipitation, rising to 2.5 times under northwest flow and falling to 1.3 times under southwest flow.
3. **East beats west when the wind turns northwest, and west beats east when it turns southwest.** This is clear across the region and faint on the valley floor.
4. **Northwoods over Lionshead is supported by two models and by terrain, and by no measurement.**
5. **No single upper-air number predicts how big a storm gets.** Duration matters most.
6. **Vail's big storms are long and steady, not bursts.**

## What this is built on

| Evidence | Coverage | What it can say |
|---|---|---|
| SNOTEL daily snow water, 7 sites | 1978 to 2026 (Vail, Copper); 2002 on for the Beaver Creek sites | Water gained per day at each site. The backbone |
| Valley observers, 5 sites | Town from 1985, East Vail 2005, West Vail 2008 | Snowfall and liquid on the valley floor |
| Wind and temperature aloft, every winter day | ERA5 on a 5.6 degree grid, 1978 to January 2023 | Which way the large-scale flow was blowing each day |
| Detailed upper-air profile, top storms | ERA5 on a 1.5 degree grid, 197 storms | Temperature, moisture, stability, jet and 500 mb pattern per storm |
| Hourly SNOTEL gauge | 2006 on, 48 of the top storms | Whether snow came steadily or in a burst |
| PRISM 800 m precipitation normals | 1991 to 2020 | A statistical map of average precipitation. A model |
| SNODAS 1 km snow model | 59 top storms since 2003 | How a snow model spread each storm. A model |
| NWS Grand Junction storm write-ups | 21 storms, 2016 to 2022 | Forecaster narrative, linked to catalogue storms |

### Limits to keep in mind

- **Wind direction is the large-scale flow at 600 mb, averaged over a grid box hundreds of kilometres wide.** It says nothing about valley winds or what the ridge-top anemometers saw. 600 mb is used because 700 mb is below mid-mountain at Vail.
- **A day gets one direction.** Storms turn. A front that arrives on southwest wind and ends on northwest wind is filed under its daily average.
- **The record aloft stops on 2023-01-10.** The last three and a half seasons, and 8 of the 205 top storms, have observations but no flow direction here. Phase 3 fills the gap from 2018 on with HRRR.
- **Site comparisons use all winter days, not chosen storms.** Picking storms by Vail's own total inflates Vail: the Vail-to-Copper ratio reads 1.31 over all days and about 1.8 when measured only in Vail's top storms. Ranges in brackets are 90% intervals from resampling whole seasons.
- **Snow is measured in one place on the mountain.** Everything about differences within the ski area comes from models and terrain.

## How a storm is defined

A snow day is one where the SNOTEL pillow gained at least 0.2 in of water, or 0.1 in with the precipitation gauge agreeing.
A storm is a run of snow days, split where the gain drops to a lull and picks up again.
That gives 1,567 storms. The 205 "top" storms are the 200 largest by water plus any in the top 100 for their best two days; the smallest of the top 200 brought 1.4 in.

## 1. Which flow brings the snow

All winter days (November to April), 1978 to 2023, by the direction the 600 mb wind blew from:

| Wind from | Share of winter days | Share of Vail's winter water | Water per day | Odds of a snow day | Town snow-to-liquid ratio | 700 mb temperature |
|---|---|---|---|---|---|---|
| SSW | 2% | 2% | 0.15 in | 31% | 11.4 | -1.6 C |
| SW | 7% | 7% | 0.13 in | 31% | 12.3 | -1.6 C |
| WSW | 14% | 17% | 0.15 in | 32% | 13.4 | -2.3 C |
| W | 21% | 26% | 0.15 in | 34% | 14.1 | -3.2 C |
| WNW | 22% | 23% | 0.13 in | 29% | 14.0 | -4.1 C |
| NW | 16% | 14% | 0.11 in | 24% | 15.7 | -5.0 C |
| NNW | 8% | 6% | 0.09 in | 22% | 18.2 | -6.0 C |
| N | 2% | 1% | 0.05 in | 10% | n/a | -5.9 C |
| Weak flow (under 5 m/s) | 7% | 4% | 0.08 in | 16% | 12.9 | -3.8 C |

Water per day is about level from south-southwest round to west (0.13 to 0.15 in), then falls as the flow swings toward north. The snow gets colder and lighter as it does: the town's snow-to-liquid ratio climbs from 11 to 18.
Multiplying the two, a west day yields about 2.2 in of snow and a northwest day about 1.7 in (using the town ratio at mid-mountain, which is a rough stand-in).
West through northwest flow together is 59% of winter days and 63% of the water.

The top storms lean the same way. Of the 197 with upper-air data:

| Storm-average wind from | Storms | Water | Mean per storm |
|---|---|---|---|
| SW | 4 | 12 in | 2.9 in |
| WSW | 37 | 71 in | 1.9 in |
| W | 85 | 200 in | 2.4 in |
| WNW | 50 | 101 in | 2.0 in |
| NW and north of it | 18 | 37 in | 2.0 in |

Three storms had weak flow. Storms from west-northwest round to north are 35% of the top storms and a third of their water. Mean size is about 2 in of water in every direction.

**On the brief's claim that northwest flow is Vail's best pattern.** By water, west flow is the larger contributor and yields more per day. Northwest flow is where Vail beats its neighbours and the valley by the widest margin (section 2), and its snow is the lightest. Many storms also turn during the event: 45% of the top storms veered by more than 20 degrees (typically toward northwest) and 30% backed.

### Other regime traits

A closed 500 mb low within 700 km overrides the wind sector. With that rule the 197 top storms split into 130 west, 31 northwest, 26 closed low, 5 southwest and 5 weak flow.

- **Season.** Northwest-flow top storms cluster in November to January (17 of 31). Closed lows are a spring pattern (18 of 26 in April and May).
- **Temperature.** Northwest storms average -6.0 C at 700 mb, west -5.3 C, southwest -4.3 C, closed lows -2.8 C.
- **Closed lows** are 13% of the top storms and 15% of their water, and the largest on average (2.4 in).
- **Southwest-flow top storms are rare:** 5 of 197.
- **An independent check.** K-means clustering on wind, temperature, moisture and lapse rate puts 18 of the 31 northwest storms in a cluster of their own. West-flow storms spread across four clusters, so "west" is a broad label covering storms that differ in speed and temperature.

## 2. Vail against its neighbours

Total water at Vail Mountain SNOTEL (10,290 ft) divided by total water at each site, all winter days both reported:

| Compared with | All days | Northwest | West | Southwest |
|---|---|---|---|---|
| Beaver Ck Village (8,530 ft) | 1.56 (1.51-1.63) | **2.00** (1.91-2.10) | 1.42 (1.34-1.51) | 1.27 (1.17-1.38) |
| McCoy Park (9,500 ft) | 1.20 (1.15-1.26) | **1.47** (1.37-1.57) | 1.14 (1.07-1.22) | 1.05 (0.98-1.14) |
| Copper Mountain (10,500 ft) | 1.31 (1.26-1.36) | **1.15** (1.10-1.21) | 1.42 (1.34-1.50) | 1.46 (1.33-1.59) |
| Fremont Pass (11,310 ft) | 1.23 | 1.07 | 1.33 | 1.61 |
| Summit Ranch (9,350 ft) | 1.64 | 1.98 | 1.45 | 1.83 |
| Elliot Ridge (10,550 ft) | 0.90 | 1.05 | 0.87 | 0.80 |

Sectors here are 45 degrees wide: northwest is 292 to 338 degrees, west 248 to 292, southwest 202 to 248.

In finer steps of wind direction:

| Wind from | Vail / McCoy Park | Vail / Beaver Ck Village | Vail / Copper | Copper / McCoy Park |
|---|---|---|---|---|
| SW | 1.05 | 1.24 | 1.39 | 0.78 |
| WSW | 1.00 | 1.27 | 1.61 | 0.67 |
| W | 1.08 | 1.36 | 1.41 | 0.85 |
| WNW | 1.38 | 1.72 | 1.19 | 1.15 |
| NW | 1.66 | 2.14 | 1.16 | 1.58 |
| NNW | 1.28 | 2.27 | 1.25 | 1.01 |

### Against the claims in the brief

| Claim | Verdict |
|---|---|
| With northwest wind Vail can roughly double Beaver Creek | **Holds** against Beaver Ck Village (2.1 times), which sits 1,760 ft lower. Against McCoy Park, on Beaver Creek's upper mountain, it is 1.7 times |
| With due-west wind Beaver Creek can beat Vail by about 30% | **Parity on average, not a 30% deficit.** In west-southwest and west flow Vail and McCoy Park collect the same within 8%. Single storms go Beaver Creek's way: January 2014 was a due-west storm and McCoy Park took 2.7 in to Vail's 2.5 |
| The Vail, Beaver Creek, Copper ratio works as a regime diagnostic | **Yes.** Copper over McCoy Park runs from 0.67 in west-southwest flow to 1.58 in northwest flow |
| A lingering jet under northwest flow means bigger totals | **Not shown.** Eight northwest storms with at least 50 m/s overhead at 300 mb averaged 2.2 in of water; twenty-three without averaged 2.0 in |

## 3. Your three beliefs

### Top gets more than the bottom: confirmed

| Regime | Mid-mountain gauge / town COOP | Winter days |
|---|---|---|
| All days | 1.85 (1.77-1.93) | 6,074 |
| Northwest | 2.47 (2.26-2.74) | 1,971 |
| West | 1.72 (1.64-1.81) | 2,518 |
| Southwest | 1.25 (1.14-1.38) | 813 |

The elevation gain is steepest under northwest flow and nearly disappears under southwest flow, which is what orographic lift predicts. The gauges differ in type and the town one is read by a volunteer, so the exact ratio is soft.
PRISM puts the gain at about 24% of the mid-mountain value per 1,000 ft across the ski area.

### East beats west: true when the flow is northwest, reversed when it is southwest

**Across the region, clearly.** Copper lies east of Vail and Beaver Creek lies west. Copper over McCoy Park climbs from 0.67 to 1.58 as the wind swings from west-southwest to northwest. The east side wins in northwest flow and the west side wins in southwest flow.

**On the valley floor, faintly.** East Vail against West Vail, all winter days both reported:

| Regime | Snowfall, east / west | Liquid, east / west |
|---|---|---|
| All days | 1.03 (1.00-1.06) | 0.89 (0.86-0.93) |
| North | 1.20 (1.04-1.39) | 1.13 (1.02-1.22) |
| Northwest | 1.04 (1.00-1.09) | 0.87 (0.83-0.92) |
| West | 1.05 (1.01-1.09) | 0.90 (0.86-0.95) |
| Southwest | 0.90 (0.85-0.95) | 0.85 (0.80-0.92) |

East Vail reports a little more snow and about 11% less liquid than West Vail, which looks like two observers measuring differently. What can be trusted is the shift between regimes: East Vail's snowfall share is about 15% better in northwest flow than in southwest flow. North flow is based on only 93 days and 31 in of snow.
In the February 2020 northwest storm East Vail measured 40 in of snow against 33 in at both the town station and West Vail.

**Net.** An east-side advantage is a northwest-flow rule, not a year-round one. West and west-northwest flow, which carry about half the water, show a 5% east-side edge in snowfall on the valley floor.

### Northwoods gets more than Lionshead: supported by models and terrain, unmeasured

Comparing the same elevation band (10,000 to 11,000 ft), relative to the SNOTEL cell:

| Zone, west to east | PRISM long-term average | SNODAS, all 59 storms | SNODAS, 9 northwest storms | SNODAS, 43 west storms |
|---|---|---|---|---|
| Lionshead / Born Free | 0.93 | 0.93 | 0.89 | 0.94 |
| Eagle's Nest / Avanti | 1.01 | 0.98 | 0.98 | 0.98 |
| Mid-Vail / Mountaintop | 1.16 | 0.98 | 1.03 | 0.98 |
| Northwoods | 1.23 | 0.99 | 1.05 | 0.99 |
| Highline / Sourdough | 1.20 | 0.98 | 1.08 | 0.97 |

- PRISM gives Northwoods about 30% more than Lionshead at the same elevation.
- SNODAS gives about 6% more overall and about 18% more in northwest storms.
- Phase 1 found Northwoods the least wind-exposed Front Side zone under northwest flow (57% exposed against 86% for Lionshead).

All three point the same way. None is a measurement on the hill. PRISM is anchored to the SNOTEL site (its value there, 21.0 in, matches the gauge's 21.7 in), and its west-to-east rise is how it connects that anchor to the wetter stations toward Vail Pass.
SNODAS is modest evidence on its own: it caught 70% of the water observed at Vail in these storms and tracked storm-to-storm changes loosely (correlation 0.44).

## 4. Across the whole mountain

PRISM winter (November to April) precipitation by zone, relative to the SNOTEL cell, all elevations:

| Zone | PRISM | Mean elevation | Left over after elevation |
|---|---|---|---|
| Golden Peak | 0.68 | 9,070 ft | -0.12 |
| Lionshead / Born Free | 0.71 | 9,200 ft | -0.11 |
| Riva Ridge / Vail Village | 0.75 | 9,000 ft | -0.03 |
| Eagle's Nest / Avanti | 0.91 | 9,880 ft | -0.08 |
| Game Creek Bowl | 1.10 | 10,390 ft | -0.01 |
| Northwoods | 1.14 | 10,260 ft | +0.05 |
| Earl's Bowl | 1.15 | 10,540 ft | 0.00 |
| Mid-Vail / Mountaintop | 1.16 | 10,560 ft | 0.00 |
| Highline / Sourdough | 1.17 | 10,480 ft | +0.03 |
| Sun Down, Tea Cup, Pete's bowls | 1.18 | 10,270 to 10,840 ft | -0.05 to +0.09 |
| Outer Mongolia Bowl | 1.20 | 10,920 ft | -0.05 |
| Sun Up Bowl | 1.21 | 10,410 ft | +0.08 |
| Inner Mongolia, Siberia, China bowls | 1.23 to 1.25 | 10,690 to 10,810 ft | +0.01 to +0.06 |

Elevation alone explains 77% of PRISM's variation across the ski area. What is left over is small: at most about a tenth of the mid-mountain value.

SNODAS shows the regime dependence PRISM cannot. In northwest storms it puts Blue Sky Basin and the Mongolia bowls 8 to 12% above the SNOTEL cell; in west storms it puts them 2 to 6% below. The brief describes Blue Sky Basin as the snowiest terrain. SNODAS agrees for northwest flow and closed lows, not for west flow.

Neither product knows about wind moving snow after it lands. That is Phase 4.

## 5. What makes a storm big

Rank correlation with total storm water across the 197 top storms:

| Factor | Correlation |
|---|---|
| Duration in days | 0.48 |
| Humidity in the snow-growth layer | 0.18 |
| Vapour transport above 700 mb | 0.15 |
| Humidity at 600 mb | 0.14 |
| 600 mb wind speed, Froude number, jet speed, column moisture, temperature, lapse rate | under 0.10 |

Among storms that are already large, nothing aloft separates the largest from the rest except how long they last. This is measured on a coarse grid and only within the top storms, so it does not mean these variables are useless for telling a snow day from a dry one. It does mean the forecast model needs the weather models' own precipitation forecast as its main input, with these as corrections.

## 6. Steady or bursty

From the hourly gauge, for the 48 top storms since 2006:

- The wettest six hours carried 21% of a storm's water on average (range 10% to 33%).
- Precipitation was falling in 54 hours of a typical top storm.
- The peak hour was usually 0.1 to 0.2 in of water.
- Northwest and west storms looked the same on these measures (8 and 27 storms). The four closed lows were a little more concentrated (26%).

The gauge reads to 0.1 in, so this is coarse. The February 2020 storm fits the pattern: 3.2 in of water over 70 wet hours, with no six-hour stretch above 0.5 in.

## 7. Benchmark storms

| | Feb 5 to 9, 2020 | Jan 29 to 31, 2014 |
|---|---|---|
| Water at Vail SNOTEL | 3.3 in (19th largest; 4th for its best two days) | 2.5 in (50th; 23rd for two days) |
| Storm-average 600 mb wind | From 303 degrees (NW), 22 m/s, peak 26 | From 269 degrees (W), 17 m/s, peak 27 |
| How the wind turned | Veered 120 degrees | Backed 25 degrees |
| 700 mb temperature | -8.4 C | -6.4 C |
| Wind at 300 mb overhead | 53 m/s. The jet core was 5 degrees north | 56 m/s. The jet core was 2 degrees south |
| Snow-growth layer (-15 C) | At 596 mb, right at ridge-top level | At 567 mb |
| Copper / Beaver Ck Village / McCoy Park | 2.9 / 1.5 / 1.4 in | 2.1 / 2.5 / 2.7 in |
| Snowfall on the valley floor | East Vail 40 in, town 33 in, West Vail 33 in | Town 24 in, West Vail 25 in |
| SNODAS at the Vail SNOTEL cell | 1.5 in, 44% of observed | 2.2 in, 87% of observed |
| NWS write-up | In the case library | None (the write-ups start in 2016) |

The dates are days on which the snow fell, on the station's clock (Pacific Standard Time).
February 2020 is the northwest-flow pattern at full strength: Vail and Copper took twice what Beaver Creek did, and the snow-growth layer sat on the ridge. January 2014 is the opposite pattern, a due-west storm in which both Beaver Creek sites matched or beat Vail.

## What is still missing

- **Vertical motion in the snow-growth layer** and **banded versus convective structure.** The reanalysis grid is too coarse for either.
- **Flow direction after January 2023 in these tables.** Phase 3 has it from HRRR for 2018 on, but the tables here still stop in 2023.
- **Any measurement of how snow is distributed within the ski area.**

## What the correction changed

| Finding | First version (dates one day early) | Corrected |
|---|---|---|
| Water per day, south-southwest flow | 0.25 in, the highest | 0.15 in, level with west |
| Water per day, northwest flow | 0.09 in | 0.11 in |
| Top storms filed as southwest | 21 | 5 |
| Top storms filed as northwest | 22 | 31 |
| Top storms with a closed low nearby | 11 | 26 |
| Vail / Beaver Ck Village, northwest flow | 1.81 | 2.00 |
| Mid-mountain / town, northwest flow | 2.12 | 2.47 |
| Mid-mountain / town, southwest flow | 1.82 | 1.25 |
| SNODAS share of observed water at Vail | 56% | 70% |
| February 2020 storm | Feb 4 to 8, wind from 313 degrees | Feb 5 to 9, wind from 303 degrees |
| January 2014 storm | Jan 28 to 30, wind from 289 degrees (WNW) | Jan 29 to 31, wind from 269 degrees (W) |

The first version's headline, that southwest flow brings the most water per day, was an artefact of the dating error and is withdrawn.
Site-against-site totals over all days did not change. Valley-only comparisons (East Vail against West Vail) did not change.

## Sources

SNOTEL: USDA NRCS. Valley observers: NWS COOP and CoCoRaHS (CC BY 4.0) via RCC-ACIS.
Reanalysis: ERA5, Copernicus Climate Change Service (CC BY 4.0), via the WeatherBench 2 public copy.
Precipitation normals: PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu, accessed 2026-11-23.
Snow model: SNODAS, NOAA National Operational Hydrologic Remote Sensing Center, archived at NSIDC (G02158).
Storm write-ups: NWS Grand Junction.
