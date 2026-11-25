# 10-day outlook

Date: 2026-11-24. Added after Phase 6. Runs on a schedule on GitHub Actions since the evening of 2026-11-24, and is published at https://cnwphoto.github.io/vail-snow-forecaster/.

> **Updated 2026-11-24 after the gauge-tick correction** (`GAUGE_TICKS.md`). The scores below are the rerun ones.

## Verdict

- **The map now has a 10-day tab.** Days 1 to 3 come from the checked forecast; days 4 to 10 come from a new long-range model built on the GEFS ensemble.
- **It re-evaluates with every ensemble run, four times a day,** once it is scheduled. Each day in the strip shows how its call has moved since a day ago, and every long-range forecast is logged and later scored against the gauge.
- **Skill fades fast.** Against simply quoting the average for the time of year, the long-range forecast is 27% better at day 4, 16% at day 7 and 8% at day 10.
- **Past day 3, read the chance of snow and the high end.** The likely amount is usually zero that far out even when a storm is possible.
- **Zones add nothing new past day 3.** They differ only by the long-term pattern.

## The National Blend, and why this forecast sits below it (2026-11-24, evening)

The user's other sources had Saturday Nov 28 at 3 to 7 in by day plus 1 to 2 overnight. This forecast had 1.7 in expected.

- **Cause one: stale guidance.** Days 4 to 10 used only the GEFS ensemble on a 0.5 degree grid (0.15 in of water for Saturday). The newest National Blend cycle had 0.36 in of water and 4.5 in of snow at the gauge, up from 0.10 in the cycle before.
- **What was done:** the Blend's precipitation (2.5 km) was added as an input, using the cycle seven hours after the ensemble run, which is the first to hold that hour's GFS and European runs. Its history since November 2020 was downloaded (two cycles a day, 2,200 runs) and the model refitted.
- **What it bought:** almost nothing on held-out winters. Skill against the average for the date went from 26% to 27% at day 4 and was unchanged within a point from day 5 on. It is used because it costs nothing and keeps the forecast in step with the newest guidance.
- **Cause two, which stays: the record says to hedge.** Over six winters, when the Blend itself called for 0.30 to 0.45 in of water four to six days out, the gauge averaged 0.27 in, got 0.2 in or more on 54% of days and nothing on 23%. The Blend as issued missed by more than this model at every lead from 4 to 10 days.
- **Saturday now:** 0 to 5 in likely at Mid-Vail, 3 in expected, 63% chance of snow, 1 in 10 chance of more than 6. The Blend's own 4.5 in is shown beside it.

The 10-day rows and the map now lead with a likely range to the half inch (the 25th to 75th percentile; it held 66% of the time for single held-out days).

## How a day is shown (final form, 2026-11-24 night)

- **A firm storm** shows its likely range (the 25th to 75th percentile).
- **A day with a 20% or better chance of an inch, but no certainty,** (30% until 2026-11-25) shows the range to expect if it snows: the middle half of the snowy outcomes, never starting below an inch, with the chance beside it. Saturday Nov 28 reads "2.5 to 5.5 in if it snows, 64% chance".
- **Each day also carries a morning total** (since 2026-11-25): new snow in the 24 hours before 5 am that day, the time and window of a morning snow report. It is the forecast for the day before under another name, so days are still compared and scored day by day. Day 1's morning has already happened, so its total is what the SNOTEL gauge measured (water times the snow ratio forecast for that day). It is this forecast's figure, not the resort's report, and nothing here reads or scores against the resort's stake.
- **A day with a lower chance reads 0.** As a storm fades in later runs, its day drops to 0 on its own.
- **The map for a day draws the same thing as its figure** (since 2026-11-25): the middle of the range the day leads with, over the whole mountain. On an "if it snows" day that is what falls if it does snow; on a day that reads 0 the map is empty. Before, the map drew the chance-weighted expected amount and left out anything under half an inch, so a 20% day labelled "2 to 3 in" was mostly white.
- **Days 4 to 10 now carry wind loading** from the ensemble's average wind near 600 mb, turned to ridge level the same way as days 1 to 3. Where ensemble members disagree on direction the average wind is weak, so loading fades as the forecast gets less sure.
- The February 2025 replay was removed from the app. The top of the page shows when the data was last updated, not the model run time.

## On the map (added later the same day)

- **A "10 day" button on the map's time bar** opens a row of day buttons: "All 10" and each of the ten days.
- **Each day shows that day's snow on the map**, with the same contour colours, zone labels and zone cards. "All 10" shows the ten-day total.
- **Tapping a day on the 10 day tab does the same thing.**
- **Low end, Expected and High end** work for every day and for the total.
- **Tapping a point** lists all ten days at that spot: low end, expected and high end, with the ten-day total underneath. Tapping a row shows that day on the map.
- **Each day shows the expected amount**: the chance of snow times how much would fall, so a day with a storm signal shows snow and the days add up to the ten-day total. Beside it are the chance of snow and "about N in if it comes".
- **Why not the "likely" (middle) amount:** on a day with a 42% chance of snow the middle outcome is nothing, so the map was blank on days with a real signal. On 412 past days given a 35 to 50% chance, it snowed an inch or more on 49%, nothing fell on 48%, and the average outcome (1.6 in) matched the expected amount (1.6 in).
- **Why the expected amount is safe now:** before the gauge-tick correction it put snow on every dry day. After it, days when every model is dry read zero. An expected amount under half an inch is not drawn on the map.
- **The 10 day button opens on all ten days.** The total is the sum of the days; its range is what past runs with the same forecast total went on to measure.
- **What tells one part of the mountain from another:** days 1 to 3 use every layer (the day's HRRR pattern, snow level, density). Past day 3 only the long-term pattern, elevation and the snow level are left. There is no wind or storm-direction detail that far out, and the map says so.
- **The ten-day total's range is checked:** 79% of 967 held-out totals fell inside it (80% is the target). The total itself misses by 0.38 in of water against 0.52 for the average for the date.

Snow is now the lead number everywhere. The Why tab, the Score tab and the analogs give inches of snow first and water second. The gauge measures water, so "measured" snow on the Score tab is that water at the snow-to-water ratio the forecast used that day.

## How often it updates

> **Update, evening of 2026-11-24.** The jobs now run on GitHub Actions (`.github/workflows/forecast.yml`), not on the Mac; the Mac schedule was removed. Times are close to the table below: map at 10:50 am, 10-day outlook at 10:55 pm, 4:55 am, 1:15 pm (after the afternoon Blend) and 4:55 pm, alerts at 9:45 pm and 5:05 am, all Mountain Standard Time. GitHub starts scheduled jobs late when it is busy, sometimes by several minutes to an hour. Each job ran once there with no error.

Four scheduled jobs were installed on this Mac on 2026-11-24 (`deploy/install_schedule.sh`), and each ran once under the scheduler with no error. `uv run vailsnow status` shows when each piece last updated.

| Piece | Updates | Ready (Mountain Standard Time) | Why then |
|---|---|---|---|
| Map, days 1 to 3, zones, lifts, first chair | Once a day | 10:45 am | The 5 am HRRR run reaches the public copy by 9:30 am standard time, 10:30 am daylight time |
| 10-day outlook, days 4 to 10 | Four times a day | 10:55 pm, 4:55 am, 10:55 am, 4:55 pm | Each GEFS run's files reach day 11 about 5 hours 45 minutes after it starts |
| Evening alert | Once a day | About 9:45 pm | The 5 pm HRRR run |
| 5 am alert | Once a day | 5:05 am | Measured, not forecast |
| Scorecard | With each map build | 10:45 am | The gauge's checked daily total lags a day |

A refresh takes a few seconds:

```bash
uv run vailsnow outlook
```

## How good it is

Each winter from 2020-21 to 2025-26 was forecast by a model that never saw it: 1,069 runs.

| Days ahead | Typical miss | Raw ensemble | Average for the date | Better than the average | Days inside the range |
|---|---|---|---|---|---|
| 4 | 0.069 in | 0.075 | 0.087 | 27% | 88% |
| 5 | 0.076 in | 0.080 | 0.087 | 21% | 87% |
| 6 | 0.078 in | 0.087 | 0.087 | 18% | 88% |
| 7 | 0.080 in | 0.091 | 0.087 | 16% | 88% |
| 8 | 0.082 in | 0.099 | 0.087 | 12% | 89% |
| 9 | 0.085 in | 0.103 | 0.087 | 9% | 88% |
| 10 | 0.086 in | 0.104 | 0.087 | 8% | 89% |

Misses are inches of water at the mid-mountain gauge for one day. "Better than the average" scores the whole forecast range, not just the middle. The ranges are a little wide: 80% is the target.

- **For comparison, the day-ahead forecast misses by 0.057 in over the same winters.** Day 4 is not far behind it; day 8 onward is barely better than knowing the month.
- **Calling a wet day.** When the model gave a 30% or better chance of 0.25 in of water (about 4 in of snow), it happened 45% of the time at days 4 and 5, and about 30% at days 8 to 10. Such days make up about 12% of all days.
- **Multi-day totals are the more useful number.** Days 4 to 7 added up miss by 0.22 in against 0.30 for the average, with 85% inside the stated range. Days 8 to 10: 0.21 against 0.25, with 89% inside.
- **For totals the model is no better than the raw ensemble mean** (0.222 against 0.218). Its gain is in the single-day ranges and odds.

This season so far is on the Score tab, by days ahead. It covers 23 replayed runs, too few to mean much.

## What "re-evaluate" means here

- **Every refresh re-forecasts all ten days** from the newest complete ensemble run.
- **Each day shows its change since a day ago** and a small line of every earlier call made for it.
- **Every long-range forecast is logged before the day arrives** in `data/log/outlook.jsonl` and never overwritten. The Score tab shows the season's misses by days ahead beside the held-out numbers above.
- **The model itself does not retrain during a season.** It is refitted when you run `uv run vailsnow outlook-train`, which is a once-a-year job.

## How it works

- **Inputs:** the ensemble's mean and spread for the day, the mean for the day before and after, the area average, the lead, the 700 mb temperature, and the National Blend's precipitation at and around the gauge.
- **Fitted on the 12z run only.** A run from another hour is treated as the nearest lead, which is up to twelve hours off.
- **Snow from water:** past day 3 there is no temperature profile. Snow level and density are estimated from the 700 mb temperature alone, using relations read off past wet days.
- **Multi-day ranges** are read from what past runs with a similar forecast total went on to measure.

## Not done

- Nobody is told if a scheduled run fails. The map's "Issued" line and `vailsnow status` show a stale forecast, but nothing alerts on it.
- After the clocks change in March, the model files arrive an hour later by the wall clock. The forecast job allows for that; the evening alert and the 10-day refresh will each use a run one step older until the jobs are shifted an hour.
- Nothing past day 10, though the ensemble runs to day 16.
- ECMWF's free ensemble data was not used. It would likely help most at days 6 to 10.
- Other run hours (00z, 06z, 18z) were not scored separately.

## Built

| Piece | Where |
|---|---|
| 10-day ensemble fetch | `fcst/archive.py` (`fetch_gefs_long`) |
| Long-range model, held-out scores, multi-day ranges | `model/extended.py` |
| The outlook file, change tracking, log | `live/outlook.py` |
| By-lead scoring this season | `extras/scorecard.py` |
| 10-day tab, 10-day map mode with day buttons, day-by-day point table, by-lead table on the Score tab | `web/app/` |
| Status command (`uv run vailsnow status`) | `extras/status.py` |
| Four scheduled jobs, with install and uninstall scripts (installed) | `deploy/` |
| Tests | 184 pass (11 new) |
