"""Phase 2 pipeline, part one: observation tables and the event catalogue."""
from __future__ import annotations

import pandas as pd

from ..obs import acis, snotel
from ..resort import Resort, data_dir, load_resort
from . import events as ev


def build(resort: Resort | None = None, top_n: int = 200) -> dict:
    resort = resort or load_resort()
    out_obs, out_cat = data_dir() / "obs", data_dir() / "catalog"
    out_obs.mkdir(parents=True, exist_ok=True)
    out_cat.mkdir(parents=True, exist_ok=True)

    sn = snotel.load_all(resort)
    va = acis.load_all(resort)
    sn.to_parquet(out_obs / "snotel_daily.parquet", index=False)
    va.to_parquet(out_obs / "valley_daily.parquet", index=False)

    target = next(s["id"] for s in resort.snotel if s.get("target"))
    by_site = {sid: g.set_index("date").sort_index() for sid, g in sn.groupby("site")}
    by_valley = {sid: g.set_index("date").sort_index() for sid, g in va.groupby("site")}
    cat = ev.detect_events(by_site[target])
    # "Top" storms: the largest by total water, plus any that rank as high on their best two days.
    cat["top"] = (cat["rank"] <= top_n) | (cat["rank_2day"] <= top_n // 2)
    for sid, df in by_site.items():
        if sid != target:
            cat[f"swe_{sid}"] = [ev.window_gain(df["swe"], s, e) for s, e in zip(cat["start"], cat["end"])]
    for sid, df in by_valley.items():
        ok = df[~df["multi_day"]]
        cat[f"snow_{sid}"] = [ev.valley_total(ok["snow"], s, e) for s, e in zip(cat["start"], cat["end"])]
        cat[f"pcpn_{sid}"] = [ev.valley_total(ok["pcpn"], s, e) for s, e in zip(cat["start"], cat["end"])]
    # storm shape from the hourly gauge, where the hourly record exists
    triplet = next(s["triplet"] for s in resort.snotel if s.get("target"))
    hourly = snotel.load_hourly(triplet)
    hourly.to_parquet(out_obs / "snotel_target_hourly.parquet")
    shape = pd.DataFrame([ev.hourly_shape(hourly["prec"], s, e) if s >= hourly.index[0] else {} for s, e in zip(cat["start"], cat["end"])], index=cat.index)
    cat = cat.join(shape)
    cat.to_parquet(out_cat / "events.parquet", index=False)
    cat.to_csv(out_cat / "events.csv", index=False)
    return {"snotel_rows": len(sn), "valley_rows": len(va), "events": len(cat), "top": int(cat["top"].sum()),
            "first": str(cat["start"].min().date()), "last": str(cat["end"].max().date())}
