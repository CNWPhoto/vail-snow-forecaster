# Phase 6: scorecard, alerts and the other extras

Date: 2026-11-24. Checkpoint for review. Everything here runs on this computer. Nothing is deployed, nothing is scheduled, and no alert has been sent anywhere except one test notification on this Mac.

> **Update, later on 2026-11-24.** The schedule is now installed on this Mac: four launchd jobs (forecast, 10-day refresh, evening alert, 5 am alert). Where this page says nothing is scheduled, that was true when it was written. Alerts still go only to a file and the map banner. See `TEN_DAY_OUTLOOK.md` for the 10-day outlook added afterwards.

> **Scores on this page predate a correction.** On 2026-11-24 the gauge record was corrected for late ticks and every model refitted. The corrected scores are in `GAUGE_TICKS.md`; rankings did not change.

## Verdict

- **The scorecard is running.** Every forecast is logged when it is built and scored against the SNOTEL gauge once the gauge reports. This season so far: 23 days scored, typical miss 0.032 in of water against 0.034 for raw HRRR.
- **Lift wind-hold risk is built, and its main input is checked.** HRRR's ridge gust was tested against a summit station at 12,075 ft. The per-lift adjustment and the hold thresholds are assumptions.
- **That check corrected a Phase 4 assumption.** Sustained ridge wind is 0.47 of the 600 mb wind, not the 0.7 I assumed. The after-wind layer now moves less snow.
- **Powder alerts work, evening and 5 am.** With your default thresholds they would have caught about half of the 4-inch days in the record.
- **Where to ski first is built and cannot be verified.** It mostly follows elevation.
- **The analog finder names the closest past days and says how close they are.** As a forecast, analogs do worse than the model.
- **Config-driven resorts: partly.** One file holds everything resort-specific. Only Vail has been built.
- **The depth logger was dropped**, as you decided.

## Scorecard

Each run of `vailsnow forecast` now writes its forecast to `data/log/forecasts.jsonl` before the answer is known. A logged forecast is never overwritten. The Score tab on the map shows the result.

| | Days scored | This model | Raw model | Inside the 10 to 90 range |
|---|---|---|---|---|
| Day ahead | 23 | 0.032 in | 0.034 (HRRR) | 21 of 22 |
| Two days ahead | 22 | 0.038 in | 0.051 (HRRR) | 13 of 15 |
| Three days ahead | 21 | 0.028 in | 0.042 (GFS) | 17 of 18 |

Typical miss in inches of water at the gauge, November 1 to 23, 2026. The range column counts only days when snow was possible.

- **23 of the 24 logged forecasts are replays.** They were rebuilt today from the model runs as issued, by the model frozen before this season, so they are out of sample. Only today's was logged on the day. The scorecard labels each one.
- **Three storms so far.** The worst call was November 20 to 21: 0.18 in forecast day-ahead (range 0.04 to 0.39), 0.47 in measured. The under-call on bigger days is still there.
- **Depth at the sensor:** typical miss 0.7 in over 22 days, with 15 inside the stated range. That range is too narrow for depth in early season, when new snow lands on a 4-inch pack.
- **Earlier winters** are shown beside it from the hindcast, with raw HRRR and the National Blend.
- It is scored against SNOTEL only, never against the resort's snow report.
- **It is not public yet.** It publishes with the map, and the map is not deployed.

## Lift wind-hold risk

### The ridge gust (checked)

Tested on 41,562 forecast hours since 2018 against the Copper Mountain summit airport station, 20 km from Vail.

| Peak gust in lift hours, day ahead | Typical miss | 40 mph days caught | False alarms |
|---|---|---|---|
| **HRRR surface gust, as issued (used)** | 5.5 mph | 123 of 206 | 42 |
| 600 mb wind, scaled | 8.2 mph | 40 of 206 | 9 |

A fitted blend of the two had a smaller hourly miss but caught half as many strong days, so HRRR's own gust is used.

Past misses turn the gust into odds. Checked with each season judged by the other seasons:

| Called | Days | Reached 40 mph |
|---|---|---|
| Low (under 20%) | 1,403 | 3% |
| Possible (20 to 50%) | 291 | 29% |
| Likely (50% or more) | 435 | 65% |

At 50 mph "likely" is too confident: 47% of 103 days reached it.

### Each lift (assumed)

- **Share of the ridge gust:** how high the lift's upper half sits, how open and unsheltered the ground around it is for the day's wind direction, and how far across the line the wind blows.
- **Thresholds:** a hold is "possible" from 35 mph and "likely" from 45 mph on chairs, 30 and 40 on gondolas. These are rules of thumb in `resorts/vail.toml`, not Vail's operating rules.
- **Nothing checks either.** No record of holds is public, and the wind sensors on the mountain are off limits.

In the February 14, 2025 replay, ridge gusts reach 39 mph on the second day and four lifts show "hold possible": Orient, Sun Up, Skyline and High Noon. The map colours those lifts when the Plan tab is open.

### What it changed in the wind layer

The same station gives sustained wind as 0.47 of the 600 mb wind. Phases 4 and 5 assumed 0.7. The forecast now uses 0.5, so a 45 mph wind aloft is taken as about 22 mph on the ridge, not 31. The after-wind layer is weaker than those phases showed. `PHASE4_SUMMARY.md` carries a correction note.

## Powder alerts

| Alert | When | What it uses |
|---|---|---|
| Evening | About 9:45 pm, when the 5 pm HRRR run is published | Tonight (5 pm to 5 am) and tomorrow, each from a model fitted to that lead time, plus what the gauge has measured since 5 am |
| 5 am | 5:05 am | What the gauge and depth sensor measured overnight, then the day ahead |

Run earlier in the evening, the alert falls back to the morning's forecast and says so.

Thresholds are in `resorts/vail.toml` under `[alerts]`: likely 4 in, high end 8 in, measured 3 in. They apply to Mid-Vail / Mountaintop.

What the evening rule would have done over eight winters of held-out forecasts:

| Likely / high-end threshold | Alerts a winter | Days that reached the likely threshold, per winter | Alerts that reached it | Such days that got an alert |
|---|---|---|---|---|
| 2 in / 4 in | 50 | 42 | 60% | 73% |
| 3 in / 6 in | 27 | 30 | 60% | 54% |
| 4 in / 6 in | 23 | 18 | 46% | 60% |
| **4 in / 8 in (default)** | 17 | 18 | 53% | 51% |
| 6 in / 10 in | 6 | 8 | 39% | 31% |

- **About half the alerts are followed by a day that reaches the threshold, and about half of such days get an alert.** Lowering the high-end threshold to 6 in catches more days at the cost of more false alerts. This is the big-day weakness showing up where it matters.
- This test uses the morning forecast for the day ahead, not the evening run, and counts snow as gauge water times the modelled ratio.
- **Delivery:** alerts are written to `data/alerts/` and shown as a banner on the map. A notification on this Mac works; I sent one test. A phone push through ntfy.sh is written and **untested**, because it sends the text to an outside service. It stays off until you add it to the channel list.
- **Nothing is scheduled.** Three launchd files are in `deploy/`; none is installed.

Example, replayed for the evening of November 20:

> Vail Mountain: 5.7 in likely by 5 am (3.7 to 8.1). Tonight, 5 pm to 5 am: 2.7 in likely (0.7 to 5.1). Since 5 am the gauge has measured 0.14 in of water, about 3.0 in of snow.

The gauge then measured 0.24 in of water overnight and the depth sensor rose 2 in.

## Where to ski first

For the next two mornings, each zone gets the snow falling between 4 pm closing and first chair, then a score.

| Factor | How it enters | Weight |
|---|---|---|
| New snow since closing | Likely amount; the high end when under half an inch is likely anywhere | Base |
| Snow quality | Lighter snow scores higher | Up to 25% either way |
| Wind | Share of the zone scoured by a tenth or more | Up to half off |
| Lift | Wind hold possible or likely on the zone's lift | 15% or 50% off |

- **The weights are my choices.** Nothing records where the skiing was best, so they cannot be fitted or tested.
- **The ranking mostly follows elevation.** In the two forecasts built so far, scour reaches at most 1% of any zone, so depth and lift holds decide the order.

## Analog finder

```bash
uv run vailsnow analogs 2020-02-06
```

- It compares a forecast day with every winter forecast day since October 2018 and names the closest, with the storm each belonged to.
- **It grades the match.** For February 6, 2020 it says nothing on record is close; the nearest day is January 14, 2024.
- **Analogs are context, not a forecast.** The average of the five closest days misses by 0.077 in, against 0.063 for the model and 0.068 for raw HRRR.
- It cannot reach back to January 2014: forecasts before July 2018 were never pulled.

## Config-driven resorts

- Map view, headline zone, alert and lift thresholds and the wind-check station moved into `resorts/vail.toml`. The last hard-coded Vail values in the live code are gone.
- `uv run vailsnow check-resort <id>` lists what a resort has and what is missing, step by step.
- **No second resort has been built.** `ADDING_A_RESORT.md` lists what blocks one: terrain inputs for mountains outside the ski game, an OpenStreetMap extract, and a full refit to the new gauge.

## Not done

- Deployment and scheduling. The scorecard is "public" only once the map is.
- The phone push channel is untested.
- Lift-hold thresholds and the first-chair weights are unverified and will stay so without a record of holds or of conditions.
- The evening run does not rebuild the map; the map still updates once a day, around 10 am.
- The alert backtest covers the morning forecast, not the evening one.
- Carried over: your review of the Phase 1 zones, the January 2014 case study, forecasts before July 2018.

## Built

| Piece | Where |
|---|---|
| Forecast log, scoring, storms, season stats | `extras/scorecard.py` |
| Ridge-gust check, lift exposure, hold odds | `extras/liftwind.py`, `fcst/gust.py`, `obs/asos.py` |
| Evening and 5 am alerts, delivery, threshold backtest | `extras/alerts.py` |
| First-chair ranking | `extras/firstchair.py` |
| Analog finder and its skill check | `extras/analogs.py` |
| Resort checklist | `extras/doctor.py`, `ADDING_A_RESORT.md` |
| Models for the evening run (tonight, tomorrow) | `model/final.py` |
| Plan and Score tabs, lift colouring, alert banner | `web/app/` |
| Tests | 173 pass (27 new) |

## To keep it running

1. Back up `data/log/forecasts.jsonl`. It is the only copy of the forecast log and it is not replaceable.
2. Run `uv run vailsnow forecast` each morning after 10 am, or install the launchd jobs once you are happy with them.
3. Set your thresholds in `resorts/vail.toml`, and try them with `uv run vailsnow alert-backtest --likely 4 --high 6`.
