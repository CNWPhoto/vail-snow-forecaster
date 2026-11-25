# Correction: late gauge ticks, and what changed with them

Date: 2026-11-24. This replaces the scores in the Phase 3, 4 and 6 summaries and in the first version of `TEN_DAY_OUTLOOK.md`. Those pages carry a pointer here.

## What was wrong

The 10-day view showed snow on every day, including three days when every model was dry. Two causes:

- **Display.** The 10-day views led with the "expected" (average) amount, and with the flawed record below that average was never zero, so every day showed something.
- **The record the model learned from.** The SNOTEL gauge record rises a tenth of an inch at a time, and the tick often lands a day or more after the snow: snow caps the gauge and drops in later. I had counted every tick as snowfall on the day it registered.

The evidence, from the mid-mountain gauge and the depth sensor beside it, winters 2018-19 to 2025-26:

| | Days |
|---|---|
| Days the gauge rose by a single tick (0.1 in) | 294 |
| Of those, days the depth sensor showed no new snow | 211 |
| Days every model was dry | 529 |
| Of those, days the gauge still ticked | 97 (18%) |
| Of those 97, days the depth sensor rose | 5 |

So the model had been taught that a bone-dry day carries about a 1-in-4 chance of light snow.

## The fix

- **A single tick on a day the depth sensor shows no new snow is moved back** to the most recent day that did have snow, up to five days earlier. With no such day it is dropped. "New snow" means the depth ended the day half an inch higher, or peaked an inch above where it started. Only the two instruments are used, never a forecast.
- 176 ticks were reclassified. Winter water in the record went from 138.3 to 134.2 in (3% dropped; 13.5 in moved onto storm days). Days of 0.5 in or more went from 58 to 68.
- **Every model was refitted and every held-out test rerun.**
- **When every model is dry, the range is capped at what such days have actually produced**: nothing on 95% of them.
- **Single days in the 10-day view lead with the expected amount**, with the chance of snow and an "if it comes" amount beside it. I tried the likely (middle) amount first; it read zero on days with a 40% chance of snow, so it was changed back once dry days were fixed. See `TEN_DAY_OUTLOOK.md`.

Limits: a real light snowfall of an inch or so that settled within the day can look like "no new snow" to a sensor that reads to the inch, and would be moved. And after a tick is moved, a day already scored can change up to five days later.

## What it did to the forecast

On the 529 held-out days when every model was dry:

| | Before | After |
|---|---|---|
| Gauge credited with 0.1 in or more | 18% | 5% |
| Model's average 75th percentile | 0.05 in of water | 0.005 |
| Model's average 90th percentile | 0.10 in | 0.05 |

Today (Nov 24), days 1 to 3 went from "0 likely, up to 2 to 3 in" to a flat zero with under a 5% chance. The weekend shows a 28 to 42% chance of snow.

## Scores, rerun

Typical miss in inches of snow at mid-mountain, each winter forecast by a model that never saw it. The right-hand columns cover the six winters where the Blend and NWS forecasts exist.

| Days ahead | This model | Raw model | National Blend | NWS official |
|---|---|---|---|---|
| 1 | 0.93 | 1.00 (HRRR) | 0.92 | 0.99 |
| 2 | 1.00 | 1.17 (HRRR) | 1.02 | 1.07 |
| 3 | 1.10 | 1.37 (GFS) | 1.16 | 1.16 |

- Every forecast scores a little better against the corrected record; the order did not change. **Still a tie with the National Blend**, ahead of the raw models and of NWS.
- **Valley snowfall, each forecast's own snow number:** the Blend 0.52 in, this model 0.54 (was 0.59), NWS 0.59.
- **Big days are still under-called.** On 60 days of about 10 in or more, the likely number missed by 5.2 in; raw HRRR by 3.8.
- **Days 4 to 10** improved against the average for the date: 26% better at day 4 (was 22%), 16% at day 7 (was 13%), 8% at day 10 (was 6%).
- **Settled depth** at seven sensors: 1.67 in typical miss (was 1.57), against 1.76 for a fixed ratio. Slightly worse than before, still ahead.
- **Alerts at the default thresholds:** 20 a winter; 55% followed by a 4-inch day; 50% of 4-inch days alerted.

This season so far (23 days, 8 of them with snow possible): typical miss 0.56 in of snow against 0.52 for raw HRRR, and 5 of 8 inside the stated range. Behind, on few days.

## Also changed the same day

- **Ridge wind direction.** At the 12,075 ft summit station the wind blows from about 35 degrees further anticlockwise than the 600 mb wind the model used (17,543 windy hours: 31 degrees in west flow, 36 in northwest, 61 in southwest). A northwest-flow storm is a west wind at ridge level. The after-wind layer and lift exposure now use the corrected direction.
- **Wind loading** was then rebuilt and tuned to local knowledge of where the mountain loads up: see `WIND_LOADING.md`.
- **Base depth** added as a map layer: `uv run vailsnow depth`. SNODAS (1 km) for the shape, tied to the SNOTEL depth sensor, with elevation detail inside each cell. On 156 past dates SNODAS missed the mid-mountain sensor by 5.4 in on average (12%) and six others nearby by 6.2 in. It is given those sensors' readings, so between them it will be worse.
- **The Plan tab was removed.** Lift wind-hold risk still feeds the alerts.
- **The 10 day button opens on all ten days.**
