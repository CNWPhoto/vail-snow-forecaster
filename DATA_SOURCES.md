# Data sources

Every row was tested on **2026-11-21** from this machine, except rows marked "Phase 3", which were tested on 2026-11-24. Raw responses from Phase 0 are in `phase0/probes/`.
Status: **OK** = endpoint answered with usable data today. **Partial** = works with a limit described in the notes.
**Blocked** = cannot be used as things stand. **Not tested** = deliberately not touched (terms) or not reached.

"Redistribution" means: may the public scorecard and map republish values derived from this source.

## 1. Observations

| Source | URL tested | Coverage | Resolution | Licence | Redistribution | Status |
|---|---|---|---|---|---|---|
| NRCS SNOTEL, AWDB REST API | `https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/{stations,data}` | Site-dependent, see below | Daily from start of record; hourly in recent years. SWE and precip to 0.1 in, depth to 1 in | US Government, public domain | Yes | OK. **Daily values are end-of-day unless the request says `periodRef=START`.** Phases 0 to 2 missed this and dated every gain one day early; fixed in Phase 3. Hourly times are Pacific Standard Time |
| NRCS Report Generator (CSV) | `https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/customSingleStationReport/daily/842:CO:SNTL...` | Same as above | Daily | Public domain | Yes | OK (same data, CSV; keep as fallback) |
| CAIC weather-station API (metadata) | `https://api.avalanche.state.co.us/api/v2/weather_stations[/ID]` | Live station list; last-report time | Station metadata and sensor list only | Terms read 2026-11-21 (`avalanche.state.co.us/terms-use`): robots and data mining of the site's information and images are **prohibited**; reproduction needs CAIC's written permission | No, without written permission | **Blocked by terms.** Called once in Phase 0 before the terms were read; not to be called again without permission |
| NWS API, station observations | `https://api.weather.gov/stations/{id}/observations` | About the last 7 days | Sub-hourly to hourly | Public domain | Yes | Partial: lists all five CAIC Vail stations but returns **zero observations** for them. Works for `CO070`, `VLMC2` |
| Synoptic (MesoWest) API | `https://api.synopticdata.com/v2/stations/metadata` | Long history for mesonets | Sub-hourly | Commercial. Free tier is for `.edu` users only per Synoptic's current pages | No (terms forbid passing data on) | Blocked: 401 without a token |
| NOAA MADIS public surface feed | `https://madis-data.ncep.noaa.gov/madisPublic1/...` | Rolling recent; archive needs an account | Sub-hourly | Public tier is open; some providers are restricted | Depends on provider | Partial: endpoint answers, but my area query was not honoured, so whether CAIC stations are in the public tier is **not established** |
| Iowa Environmental Mesonet, CDOT RWIS | `https://mesonet.agron.iastate.edu/geojson/network/CO_RWIS.geojson`, `/api/1/currents.json?network=CO_RWIS` | `CO070` and `CO092` from 2003-06-25; `CO160`, `CO172` from 2018-12-19 | About 10 to 20 min. Wind, temperature, humidity. **No snow depth** | Public | Yes | OK. Note: IEM's names for some CO RWIS sites are wrong (`CO172` shows as "US-40 @ Red Dirt Hill"). Use NWS/CAIC names and coordinates |
| IEM text archive, GJT forecast discussions | `https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=AFDGJT&sdate=..&edate=..` | Found for 2001, 2005, 2010, 2014, 2020, today | Each issued product | Public domain (NWS text) | Yes | OK |
| IEM Local Storm Reports | `https://mesonet.agron.iastate.edu/geojson/lsr.geojson?sts=..&ets=..` | Back to at least 2014 | Per report | Public domain | Yes | Partial: works, but the office filter I passed was ignored (national result). 190 GJT reports for Feb 4-8 2020. Fix the parameter in Phase 2 |
| NWS GJT event summaries | `https://www.weather.gov/gjt/events` | 22 winter-storm write-ups, Nov 2016 to Feb 2021, plus a Dec 2022 squall | Narrative pages | Public domain | Yes | OK. The Feb 4-7 2020 summary is there |
| CoCoRaHS export API | `https://data.cocorahs.org/export/exportreports.aspx?ReportType=Daily&Format=CSV&State=CO&County=EG...` | 2003 onward in Eagle County | Daily, manual. New snow, SWE of new snow, total depth | CC BY 4.0 (stated on their data-usage page) | Yes, with credit | OK |
| NWS COOP and CoCoRaHS via RCC-ACIS | `https://data.rcc-acis.org/{StnMeta,StnData}` | Vail COOP `058575`: 1985-02 to present. See list below | Daily | Public | Yes | OK |
| Vail snow report and snow-stake cameras | not accessed | Current conditions only; no free archive | Daily report; camera images | Vail Resorts website terms read 2026-11-21 on `snow.com/policies?tc_1=4` (the `vail.com` copy could not be read, so assumed the same): personal, non-commercial use, one copy on one computer; no copying, posting or distributing the content on another website. No clause on automated access was found | **No.** Private use only | Not accessed yet. Usable for private checking going forward, not for anything published |
| Resort report pages, OpenSnow, OnTheSnow | not accessed | n/a | n/a | Terms prohibit scraping | No | Not tested, by instruction |

### SNOTEL sites (IDs resolved from the API)

| Site | Triplet | Elevation | Lat, lon | Record starts | Distance from Vail PHQ |
|---|---|---|---|---|---|
| Vail Mountain | `842:CO:SNTL` | 10,290 ft | 39.61765, -106.38019 | 1978-10-01 | 2.4 km |
| Beaver Ck Village | `1041:CO:SNTL` | 8,530 ft | 39.59871, -106.51113 | 2002-11-01 | 13.3 km |
| McCoy Park | `1040:CO:SNTL` | 9,500 ft | 39.60231, -106.54400 | 2002-10-01 | 16.1 km |
| Copper Mountain | `415:CO:SNTL` | 10,500 ft | 39.48917, -106.17154 | 1978-10-01 | 20.5 km |
| Summit Ranch (extra) | `802:CO:SNTL` | 9,350 ft | 39.71803, -106.15770 | 1979-10-01 | 21.1 km |
| Fremont Pass | `485:CO:SNTL` | 11,310 ft | 39.38014, -106.19784 | 1979-10-01 | 28.5 km |
| Elliot Ridge | `1120:CO:SNTL` | 10,550 ft | 39.86380, -106.42473 | 2009-08-07 | 29.3 km |

Facts about site 842 that shape the project:

- SWE and precipitation go back to 1978-10-01. **Snow depth only starts 2006-07-14.**
- Hourly data: 5 values in a 2-day window in Jan 1990, 17 in Jan 2000 and Jan 2006, full hourly with depth by Jan 2014. I have not pinned down the exact changeover dates.
- Elevations and coordinates from the API differ slightly from the brief (10,290 ft matches; Beaver Ck Village is 8,530 ft, McCoy Park 9,500 ft, Elliot Ridge 10,550 ft).

### On-mountain and pass stations (all found; two more than the brief listed)

| ID | Name | Owner | Elevation | Lat, lon | Sensors (per CAIC API) |
|---|---|---|---|---|---|
| `CAVPQ` | Vail SA, PHQ | Vail Resorts | 11,248 ft | 39.60548, -106.35657 | temp, dew point, RH, wind, **snow height, precipitation** |
| `CAVMM` | Vail SA, Mid-Mtn | Vail Resorts | 10,303 ft | 39.61761, -106.38006 | temp, dew point, RH, wind, **snow height, 24 h new snow** |
| `CAVBS` | Vail SA, Blue Sky | Vail Resorts | 11,109 ft | 39.57106, -106.33239 | temp, dew point, RH, wind (no snow sensor) |
| `CAVCB` | Vail SA, China Bowl | Vail Resorts | 11,031 ft | 39.59772, -106.33835 | temp, dew point, RH, wind (no snow sensor) |
| `CAVLP` | Vail Pass, CDOT Yard | CAIC | 10,250 ft | 39.57861, -106.24722 | temp, dew point, RH, wind, **snow height** |
| `CO070` | Vail Pass summit RWIS | CDOT | 10,614 ft | 39.52958, -106.21687 | temp, RH, wind |
| `CO172` | 2.6 mi W of Vail Pass summit | CDOT | 10,234 ft | 39.56594, -106.23739 | temp, RH, wind |
| `CO160` | East Vail, I-70 | CDOT | 8,776 ft | 39.62122, -106.27850 | temp, RH, wind |
| `CO092` | Dowd Junction | CDOT | 7,776 ft | 39.60802, -106.44914 | temp, RH, wind |

All five `CA*` stations reported within the hour on 2026-11-21 according to CAIC's API. **I have not found a permitted source for their history.**
`CAVMM` and SNOTEL 842 sit at the same spot (the coordinates agree to about 10 m).

### Valley observers (daily, manual)

| Station | ACIS id | Elevation | Record | Where |
|---|---|---|---|---|
| Vail COOP | `058575` | 8,297 ft | 1985-02 to present | Town of Vail |
| Vail 2.6 E | `US1COEG0019` | 8,389 ft | 2005-05 to present | East Vail |
| Vail 2.1 ENE | `US1COEG0071` | 8,359 ft | 2023-09 to present | East Vail |
| Vail 3.5 E | `US1COEG0053` | 8,507 ft | 2019-04 to 2023-12 | East Vail |
| Vail 0.9 WNW | `US1COEG0021` | 8,172 ft | 2008-07 to present | West of Lionshead |
| Avon 1.1 SE | `US1COEG0031` | 7,603 ft | 2011-12 to present | Below Beaver Creek |
| Edwards 1.3 WNW | `US1COEG0003` | 7,172 ft | 2003-09 to present | Down-valley |

## 2. Gridded history

| Source | URL tested | Coverage | Resolution | Licence | Redistribution | Status |
|---|---|---|---|---|---|---|
| HRRR archive | `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.YYYYMMDD/conus/` | 2014-07-30 to today. Every day present in the 7 months I sampled between 2014-08 and 2016-08 | 3 km, hourly, `sfc` and `prs` | Public domain (NOAA Open Data) | Yes | Partial, see lead-time table below |
| HRRR, chunked copy (University of Utah, on AWS Open Data) **in use, Phase 3** | `https://hrrrzarr.s3.amazonaws.com/sfc/YYYYMMDD/YYYYMMDD_HHz_fcst.zarr/{level}/{field}/{level}/{field}/0.3.4` | Analyses from 2016-08-23; **forecast runs from 2018-07-13** to today. 36 h runs at 00/06/12/18z until December 2020, 48 h after | 3 km, hourly. Each field is stored in 150 x 150 cell chunks holding every forecast hour, so the whole Vail region is one request of 0.2 to 1.7 MB per field | Public domain (NOAA data, reformatted) | Yes | OK. Chunk row 3, column 4 holds the project box. 2,880 of 2,940 runs fetched; 60 are missing from the copy, mostly November 2019 and November 2020. `APCP_acc_fcst` lacks the first two hours, so the hourly field is summed instead. `ASNOW` has no time axis before 2021 |
| GEFSv12 reforecast | `https://noaa-gefs-retrospective.s3.amazonaws.com/GEFSv12/reforecast/YYYY/YYYYMMDD00/` | 2000 to 2019, 00z daily | 0.25 deg to day 10. 2014-01-29 has c00 plus p01 to p10 | Public domain | Yes | OK. **Only as-issued-style forecasts available for the Jan 2014 storm** |
| GEFS operational | `https://noaa-gefs-pds.s3.amazonaws.com/gefs.YYYYMMDD/` | 2017-01-01 to today | 0.5 deg in 2020 (`pgrb2a/b`) | Public domain | Yes | OK. **In use, Phase 4**: ensemble mean and standard deviation of six-hour precipitation, `.../HH/atmos/pgrb2ap5/{geavg,gespr}.tHHz.pgrb2a.0p50.fFFF`. These two files only exist from the September 2020 upgrade; individual members were not pulled |
| GFS operational | `https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.YYYYMMDD/` | **2021-01-01** to today | 0.25 deg | Public domain | Yes | OK, **in use, Phase 3** (12z runs). Files sit under `gfs.YYYYMMDD/HH/` until 2021-03-22 and under `.../HH/atmos/` after |
| National Blend of Models | `https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.YYYYMMDD/HH/{core,qmd}/` | **2020-05-18** to today. `qmd` percentiles present by 2020-12-15 | 2.5 km CONUS | Public domain | Yes | OK, **in use, Phase 3** (`core` files: six-hour `APCP` and `ASNOW`, plus `SNOWLR` and `SNOWLVL` from 2023). The grid stores every second row right to left; see the note below. Percentiles (`qmd`) not pulled yet |
| NDFD (NWS official forecast grids) | `https://noaa-ndfd-pds.s3.amazonaws.com/wmo/snow/YYYY/` | **2020** to 2026 | 2.5 km | Public domain | Yes | OK, **in use, Phase 3**. This is the "NWS point forecast" baseline. CONUS files are `wmo/qpf/YYYY/MM/DD/YIUZ98_KWBN_<stamp>` and `wmo/snow/.../YSUZ98_KWBN_<stamp>`, issued twice an hour; the one stamped about 11 minutes before the hour carries six-hour periods aligned to that hour. Same row-order quirk as NBM |
| ECMWF open data | `https://ecmwf-forecasts.s3.amazonaws.com/YYYYMMDD/` and `https://data.ecmwf.int/forecasts/` | **2023-01-18** to today | 0.25 deg | CC BY 4.0 | Yes, with credit | OK |
| ERA5 (ARCO copy on Google Cloud) | `https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3` | 1940-01-01 to 2026-06-30 final, to 2026-11-15 preliminary | 0.25 deg, hourly, 37 pressure levels | CC BY 4.0 (confirmed in the CDS catalogue) | Yes, with credit | OK. No account needed for this copy |
| ERA5 (WeatherBench 2 copy on Google Cloud, 1.5 degree) **in use** | `https://storage.googleapis.com/weatherbench2/datasets/era5/1959-2023_01_10-6h-240x121_equiangular_with_poles_conservative.zarr` | 1959-01-01 to 2023-01-10 | 1.5 deg, 6-hourly, 13 pressure levels. Two days per chunk, about 10 MB per pressure-level variable | CC BY 4.0 | Yes, with credit | OK. Fetched for the top storms only (about 36 GB). Its `integrated_vapor_transport` field has no stated units and implausible values, so transport is computed from humidity and wind instead |
| ERA5 (WeatherBench 2 copy, 5.6 degree) **in use** | `.../era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr` | Same | 5.6 deg, 6-hourly, 13 levels. 25 days per chunk, about 10 MB | CC BY 4.0 | Yes, with credit | OK. Fetched for every winter since 1978 (about 13 GB) so that every day has a flow direction |
| ERA5 (NCAR copy on AWS) | `https://nsf-ncar-era5.s3.amazonaws.com/` | Same | Same | Same | Yes | OK (fallback) |
| ERA5 (Copernicus CDS) | `https://cds.climate.copernicus.eu/api/catalogue/v1/collections/reanalysis-era5-pressure-levels` | Same | Same | CC BY 4.0 | Yes | Partial: catalogue answers, downloads need a free account and key. Not needed given the two copies above |
| SNODAS | `https://noaadata.apps.nsidc.org/NOAA/G02158/masked/YYYY/MM_Mon/SNODAS_YYYYMMDD.tar` | 2003-09-30 to 2026-11-21 (both ends confirmed) | 1 km, daily | NOAA product at NSIDC. **Licence text not verified** | Expected yes with citation; confirm | OK |
| PRISM normals and daily grids | `https://data.prism.oregonstate.edu/normals/us/800m/ppt/{monthly,daily}/`, `.../time_series/us/an/800m/ppt/daily/YYYY/` | Normals; daily files present for 2020 (other years not checked) | **800 m** | Free. "May be freely reproduced and distributed" with name, URL and access date | Yes, with credit | OK. The old `services.nacse.org/.../public/normals` URLs are dead. 800 m daily grids are now on the open server |
| Open-Meteo archive (ERA5 at a point) | `https://archive-api.open-meteo.com/v1/archive` | 1979 confirmed | Point, hourly | CC BY 4.0. Free tier is non-commercial, under 10,000 calls a day | Yes for non-commercial use, with credit | OK |
| Open-Meteo historical forecast | `https://historical-forecast-api.open-meteo.com/v1/forecast` | HRRR surface values returned for 2020-02-06. 700 mb fields returned for 2022-01, **null in 2020**. ECMWF null in 2022-01 | Point, hourly | As above | As above | Partial: cross-check only, as the brief intended |
| Open-Meteo single runs, previous runs, ensemble | `single-runs-api`, `previous-runs-api`, `ensemble-api` `.open-meteo.com` | Yesterday's HRRR run returned | Point, hourly | As above | As above | OK |
| Airborne lidar snow depth (ASO, CASM) | `https://data.airbornesnowobservatories.com/` (page shell only), `coloradosnow.org` via search | 2026 basin list does **not** name the Eagle River. It does list the Blue River above Dillon Reservoir | 3 m depth, 50 m SWE where flown | Varies | Varies | **No coverage of Vail Mountain confirmed.** Copper and the east side of Vail Pass may fall in the Blue River survey; unverified |

### Reading NBM and NDFD grids (found in Phase 3)

Both use GRIB "alternate row scanning": odd rows are stored east to west. ecCodes returns the values in stored order and the coordinates in regular order, so odd rows come out mirrored across the country unless they are flipped back. `fcst/grib.py` does this, and its values are checked against the library's own nearest-point lookup. HRRR and GFS grids are not affected.

### HRRR forecasts as issued: how far ahead they go

Counted from the bucket listing for the 00z run on each date.

| Date sampled | `sfc` files | `prs` files | Longest lead |
|---|---|---|---|
| 2014-08-01 | **0** | 16 | 15 h |
| 2014-12-15 through 2016-06-01 | 16 | 16 | 15 h |
| 2016-09-01 through 2018-01-15 | 19 | 19 | 18 h |
| 2018-08-01 through 2020-11-15 | 37 | 37 | 36 h |
| 2020-12-15 onward | 49 | 49 | 48 h |

Fields confirmed in today's files: `APCP`, `WEASD`, `ASNOW`, `SNOD`, `FROZR`, `CSNOW`, `GUST`, `PWAT`, and 700 mb `HGT/TMP/DPT/DZDT/UGRD/VGRD` in `sfc`;
all pressure levels with `VVEL`, humidity and hydrometeor mixing ratios in `prs`. Which of these exist in the 2014 to 2016 files is not yet checked.

## 3. Live forecast inputs

Operational lineup as seen on NOMADS today (`https://nomads.ncep.noaa.gov/pub/data/nccf/com/`, needs `--http1.1` with curl).

| Model | URL tested | Resolution, range | Licence | Status |
|---|---|---|---|---|
| RRFS | `.../com/rrfs/prod/rrfs.20261121/HH/` | 3 km CONUS, `2dfld` and `prslev`, hourly cycles, **to 84 h** at 00z and 12z | Public domain | OK and operational. **No operational archive found on AWS** (`noaa-rrfs-pds` holds only retrospective test output), and NOMADS shows two days |
| REFS (RRFS ensemble) | `.../com/refs/prod/refs.20261121/{00,06,12,18}/ensprod/` | 3 km ensemble products | Public domain | OK |
| HRRR | `.../com/hrrr/prod/` and AWS | 3 km, 48 h at 00/06/12/18z | Public domain | OK, still running |
| GFS, GEFS | AWS buckets above | 0.25 deg, 0.25/0.5 deg | Public domain | OK |
| NBM | AWS bucket above | 2.5 km | Public domain | OK |
| ECMWF open data | AWS bucket above | 0.25 deg | CC BY 4.0 | OK |
| NWS gridded forecast | `https://api.weather.gov/gridpoints/GJT/174,119` | 2.5 km, includes `snowfallAmount`, `snowLevel`, gust probabilities | Public domain | OK |
| NAM, SREF, HREF | `.../com/{nam,sref,href}/prod/` | n/a | n/a | **Gone: all three return 404.** Nothing here depends on them |

`hiresw` and `rap` folders still carry today's date. I did not check whether they are still being filled.

## 4. Terrain

| Source | Location | Coverage | Resolution | Licence | Redistribution | Status |
|---|---|---|---|---|---|---|
| **Existing ski game: lidar height grid** | `~/Games/Copper Ski Resort Game/data/work/vail/hm.npy` | 15.4 x 10.5 km, UTM 13N (EPSG:26913), E 376200 to 391560, N 4379504 to 4390000. Lat 39.5563 to 39.6529, lon -106.4410 to -106.2640 | **2 m** (5249 x 7681), from 1 m 3DEP lidar | USGS, public domain | Yes | OK, opened and read. Elevation range 2358 to 3603 m |
| Existing: 1 m DEM tiles | `.../data/raw/vail/dem1m/` (1.1 GB) | Same box | 1 m | Public domain | Yes | OK |
| Existing: lidar canopy | `.../data/work/vail/lidar_canopy.npz` (`n1`, `nh`, `top`) | Same box | 2 m: share of first returns above ground, and canopy height | Public domain (USGS 2016 survey) | Yes | OK. Measured canopy, better than NLCD. Survey is from 2016 |
| Existing: land-cover masks | `.../data/work/vail/masks.npy` (4 planes) | Same box | 2 m | Derived from NAIP and Sentinel-2 | Yes with Copernicus credit | OK |
| Existing: regional DEM | `.../data/raw/vail/bg/bg_dem20.tif` | 46 x 46 km. Lat 39.377 to 39.798, lon -106.586 to -106.057 | **30 m** (the file name says 20) | Public domain | Yes | OK. Covers Beaver Creek, McCoy Park, Copper, Vail Pass, Fremont Pass. **Does not reach Elliot Ridge or the full box in the brief** |
| Existing: OpenStreetMap extract | `.../data/raw/vail/osm_overpass.json` | Lat 39.515 to 39.66, lon -106.45 to -106.20. Fetched 2026-11-14 | 356 downhill piste ways, 33 lift ways (all named chairs and both gondolas), the "Vail" ski-area boundary | ODbL | Yes, with credit and share-alike on the data | OK |
| USGS 3DEP image service | `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer` | National | 1 m where lidar exists, else 10 m | Public domain | Yes | OK (service dated 2026-10-27). Use to extend the regional DEM |
| USGS TNM product API | `https://tnmaccess.nationalmap.gov/api/v1/products` | 9 one-metre DEM tiles and 271 point-cloud tiles over the Vail box, all "CO Central Western 2016" | 1 m | Public domain | Yes | OK |
| NLCD tree canopy | `https://www.mrlc.gov/geoserver/ows` (WCS) | CONUS, yearly layers 2014 to 2021 seen (`nlcd_tcc_conus_YYYY_v2021-4`) | 30 m | Public domain | Yes | OK. Only needed outside the lidar box |
| LANDFIRE | `https://lfps.usgs.gov/` | National | 30 m | Public domain | Yes | Partial: site reachable, product request not tested. Not needed if NLCD is used |
| Overpass API | `https://overpass-api.de/api/interpreter` | Global | Vector | ODbL | Yes | OK. The `overpass.kumi.systems` mirror returned 502 today |

## 4b. Map tiles and fonts (Phase 5)

Loaded by the viewer's browser, not by the pipeline. Tested 2026-11-24.

| Source | URL | Licence | Status |
|---|---|---|---|
| USGS basemaps (shaded relief, topo, imagery) | `https://basemap.nationalmap.gov/arcgis/rest/services/{USGSShadedReliefOnly,USGSTopo,USGSImageryOnly}/MapServer/tile/{z}/{y}/{x}` | Public domain | OK. Cached to zoom 16 |
| Terrain tiles for 3D (Mapzen "terrarium", AWS Open Data) | `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` | Open data; attribution to Mapzen and its listed sources is required and is shown on the map | OK |
| MapLibre GL JS 4.7.1 | `https://unpkg.com/maplibre-gl@4.7.1/` | BSD 3-clause | OK |
| Label fonts for contour labels | `https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf` | MapLibre's demo server; no service promise | OK today. **Replace with self-hosted fonts before relying on it**; if it fails the contour labels vanish and nothing else breaks |
| Page fonts | Google Fonts: Big Shoulders Display, Instrument Sans | SIL Open Font License | OK |

## 4c. Phase 6 additions

| Source | Endpoint | Record | Resolution | Licence | Redistribute? | Status |
|---|---|---|---|---|---|---|
| Iowa Environmental Mesonet, airport stations (ASOS/AWOS) **in use** | `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=CCU&data=sknt&data=gust&data=drct&tz=Etc/UTC&format=onlycomma&report_type=3&report_type=4&year1=..` | `CCU` (Copper Mountain summit, 12,075 ft, 39.475, -106.153): every report since October 2018 pulled, 59,857 station-hours | Every 20 minutes: wind, gust, direction | Public domain (FAA/NWS observations, served by Iowa State) | Yes | OK. Rate limited: the ninth request in a row drew a 429, so the reader waits 5 s between requests and backs off on 429. One file per water year, cached |
| HRRR surface gust, same public zarr copy **in use** | `https://hrrrzarr.s3.amazonaws.com/sfc/YYYYMMDD/YYYYMMDD_12z_fcst.zarr/surface/GUST/surface/GUST/0.3.4` | Winter 12z runs since October 2018: 1,437 fetched, 34 absent | 3 km, hourly to 48 h | Public domain | Yes | OK. Kept apart from the main cache (`data/cache/hrrr_gust`, 219 MB) |
| NRCS SNOTEL hourly, for today's hours | Same AWDB endpoint as above with `endDate` set to tomorrow | Readings arrive about an hour behind | Hourly | Public domain | Yes | OK. With `endDate` = today the reply stops at midnight station time, so the live code asks for tomorrow. The service returned one 403 on a good request and recovered; requests now retry |
| ntfy.sh (optional, off) | `https://ntfy.sh/<topic>` | n/a | n/a | Free public service; the topic name is the only secret | n/a | **Not tested, nothing sent.** Used only if `ntfy` is added to `[alerts] channels` and `NTFY_TOPIC` is set in `.env`. It would send the alert text to that service |

| GEFS ensemble mean and spread to 264 h **in use** (10-day outlook) | `https://noaa-gefs-pds.s3.amazonaws.com/gefs.YYYYMMDD/HH/atmos/pgrb2ap5/{geavg,gespr}.tHHz.pgrb2a.0p50.fFFF` | Winter 12z runs since November 2020: 1,106 fetched, hours 54 to 264. Live: any of the four daily runs, hours 6 to 264 | 0.5 degree, six-hourly: precipitation mean and spread, 700 mb temperature mean | Public domain | Yes | OK. Files reach 240 h about 5.5 h after each run (measured: 05:29, 11:30, 17:31, 23:30 UTC) and 384 h an hour later. At 72 requests at once the bucket answered "503 Slow Down" for 16 runs; they came through at 12 at once. Cache `data/cache/gefs_240` |

| SNODAS snow depth, live **in use** (base depth) | Same archive as the SWE files above; depth is product code 1036 inside each daily tar | The file for 2026-11-24 was up by midday the same day | 1 km, daily (06 UTC state) | NOAA product at NSIDC. Licence text still not verified | Expected yes with citation | OK |

| National Blend long-range precipitation **in use** (days 4 to 10) | `https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.YYYYMMDD/HH/core/blend.tHHz.core.fFFF.co.grib2`, six-hour `APCP` (and `ASNOW` live) | Winter 12z and 19z cycles since November 2020: 2,213 runs, hours 44 to 264 | 2.5 km, six-hourly | Public domain | Yes | OK. About 1 MB a field; 36 fields a run. Six-hour totals end at 00, 06, 12 and 18 UTC whatever the cycle hour. The long-range fields are refreshed in the 01, 07, 13 and 19 UTC cycles; other hours carry the last refresh. Cache `data/cache/nbm_240` |
| NWS gridded forecast (checked by hand, not automated) | `https://api.weather.gov/gridpoints/GJT/173,120` | Live | 2.5 km | Public domain | Yes | OK. Used once on 2026-11-24 to compare Saturday's numbers |

Not used, and why: no record of lift holds is public, and Vail's own lift status and wind sensors are on pages whose terms rule out automated reading. The four wind stations on the mountain are served through CAIC, whose terms prohibit it.

## 5. Snow-density research

| Item | Where | What is established | Status |
|---|---|---|---|
| Veals et al. 2025, *Weather and Forecasting* 40, 1825-1838 | doi:10.1175/WAF-D-24-0233.1 | From the abstract: 14 western mountain sites; random forest R2 0.43, MAE 2.94; operational methods R2 0.04 to 0.23, MAE 4.01 to 9.45 | Partial. **Full text not read** (publisher returned 403, PDF too large for the fetch tool). Site list, predictor heights and the data statement are unverified |
| Pletcher et al. 2026, *Weather and Forecasting* 41(2), 317-338 | doi:10.1175/WAF-D-25-0124.1 | From the abstract: CONUS random forest trained on CoCoRaHS and ERA5 profiles, applied to HRRR; MAE 2.92 against 3.51 to 4.76 for operational methods | Partial, same limit |
| Code and trained models | `https://github.com/mdpletcher/utahrfslr` | Public repository. Contains `models/rf/rf_slr_model.pkl`, HRRR helpers, the NBM density methods, and example notebooks. Last push 2026-01-09 | OK to read. **No licence file**, so reuse rights are not granted. Ask the authors before building on it |
| Earlier Alta-only model | `https://github.com/mdpletcher/SLR_random_forest_pletcher` | Public, no licence | Same |

## Not yet tested

- History for the five CAIC/Vail Resorts stations from any permitted source.
- SNODAS licence wording.
- The `vail.com` copy of the website terms, the site's `robots.txt`, and who hosts the stake-camera images.
- Which HRRR fields exist in 2014 to 2016 files. Forecasts before July 2018 are only in the GRIB archive and have not been pulled.
- NBM percentile (`qmd`) files, GEFS members.
- PRISM 800 m daily availability outside 2020.
- Whether any ASO flight touched Vail Mountain, Copper or Vail Pass.
- LANDFIRE product requests, MADIS account-level access.
