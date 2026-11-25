# Phase 0: feasibility report

Date: 2026-11-21. Companion to `DATA_SOURCES.md`, which holds the endpoint-by-endpoint record.
Later notes: the SNOTEL daily readings in the storm table below are end-of-day values (found 2026-11-24), so each row's date range is the days the snow fell only if read as "gain between the reading dated the day before and this one". `STORM_CLIMATOLOGY.md` has the corrected storm dates and totals.
No model code has been written. This is the checkpoint the brief asks for.

## Verdict

The project is feasible, with one claim that has to be scaled back.

- **A verified mountain-scale forecast is achievable.** SNOTEL 842 gives 48 seasons of daily snow water at mid-mountain, and every forecast model in the brief has a public archive.
- **Verified zone-level precision is not achievable from existing data.** Only two places on the mountain measure snow, both on the Front Side. Zone differences will come from physics and terrain, shown as ranges, until your logger builds a record.
- **The verification plan needs trimming.** The HRRR archive cannot support 48 h or 72 h scoring for most of its life, and RRFS has no history at all.

## What the data can and cannot verify

### Snow is measured in two places on the mountain

| Place | Elevation | Measures snow? | History available today |
|---|---|---|---|
| Mid-mountain (SNOTEL 842 and Vail's `CAVMM`, same spot) | 10,290 ft | Yes | SNOTEL: yes, from 1978. `CAVMM`: no |
| Patrol HQ, `CAVPQ` | 11,248 ft | Yes (snow height, precipitation) | No |
| Blue Sky Basin, `CAVBS` | 11,109 ft | No (wind, temperature only) | No |
| China Bowl, `CAVCB` | 11,031 ft | No (wind, temperature only) | No |

Off the mountain: Vail Pass CDOT yard (`CAVLP`, snow height, no history yet), the town COOP station, and CoCoRaHS observers in East and West Vail.

So the brief's "about five observation points" is, for snow, two on-mountain points plus the valley floor and the pass.
There is no snow measurement anywhere in the Back Bowls or Blue Sky Basin.

### Your three beliefs: what can be tested

| Belief | Testable with existing data? | How |
|---|---|---|
| Top gets more than the bottom | Yes | Mid-mountain SNOTEL against the town COOP and CoCoRaHS (1985 and 2005 onward). Adding Patrol HQ needs its history |
| East beats west | Partly | At valley level: East Vail (`Vail 2.6 E`, 2005 on) against West Vail (`Vail 0.9 WNW`, 2008 on). At regional scale: Copper and Vail Pass against Beaver Creek and McCoy Park. Not on the ski terrain itself |
| Northwoods beats Lionshead | **No** | No snow measurement exists on either side. Only model patterns (PRISM 800 m, SNODAS 1 km) and your own logged depths can speak to it |

### The first data pulled already shows the flow-direction signal

Daily snow water gained at three SNOTEL sites during the two benchmark storms:

| Storm | Vail Mountain (10,290 ft) | Copper (10,500 ft) | Beaver Ck Village (8,530 ft) |
|---|---|---|---|
| Feb 5 to 8, 2020 (northwest flow) | 2.9 in water, depth 44 to 60 in | 2.6 in, depth 37 to 62 in | 1.1 in, depth 36 to 46 in |
| Jan 29 to 31, 2014 | 2.0 in water, depth 39 to 60 in | 1.8 in, depth 43 to 61 in | 2.3 in, depth 28 to 51 in |

Vail took about 2.6 times Beaver Creek's water in the 2020 storm and slightly less than Beaver Creek in 2014, which matches the regime behaviour in the brief.
The elevations differ by 1,760 ft, so this is a diagnostic, not a clean comparison.
It also shows SNOTEL depth change (16 in net in 2020) sits far below the resort's reported 38 in, because snow settles while it falls and these are start-of-day depth readings, not cleared-board totals.

## Limits that change the plan

### 1. HRRR lead times

| Lead time | HRRR forecasts as issued exist from | Seasons available |
|---|---|---|
| 12 h | Dec 2014 (surface files are missing in Aug 2014) | 12 |
| 18 h | Aug 2016 | 10 |
| 24 and 36 h | Aug 2018 | 8 |
| 48 h | Dec 2020 | 6 |
| 72 h | Never | 0 |

Start dates are from spot checks of the archive, so each is good to within a few months. The 36 h and 48 h runs are the 00, 06, 12 and 18z cycles only.

Longer leads must come from other models: GEFS (2017 on), GFS (2021 on), NBM (May 2020 on), ECMWF (Jan 2023 on).
The baselines the brief names limit the fair comparison window further: NBM and the NWS official grids both start in 2020.

Recommended verification matrix:

- 12 and 24 h: HRRR era, leave-one-season-out over 8 to 12 seasons.
- 48 h: six seasons (2020-21 on), against NBM and NWS.
- 72 h: GEFS and GFS based, six to nine seasons, labelled as coarser-model skill.

### 2. January 2014 predates the HRRR archive

The archive starts 2014-07-30. The Jan 30-31, 2014 case study can still be done with the GEFSv12 reforecast (11 members on 2014-01-29, confirmed) plus ERA5.
It will be a different model stack from the Feb 2020 case, and the page should say so.

### 3. RRFS is live but has no archive

RRFS is operational to 84 h and NAM is gone, as the brief expected. NOMADS keeps two days and I found no operational archive on AWS.
RRFS cannot be bias-corrected this season. Start saving the cropped RRFS files now so next season has a training record.

### 4. On-mountain station history is the largest open gap

The five Vail-area stations are live, but I found no permitted source for their past data:

- NWS API lists them and returns nothing.
- Synoptic needs a token and limits free access to `.edu` users.
- CAIC's public API gives metadata only, and I could not read its terms.
- MADIS has an account tier I have not applied for.

Four of the five belong to Vail Resorts, so republishing their values on a public scorecard is a separate question from reading them.
Without them, the model is trained and scored against SNOTEL 842 alone on the mountain, and Patrol HQ's elevation signal is lost.

### 5. SNOTEL's own limits

- Snow depth starts in 2006, so snow-density work has 20 seasons, not 48.
- Water is recorded to 0.1 in, about 1 to 1.5 in of snow. Small events and hourly rates are coarse.
- The storm catalogue of the top 200 events over 48 seasons is feasible from daily water increments. ERA5 covers all of them; HRRR covers those from late 2014.

### 6. No lidar snow-depth map of Vail was found

The state's 2026 airborne survey list does not include the Eagle River basin. It does include the Blue River above Dillon, which may cover Copper and the east side of Vail Pass.
Worth one more check in Phase 2, but plan on having no measured snow-distribution map for Vail.

### 7. Snow-density model

The Utah group's random forest is public with trained model files, but the repository has no licence, so it grants no reuse rights. It is not used (see the ground rules below).
We train our own on local data. I could not read either paper's full text, so their site list and predictor details are unconfirmed.

## What is ready to reuse

The ski game at `~/Games/Copper Ski Resort Game` covers Phase 1's inputs almost completely:

- 2 m lidar height grid over the whole resort, East Vail and Minturn, in UTM 13N.
- Measured canopy cover and height at 2 m from the lidar point cloud. This is better than the NLCD or LANDFIRE layers the brief suggests.
- OpenStreetMap lifts (all chairs and gondolas, named), 356 piste ways and the ski-area boundary, fetched a week ago.
- A 30 m regional DEM covering Beaver Creek, Copper, Vail Pass and Fremont Pass.

Gaps to fill in Phase 1:

- Vail Pass CDOT yard is about 1.4 km east of the 2 m grid. The 30 m grid covers it.
- The regional DEM stops short of Elliot Ridge and the box in the brief. Extending it from the same USGS service is a small download.
- OpenStreetMap has no polygons for the bowls or zones. Zone outlines have to be drawn from lift lines, ridgelines and drainage divides, then checked by you.

I will read these files in place and not modify the game project.

## Proposed adjustments to the brief

1. **Compute terrain on a 10 m grid, publish on 30 m.** The 2 m data makes this free, and wind-shelter terms are sensitive to resolution.
2. **State zone numbers as "mountain total x terrain factor" with a range**, and label the factor as physics-derived until logger data exists.
3. **Use the ARCO ERA5 copy** (no account) instead of the Copernicus download service.
4. **Add PRISM 800 m daily grids** as a second spatial reference next to SNODAS. Both are model products, not truth.
5. **Add the East Vail and West Vail CoCoRaHS pair** as the east-west test.
6. **Thin the HRRR pull.** GRIB fields are whole-CONUS and must be fetched before cropping. A full hourly pull of every cycle would be several terabytes of transfer; storm days plus a sample of quiet days at 00z and 12z keeps it to a pilot-sized job. I will measure the real cost on one month before committing.
7. **Pin Python 3.12 with uv.** This machine defaults to 3.14, where GRIB and LightGBM wheels are a risk. `uv`, GDAL and eccodes are not installed yet.

## Ground rules (set 2026-11-21)

This is a hobby project. Reading public data is fine; nothing is paid for; nobody else's model or code is copied.

What that settles:

- **Snow-density model.** The Utah repository is not used, run or copied. We train our own on SNOTEL and CoCoRaHS, using methods described in published papers.
- **Synoptic.** Dropped (paid).
- **Vail Resorts and CAIC stations.** Not used. Their history has no free and permitted source. I fetched station metadata from CAIC's site back end once during Phase 0, before reading its terms, which prohibit that. It will not be called again without written permission. The same five stations also appear in the public NWS API.
- **Hand-logged depths.** Dropped (decided 2026-11-21). Zone-to-zone differences therefore stay physics-derived for good, with nothing on the ski terrain to calibrate them except the sources below.
- **Vail snow report and stake cameras.** Terms read 2026-11-21. Vail Resorts' website terms allow personal, non-commercial use and forbid copying or posting the content on another website. So they can be read for private checking of the forecast, and their numbers and images must not appear on a public scorecard or map. They only exist going forward: there is no free archive, so they cannot be used for hindcasts.
- **Colorado Avalanche Information Center.** Terms read 2026-11-21. They prohibit robots and data mining of the site's information and images, say the site is for use by people, and require written permission to reproduce material. Automated use is out unless CAIC grants permission in writing.
- **The project stays non-commercial.** Open-Meteo's free tier requires it.

Sources the build will rely on, and what each asks for:

| Source | Terms | What we owe |
|---|---|---|
| NOAA and NWS (HRRR, RRFS, GFS, GEFS, NBM, NDFD, forecast text), NRCS SNOTEL, USGS terrain and lidar | US Government, public domain | Nothing. Credit is good practice |
| ERA5, ECMWF open data | CC BY 4.0 | Credit |
| CoCoRaHS | CC BY 4.0 | Credit |
| PRISM | Free to reproduce and distribute | Name, URL and access date |
| OpenStreetMap | ODbL | Credit, and share-alike if the map data itself is republished |
| Open-Meteo free API | CC BY 4.0, non-commercial, under 10,000 calls a day | Credit; stay non-commercial |
| SNODAS | NOAA product at NSIDC | Citation. Licence wording still to be read before any public use |

Using the output of public weather models is not copying a model: those files are published for reuse.
The physics we build on (linear orographic precipitation theory, wind-shelter terrain indices, wet-bulb snow level) comes from published papers, and methods are free to reimplement; only someone's code or trained weights would need a licence.
This is a careful reading of the published terms, not legal advice.

## Decisions I still need from you

1. **Verification matrix.** Accept the reduced lead-time matrix above?
2. **Where it runs.** Is this machine the Mac mini, and what is the NAS path for the raw cache? 429 GB is free locally.
3. **Optional, free:** a NOAA MADIS research account might expose the on-mountain stations. You would have to apply yourself, and the values could still not be republished. Skipping it is fine.

## Phase 1 plan, once approved

1. Set up the project (uv, Python 3.12, tests, `.env` handling, cache layout).
2. Build the 10 m terrain grid from the game's lidar: slope, aspect, curvature, position index, ridge distance, canopy, sky view, and wind shelter for 16 directions.
3. Extend the regional DEM to the full box.
4. Draft zone polygons with elevation bands and render a map for you to correct.
5. Tests for the terrain code, and a short written summary.
