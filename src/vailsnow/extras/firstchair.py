"""Where to ski first: zones ranked for first chair by new snow since the lifts closed, how
light it is, how much of the zone the wind has scoured, and whether its lift is likely to run.

The amounts carry the same caveat as every zone number: differences between zones come from
long-term patterns, mostly elevation, and nothing measures snow by zone. The weights below are
choices, not fitted values. There is no record of where the skiing was best to fit them to.
"""
from __future__ import annotations

import numpy as np

CLOSE_HOUR = 11              # lifts close 4 pm MST: 11 hours into a 12z run
FIRST_CHAIR_HOUR = 27        # about 8 am MST the next morning
AVERAGE_RATIO = 13.0         # snow-to-liquid ratio counted as neither light nor heavy
QUALITY_LIMITS = (0.75, 1.25)
WIND_PENALTY = 0.5           # a zone scoured everywhere scores half
SCOUR_BELOW, LOAD_ABOVE = 0.9, 1.1   # after-wind depth relative to as-fallen
LIFT_FACTOR = {"low": 1.0, "possible": 0.85, "likely": 0.5, None: 1.0}
WORTH_RANKING_IN = 0.5       # below this likely amount everywhere, the ranking is by the high end instead


def overnight_parts(shares: np.ndarray, morning: int) -> np.ndarray:
    """(3,) share of each forecast day's snow that falls between closing time and first chair on
    `morning` (1 = tomorrow, 2 = the day after). `shares` is (3, 24): each day's hourly timing."""
    first, last = CLOSE_HOUR + 24 * (morning - 1), FIRST_CHAIR_HOUR + 24 * (morning - 1)      # hours first+1 .. last
    out = np.zeros(3)
    for d in range(3):
        a, b = max(first - 24 * d, 0), min(last - 24 * d, 24)
        if b > a:
            out[d] = shares[d][a:b].sum()
    return out


def quality_factor(ratio: float) -> float:
    return float(np.clip(ratio / AVERAGE_RATIO, *QUALITY_LIMITS))


def score(amount_in: float, ratio: float, scoured_share: float, lift_risk: str | None) -> float:
    return float(amount_in * quality_factor(ratio) * (1.0 - WIND_PENALTY * scoured_share) * LIFT_FACTOR.get(lift_risk, 1.0))


def reasons(z: dict, basis: str) -> str:
    amount = z["overnight"][0 if basis == "likely" else 2]
    bits = [f"{amount:.1f} in {'likely' if basis == 'likely' else 'at the high end'} since closing"]
    if amount >= 0.5:
        bits.append(f"{z['quality']} snow")
        if z["scoured_pct"] >= 15:
            bits.append(f"wind has scoured about {z['scoured_pct']}% of it")
        elif z["loaded_pct"] >= 15:
            bits.append(f"wind-loaded pockets on about {z['loaded_pct']}% of it")
    if z["lift"] and z["lift"]["risk"] != "low":
        bits.append(f"{z['lift']['name']} wind hold {z['lift']['risk']}")
    return ", ".join(bits) + "."


def rank(zones: list[dict]) -> tuple[str, list[dict]]:
    """Order zones for one morning. Each zone needs: overnight [likely, low, high] inches, ratio,
    scoured_pct, loaded_pct, quality and lift ({name, risk} or None). Returns the basis used
    ('likely', or 'high' when nothing much is likely anywhere) and the ordered list."""
    basis = "likely" if max((z["overnight"][0] for z in zones), default=0.0) >= WORTH_RANKING_IN else "high"
    k = 0 if basis == "likely" else 2
    out = []
    for z in zones:
        s = score(z["overnight"][k], z["ratio"], z["scoured_pct"] / 100.0, z["lift"]["risk"] if z["lift"] else None)
        out.append({**z, "score": round(s, 2), "why": reasons(z, basis)})
    out.sort(key=lambda z: -z["score"])
    for i, z in enumerate(out, 1):
        z["rank"] = i
    return basis, out
