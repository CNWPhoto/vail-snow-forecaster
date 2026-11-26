"""Command line entry point."""
from __future__ import annotations

import argparse

from .resort import load_resort


def main() -> None:
    ap = argparse.ArgumentParser(prog="vailsnow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("terrain", help="build the terrain grid, wind-shelter layers and zones")
    t.add_argument("--resort", default="vail")
    c = sub.add_parser("catalog", help="ingest observations and build the snow-event catalogue")
    c.add_argument("--resort", default="vail")
    c.add_argument("--top", type=int, default=200)
    sub.add_parser("upper-fetch", help="download the ERA5 chunks the storm catalogue needs")
    sub.add_parser("upper", help="build storm upper-air features and the daily flow table")
    sub.add_parser("climatology", help="compute the regime climatology tables")
    sub.add_parser("table", help="build the forecast-versus-observed table from the cached forecasts")
    sub.add_parser("hindcast", help="run the leave-one-season-out hindcasts and write the Phase 3 score tables")
    sub.add_parser("train", help="fit the final models on every finished season, for the live forecast")
    fc = sub.add_parser("forecast", help="build the map files from the newest 12z runs (or a past run with --run)")
    fc.add_argument("--run", help="a past 12z run to replay, e.g. 2025-02-14")
    sc = sub.add_parser("score", help="log this season's earlier runs if missing, then score every logged forecast against the gauge")
    sc.add_argument("--no-replay", action="store_true", help="score only what is already in the log")
    al = sub.add_parser("alert", help="build the evening or 5 am powder alert and send it if a threshold is met")
    al.add_argument("when", choices=("evening", "morning"))
    al.add_argument("--dry-run", action="store_true", help="print it and send nothing")
    al.add_argument("--force", action="store_true", help="send even if no threshold is met")
    al.add_argument("--at", help="pretend it is this UTC time, e.g. '2026-11-21 04:45' (implies --dry-run)")
    bt = sub.add_parser("alert-backtest", help="what the alert thresholds would have done over past winters")
    bt.add_argument("--likely", type=float)
    bt.add_argument("--high", type=float)
    an = sub.add_parser("analogs", help="the past days most like a given day's forecast")
    an.add_argument("date", help="a day since October 2018, e.g. 2020-02-06")
    an.add_argument("-k", type=int, default=5)
    sub.add_parser("wind-check", help="check forecast ridge gusts against the summit airport station (lift wind-hold odds)")
    dr = sub.add_parser("check-resort", help="say what a resort file has and what is still missing")
    dr.add_argument("resort", nargs="?")
    ol = sub.add_parser("outlook", help="refresh the 10-day outlook from the newest complete ensemble run (four a day)")
    ol.add_argument("--replay", action="store_true", help="first log what this season's earlier runs would have said")
    sub.add_parser("depth", help="estimate base depth across the mountain from SNODAS and the SNOTEL depth sensors")
    sub.add_parser("outlook-fetch", help="download 10-day ensemble forecasts as issued since late 2020")
    sub.add_parser("outlook-train", help="fit and score the days 4 to 10 model, season by season, then fit the one used live")
    pr = sub.add_parser("prune", help="remove files the schedule no longer needs (old map forecasts, spent downloads) and trim job logs")
    pr.add_argument("--dry-run", action="store_true", help="say what would go and remove nothing")
    sub.add_parser("disk", help="what is on disk, largest first, and what grows")
    sub.add_parser("status", help="when each piece last updated, and whether the scheduled jobs are installed")
    sub.add_parser("state-grid", help="cut the mountain-state grid and its static terrain layers from the ski game's data")
    sub.add_parser("state", help="bring the hourly mountain state up to now and publish it, with tomorrow morning's preview")
    sd = sub.add_parser("state-days", help="rebuild the notable past days for the game's day picker")
    sd.add_argument("--only", help="one day id from the resort file, e.g. 2020-02-07")
    sub.add_parser("baseline-fetch", help="download NBM, NWS official and GFS forecasts as issued since late 2020")
    sub.add_parser("hrrr-fetch", help="download HRRR forecasts as issued for every snow-season day since 2018")
    args = ap.parse_args()
    import sys
    if not sys.stdout.isatty() and args.cmd in ("forecast", "outlook", "alert", "score", "state"):      # a scheduled run: stamp the log
        import datetime as dt
        print(f"\n[{dt.datetime.now().astimezone():%Y-%m-%d %H:%M %Z}] vailsnow {' '.join(sys.argv[1:])}", flush=True)
    if args.cmd == "terrain":
        from .terrain.build import build
        s = build(load_resort(args.resort))
        w = s["whole_resort"]
        print(f"{s['resort']}: {w['acres']:,} acres in zones, {w['elev_min_ft']:,} to {w['elev_max_ft']:,} ft")
        if s["seed_trails_not_in_map"]:
            print("config trails not found in the map data:", ", ".join(s["seed_trails_not_in_map"]))

    elif args.cmd == "catalog":
        from .catalog.build import build
        print(build(load_resort(args.resort), args.top))

    elif args.cmd == "upper-fetch":
        from .upper.download import run
        print(run())

    elif args.cmd == "upper":
        from .upper.features import build_all
        print(build_all())
    elif args.cmd == "climatology":
        from .catalog.climatology import build
        out = build()
        print({"storms": out["storms"]["storms"], "snodas_storms": out["snodas"]["storms_used"]})

    elif args.cmd == "table":
        from .model.table import build
        from .obs import hourly
        hourly.build(load_resort())
        t = build()
        print({"rows": len(t), "windows_by_lead": t.groupby("tier").size().to_dict()})

    elif args.cmd == "hindcast":
        import json
        import pandas as pd
        from .model import downscale, hindcast, settle, slr, snowfall, snowlevel
        from .resort import data_dir
        from .verify.plots import reliability_figure
        resort, out = load_resort(), data_dir() / "model"
        hindcast.run(resort)
        hindcast.reliability_tables()
        reliability_figure(name="phase4_reliability.png")
        table = pd.read_parquet(out / "table.parquet")
        table = table[table["season"] <= hindcast.LAST_SEASON]
        phase = snowlevel.phase_cases(resort, table)
        phase.to_parquet(out / "phase_cases.parquet")
        (out / "scores_phase.json").write_text(json.dumps(snowlevel.score_phase(phase), indent=1))
        h = slr.hindcast(slr.cases(resort, table))
        h.to_parquet(out / "slr_hindcast.parquet")
        both = h[h["nbm_slr"].notna()]
        (out / "scores_slr.json").write_text(json.dumps({
            "cases": int(len(h)), "all": slr.score(h).round(3).to_dict("records"),
            "where_nbm_exists": slr.score(both).round(3).to_dict("records")}, indent=1))
        downscale.evaluate(resort)
        snowfall.score(snowfall.hindcast(resort))
        _, settled = settle.hindcast(resort)
        settled["two_day"] = settle.two_day_check()
        (out / "scores_settle.json").write_text(json.dumps(settled, indent=1))
        settled["mountain_end_to_end"] = settle.mountain_end_to_end()
        (out / "scores_settle.json").write_text(json.dumps(settled, indent=1))
        print("scores written to", out)

    elif args.cmd == "train":
        from .model.final import train
        print(train())

    elif args.cmd == "forecast":
        import pandas as pd
        from .live.forecast import build
        print(build(pd.Timestamp(args.run).normalize() + pd.Timedelta(hours=12) if args.run else None))

    elif args.cmd == "score":
        import json
        from .extras import scorecard
        if not args.no_replay:
            print("replay:", scorecard.replay())
        card = scorecard.publish()
        print(json.dumps({"counts": card["counts"], "leads": card["leads"], "depth": card["depth"], "storms": len(card["storms"])}, indent=1))

    elif args.cmd == "alert":
        import pandas as pd
        from .extras import alerts
        resort = load_resort()
        now = pd.Timestamp(args.at) if args.at else None
        a = alerts.evening(resort, now) if args.when == "evening" else alerts.morning(resort, now)
        print(alerts.text(a))
        print("\nthreshold met:" if a["fire"] else "\nno threshold met", "; ".join(a.get("reasons", [])))
        print(alerts.deliver(resort, a, dry_run=args.dry_run or bool(args.at), force=args.force))

    elif args.cmd == "alert-backtest":
        from .extras import alerts
        print(alerts.backtest(likely_in=args.likely, high_end_in=args.high))

    elif args.cmd == "analogs":
        from .extras import analogs
        r = analogs.for_date(args.date, args.k)
        print(f"{r['date']}: gauge measured {r['measured_in']} in" + (f", in the {r['storm']['label']} storm" if r["storm"] else ""))
        print(r["resembles"])
        for a in r["analogs"]:
            print(f"  {a['date']}  {a['water_in']:.2f} in  {a['regime']:<12} match: {a['match']:<5} " + (a["storm"]["label"] if a["storm"] else ""))

    elif args.cmd == "wind-check":
        import json
        from .extras import liftwind
        s = liftwind.evaluate()
        print(json.dumps({k: s[k] for k in ("hours", "hourly", "sustained_share_of_600mb", "odds_check")}, indent=1))

    elif args.cmd == "check-resort":
        from .extras import doctor
        print(doctor.report(args.resort))

    elif args.cmd == "outlook":
        from .live import outlook
        if args.replay:
            print("replay:", outlook.replay())
        o = outlook.build()
        print(f"ensemble run {o['ensemble_run']}, {o['logged']} new forecasts logged, next update about {o['next_update']}Z")
        for d in o["days"]:
            if d["available"]:
                z = d["zones"][o["headline_zone"]]
                wet = d.get("if_it_snows")
                print(f"  day {d['day']:>2} {d['start'][:10]} {d['source']:<15} expected {d['expected'][o['headline_zone']]:>4} in ({z[1]} to {z[2]})  chance of snow {d['chance']['1in']:>2}%"
                      + (f", about {wet[o['headline_zone']]} in if it comes" if wet else "")
                      + ("" if d.get("change_in") is None else f"  change since yesterday {d['change_in']:+.1f} in"))

        try:                                      # the base-depth estimate rides along: the sensors report hourly
            from .live import depth
            d = depth.build()
            print(f"base depth: sensor {d['gauge']['depth_in'] if d['gauge'] else 'n/a'} in, SNODAS of {d['model_date']}, mountain {d['range_in'][0]} to {d['range_in'][1]} in")
        except Exception as e:
            print(f"base depth not updated: {type(e).__name__}: {e}")

    elif args.cmd == "depth":
        import json
        from .live import depth
        d = depth.build()
        print(json.dumps({k: d[k] for k in ("model_date", "gauge", "model_at_gauge_in", "tie", "inches_per_1000ft", "range_in", "checks")}, indent=1))

    elif args.cmd == "outlook-fetch":
        from .fcst.archive import run
        print(run(models=("gefs_240",), workers=4))

    elif args.cmd == "outlook-train":
        import json
        from .model import extended
        s = extended.score(extended.hindcast())
        print(json.dumps({k: {x: v[x] for x in ("n", "mae", "raw_gefs_mae", "average_for_the_month_mae", "skill_pct", "inside_10_90_pct", "wet_day_skill_pct")} for k, v in s["by_lead"].items()}, indent=1))
        print(json.dumps(s["blocks"], indent=1))
        print("totals through each day:", json.dumps(extended.score_horizons()))
        print(extended.train())

    elif args.cmd == "prune":
        import json
        from .extras import housekeeping
        print(json.dumps(housekeeping.prune(dry_run=args.dry_run), indent=1))

    elif args.cmd == "disk":
        from .extras import housekeeping
        rows = housekeeping.usage()
        for r in rows:
            print(f"  {r['size']:>9}  {r['what']:<52} {r['note']}")
        print(f"  {housekeeping.human(sum(r['bytes'] for r in rows)):>9}  total of the above")

    elif args.cmd == "status":
        from .extras import status
        print(status.report())

    elif args.cmd == "state-grid":
        from .state import grid, publish
        resort = load_resort()
        out = grid.build(resort)
        files = publish.static_files(resort, grid.load(resort))
        g = out["grid"]
        print(f"grid {g['width']} x {g['height']} cells of {g['cell_m']:g} m, {g['crs']}, west {g['west']:g} north {g['north']:g}")
        print(f"static layers: {out['file']} ({out['mb']} MB); {files['grid']}")
        print("share of the ski area by terrain class: " + ", ".join(f"{k} {v:.0%}" for k, v in out["inside_share_by_class"].items()))
        print(f"assumed groomed nightly ({len(out['groomed_runs'])} runs): " + ", ".join(out["groomed_runs"]))

    elif args.cmd == "state":
        from .state import run as state_run
        r = state_run.live()
        print(f"state for {r['time']} UTC, from {r['started_from']} ({r['hours_stepped']} hours stepped; gauge checked to {r['checked_until']})")
        print(f"  {r['latest']['conditions']}")
        print(f"  textures {r['latest']['total_kb']} KB in {r['latest']['folder']}; tomorrow: {r['tomorrow'] if isinstance(r['tomorrow'], str) else r['tomorrow']['time']}")

    elif args.cmd == "state-days":
        from .state import run as state_run
        for d in state_run.days(only=args.only):
            print(f"{d['label']}\n  {d['conditions']}")

    elif args.cmd == "baseline-fetch":
        from .fcst.archive import run
        print(run())

    elif args.cmd == "hrrr-fetch":
        from .fcst.hrrr import run
        print(run())


if __name__ == "__main__":
    main()
