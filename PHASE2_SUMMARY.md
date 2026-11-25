# Phase 2: history and storm catalogue

Date: 2026-11-23, corrected 2026-11-24. Checkpoint for review.

> **Correction (2026-11-24).** Phase 2 dated every SNOTEL gain one day early: the service returns end-of-day readings by default and the code read them as start-of-day. The loader now asks for start-of-day readings, a test compares the daily and hourly records, and the catalogue, upper-air features, climatology tables and `STORM_CLIMATOLOGY.md` have all been rebuilt. That document lists what changed. Storm counts did not change; storm dates moved one day later.

Checkpoint for review. The findings are in `STORM_CLIMATOLOGY.md`; this note covers what was built and what was left out.

## Built

| Piece | Where | Notes |
|---|---|---|
| SNOTEL daily record, 7 sites | `data/obs/snotel_daily.parquet` | 1978 to today |
| SNOTEL hourly record, Vail | `data/obs/snotel_target_hourly.parquet` | 2006 to today |
| Valley observers, 5 sites | `data/obs/valley_daily.parquet` | Town from 1985, East Vail 2005, West Vail 2008 |
| Storm catalogue | `data/catalog/events.parquet`, `.csv` | 1,567 storms, 205 tagged top, each with totals at every other site |
| Flow direction for every winter day | `data/upper/daily_flow.parquet` | 11,816 days, 1978 to January 2023 |
| Upper-air features, top storms | `data/upper/event_features.parquet`, `data/catalog/top_storms.csv` | 197 storms, 34 features each, with a regime |
| PRISM zone prior | `data/prism/vail_zone_prior.json` | Winter precipitation by zone and band |
| SNODAS storm patterns | `data/catalog/snodas_zone_gains.parquet` | 59 top storms since 2003 |
| NWS case library | `data/catalog/case_library.json` | 21 write-ups, 20 linked to catalogue storms |
| Climatology tables | `data/catalog/climatology.json` | Every number in `STORM_CLIMATOLOGY.md` |
| Tests | `tests/` | 88 passed at the time; see `PHASE3_SUMMARY.md` for the current count |

Rebuild in order:

```bash
uv run vailsnow catalog
```

```bash
uv run vailsnow upper-fetch
```

```bash
uv run vailsnow upper
```

```bash
uv run vailsnow climatology
```

Downloads are cached and never repeated. The cache is 47 GB, almost all of it reanalysis.

## Departures from the brief

- **Regimes are wind sectors, not learned clusters.** They are named for the 600 mb wind direction, with a closed-low override. K-means was run as a cross-check and agrees at the warm and cold ends. Sectors are easier to forecast against and to explain in the map's "why" panel.
- **600 mb replaces 700 mb** for wind, because 700 mb is below mid-mountain at Vail.
- **The reanalysis is the coarse public copy**, as chosen. It ends on 2023-01-10, so 8 of the 205 top storms have no upper-air features.
- **The reanalysis download came to 47 GB, not the 30 GB I quoted.** I added a coarser all-days set so regimes could be compared without picking storms by Vail's own totals.
- **No ERA5 back to 1979 for every storm.** All winter days have a flow direction; only the top storms have the full feature set.

## Not done

- HRRR features for storms since late 2014. Cost measured only: about 50 MB per forecast hour for the fields needed.
- Vertical motion in the snow-growth layer, and banded versus convective structure. Too fine for this reanalysis grid.
- Wet-bulb zero height. A freezing level is computed, but in cold storms it falls below the lowest usable level and is not reliable.
- Lidar snow-depth coverage. Not rechecked; Phase 0 found none for Vail.
- The 2019 write-up titled "February 20-23" could not be dated automatically and is unlinked.

## Corrections this phase makes to earlier notes

- Phase 0 said the SNOTEL record shows Vail at 2.6 times Beaver Creek in February 2020. With storms split at lulls the figure is 2.2 times (3.3 in against 1.5 in).
- Phase 0 dated the benchmark storms Feb 5 to 8, 2020 and Jan 29 to 31, 2014, which were right by accident of how its table was read. The catalogue now has them as Feb 5 to 9, 2020 and Jan 29 to 31, 2014.
- The first Phase 2 note compared sites during Vail's own storms. Those ratios were inflated and are replaced by all-days ratios.

## For Phase 3

1. The precipitation forecast from the weather models has to be the main input. Upper-air features alone do not rank big storms (best rank correlation 0.2).
2. Flow direction is a strong correction for where the snow goes: the Copper to McCoy Park ratio more than doubles between west-southwest and northwest flow.
3. Snow-to-liquid ratio rises from about 11 in south-southwest flow to 18 in north-northwest flow at the town station. That is the first local signal for the density model.
4. SNODAS under-reads Vail's storm gains by about 30%, so it should not be a training target.
5. HRRR has to be pulled by storm day, not wholesale.
