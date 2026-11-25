# Vail Pinpoint Snow Forecaster: project summary

Written 2026-11-24, four days after the project began from a one-page brief. This is my own account: what stands out, what you steered, what I think is good, what I would fix next.

## What it is

A snowfall forecast for Vail Mountain that works at the scale of a zone or a single slope. It takes free public weather-model output, corrects it against eight winters of what the mid-mountain SNOTEL gauge measured, spreads it across 17 zones on a 30 m grid, loads it onto the lee side of ridges, and shows it on a map with a 10-day outlook. Every forecast is logged before the answer is known and scored afterward.

| | |
|---|---|
| Code | About 8,800 lines of Python, 840 of front end, 198 tests |
| Data | 58 GB, all free and public: SNOTEL, HRRR, GFS, GEFS, National Blend, NWS grids, ERA5, PRISM, SNODAS, USGS lidar, OpenStreetMap, airport stations |
| Runs | Unattended on GitHub Actions, free for a public repository: map once a day, 10-day outlook five times, alerts at 9:45 pm and 5:05 am. Nothing runs at home |
| Live | https://cnwphoto.github.io/vail-snow-forecaster/ and, once the pull request is merged, build 005 on purposebuilt.studio |
| Not done | Alerts go to the map banner and a log, not a phone |

## The most striking details

**It ties the National Weather Service's best blend, on a laptop.** Tested on winters it never saw, the day-ahead forecast misses by 0.93 in of snow at mid-mountain. The National Blend of Models misses by 0.92, the official NWS forecast by 0.99, raw HRRR by 1.00. At three days it is ahead of all three (1.10 against 1.16). That is a tie, not a win, and the write-ups say so.

**The gauge was lying about when it snowed.** The SNOTEL gauge rises a tenth of an inch at a time, and the tick often lands a day or more after the storm. On 211 of 294 single-tick days the depth sensor beside it showed no new snow. The model had learned from this that a bone-dry day carries a 1-in-4 chance of light snow. You spotted the symptom; the cause was in the instrument.

**The first big finding was wrong for a day because of a date label.** The SNOTEL service labels each daily value with the end of the day unless asked otherwise. Every date in the storm catalogue was off by one. Fixing it overturned the Phase 2 headline: the biggest storms come on west and northwest flow, not southwest.

**The wind at the ridge is not the wind in the forecast.** At a 12,075 ft summit station the wind blows at 0.47 of the upper-level speed and from 35 degrees further southwest. A "northwest flow" storm is a west wind where the snow lands. That is why Northwoods and Kangaroo Cornice load on most storms, as you said they do.

**The forecasts people trust run high far out.** When the National Blend called for 0.30 to 0.45 in of water four to six days ahead, the gauge averaged 0.27, and nothing fell on 23% of those days. Adding the Blend to this model bought one point of skill at day 4 and nothing beyond.

**Skill has a visible edge.** Against simply quoting the average for the date, the forecast is 27% better at day 4, 16% at day 7 and 8% at day 10. The app shows that fade next to each day.

**Textbook physics lost.** I built the standard model of air lifted over terrain to place snow across the mountain. It added no skill at any station and was left out. A long-term precipitation map did the job better.

## What you guided me to fix

Most of the important corrections started with something you noticed.

| You said | What it led to |
|---|---|
| Stay inside legal lines, free data only, don't copy models | No automated reading of CAIC or Vail's site; the scorecard is against SNOTEL only; nothing paid |
| "It's forecasting snow every single day" | The gauge-tick discovery, a corrected record and every model refitted |
| "I assume these are liquid" | Snow in inches leads everywhere; water is a footnote |
| "It shows 0 when it really looks like a weather event" | Far-out days show the expected amount and a chance of snow |
| "Other models are predicting 3 to 7 in" | The Blend's newest run added to the long-range model, and the evidence for why this forecast still sits lower |
| Northwoods, Kangaroo Cornice, the Blue Sky cornice, the Tea Cup ridge; "10 in reported means about 13 there" | Wind loading rebuilt and tuned to your account, and a bug found: the wind layer had been a copy of the calm one |
| "Does it show windward and leeward?" | The check that found the ridge wind direction was 35 degrees off |
| "Is there really a chance of 0?" | A direct count: about 1 day in 4 on forecasts like Saturday's |
| Use a range, to the half inch; make wind-loaded the default | Ranges lead every figure; "As fallen" is gone |
| Drop "0 to X"; show the range expected, and let it fall to 0 when unlikely | Far-out days read "2.5 to 5.5 in if it snows, 64% chance", or 0 |
| Why doesn't the 10-day show wind loading? | Lee loading extended to days 4 to 10 from the ensemble's wind |
| Add a 10-day, put it on the map, tap a day, list days at a point | The 10-day outlook, day buttons and the point table |
| Add base depth | A depth layer from SNODAS tied to the SNOTEL sensor |
| The Plan tab isn't helpful; the February replay is confusing | Both removed |
| Make sure data doesn't pile up | A daily clean-up; growth cut from about 9 GB a year to half a gigabyte |

You also made three calls that shaped the build: dropping the depth logger, using the public 1.5 degree ERA5 copy, and keeping the 48 GB history.

## What I think is best about it

- **It was tested the hard way.** Every score comes from a winter the model never saw, and each result is shown beside the raw models and the Blend, including where it loses.
- **It says what it doesn't know.** Zone differences, wind loading and base depth are labelled as modelled or tuned to your account, not measured. The Score tab counts the misses in public view.
- **The errors got found.** The date labels, a file-reading bug that mirrored every other row of the Blend's grids, the gauge ticks, and the dead wind layer were each caught by a check that didn't add up, then fixed and written down. I also predicted that adding the Blend at short range would be the cheapest gain available. It wasn't, and the summary says that.
- **Your knowledge is in the model.** The wind loading reproduces the places you named, and the data independently explained why they load.

## What I would improve

1. **Big days are under-called.** On the 60 biggest days in the record, the likely number missed by about 5 in; raw HRRR missed by less. This is the weakness that matters most on a powder morning, and nothing I tried fixed it.
2. **Nothing measures snow by zone.** Every difference between zones rests on a long-term map and on wind loading tuned to one person. A few dozen depth readings a season, from you or a stake camera you check by hand, would turn that from a claim into something scored.
3. **Wind loading is tuned to four spots.** It now moves whole-zone averages by up to 20%. More places you know, especially ones that get stripped, would show whether that is right.
4. **The 24, 48 and 72 hour views can still read "0 to X"** when a storm inside three days is uncertain. The 10-day view no longer does: a day with a fair chance shows the range to expect if it snows, and a day with a low chance reads 0.
5. **The map updates once a day.** An evening rebuild from the 5 pm model run is built for alerts but not for the map.
6. **The European ensemble isn't in it.** It is free and would likely help most at days 6 to 10.
7. **A failed scheduled run is easy to miss.** GitHub emails the repository owner when a job fails, and the page shows a stale time. GitHub also pauses schedules in a public repository after 60 days without activity; I have not confirmed that the jobs' own commits count as activity.
8. **The zone boundaries are still my draft.** Every card and outline uses them, and you haven't reviewed them.

## Other thoughts

- **The honest pitch is not "more accurate than the pros".** It is as accurate as the best public blend at the one place that can be checked, and it goes further than they do in three ways: slope-scale detail, your local knowledge, and a public record of its own misses.
- **This season is the real test.** Only one forecast so far was logged on the day; the rest were rebuilt afterward. By spring the scorecard will say whether the held-out numbers hold up.
- **It is public now, and a few things matter.** The page, the repository and the site entry each say it is not affiliated with Vail Resorts, is not an official forecast and is not avalanche advice. The data credits are kept. It should stay non-commercial, since that is the basis several of the sources and decisions rest on.
- **The most useful thing you did was distrust it.** Each time a number looked wrong to you, something real was behind it.
