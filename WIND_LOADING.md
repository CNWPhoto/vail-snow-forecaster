# Wind loading

Date: 2026-11-24. Tuned to one skier's account of the mountain. Nothing public measures it.

> **Later on 2026-11-24:** at the user's request the wind-loaded field became the forecast itself for days 1 to 3. The separate "As fallen" and "After wind" layers are gone; the map's "New snow" layer, zone cards, point forecasts and the 10-day view's first three days all include wind loading. Where this page says "After wind layer", read "New snow".

## What it does

The **After wind** layer now puts more snow on slopes just over a ridge from the wind and less on the exposed side, storm by storm, using that day's ridge wind direction and strength.

- **Typical storm wind:** open east- and north-facing slopes within 100 m of a ridge get 12.5 to 13.2 in where the base amount is 10. The windward side of the same ridges gets 7.8 to 9.0.
- **Named spots, base of 10 in:** Northwoods 11.7 to 14.1, Kangaroo Cornice 11.8 to 12.1, the top of Lover's Leap 12.2 to 12.4.
- **Stronger wind, lighter snow:** up to 1.6 times that effect. Calm, or wet sticky snow: little or none.
- **The total on the mountain is unchanged.** What the lee gains, the exposed side loses.
- **Trees** stop the stripping entirely and halve the loading.

In the February 14 to 17, 2025 replay (ridge wind from the west-southwest, then west, then southwest):

| Zone | As fallen | After wind | Most of the zone |
|---|---|---|---|
| Northwoods | 18.6 in | 22.0 | 15 to 29 |
| Eagle's Nest / Avanti | 15.0 | 18.0 | 15 to 22 |
| Sun Down Bowl | 19.8 | 21.2 | 16 to 27 |
| Mid-Vail / Mountaintop | 19.0 | 19.5 | 16 to 25 |
| China Bowl | 21.3 | 20.2 | 16 to 26 |
| Outer Mongolia Bowl | 20.7 | 18.1 | 16 to 19 |
| Game Creek Bowl | 18.2 | 16.5 | 13 to 19 |

## What it was tuned to

The user's account, 2026-11-24:

- Northwoods and Kangaroo Cornice catch far more than the other side of their ridges on most storms.
- The cornice east of the middle Blue Sky Basin lift loads heavily, and so does the ridge under the Tea Cup lift in China Bowl.
- Any slope over a ridge toward the east, and regularly the north, holds about 30% more until the slope below eases toward flat.
- "If the mountain reports 10 in, those spots would have about 13."

## What the data added

- **Ridge wind direction.** At the 12,075 ft summit station the wind blows from about 35 degrees further anticlockwise than the 600 mb wind (17,543 windy hours). On storm days that makes the ridge wind southwest 47% of the time and west 31%, which is why east- and north-facing slopes load "on most storms".
- **The terrain agrees with the account.** For southwest and west ridge winds, the shelter angle marks Northwoods, Kangaroo Cornice and Lover's Leap as lee slopes.

## How it works

For each 10 m cell: how sheltered or exposed it is to the day's ridge wind (the shelter angle from Phase 1), times how close it is to a ridge line (fading over about 150 m), times the wind's strength, scaled down toward the base of the mountain. The older step that moves fallen snow a short distance is applied afterward.

## Limits

- **Tuned to two general statements and four places, by one person.** It reproduces them. Whether it is right elsewhere is unknown: zone averages now move by up to about 20%, which is a strong claim for places nobody has checked.
- **Scored numbers do not include it.** The scorecard is at the gauge, which wind loading leaves alone. Alerts use the headline zone before wind.
- **Days 4 to 10 have no wind detail.**
- **The ridge lines come from the terrain analysis**, and it marks many minor ridges. Loading may be spread too widely on broken ground.
- **A bug, now fixed:** from the Phase 6 work earlier on 2026-11-24 until this change, the After wind layer was an exact copy of As fallen. One line compared two arrays the wrong way. No scored number used that layer.

## Where

`model/exposure.py` (`lee_strength`, `lee_factor`, `lee_load`), `model/zones.py` (`ridge_direction`), applied in `live/forecast.py`.
