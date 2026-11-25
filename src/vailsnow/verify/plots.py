"""Figures for the phase summaries."""
from __future__ import annotations

import json
from pathlib import Path

from ..resort import ROOT, data_dir

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
LEAD_COLOURS = {12: "#86b6ef", 24: "#3987e5", 48: "#1c5cab", 72: "#0d366b"}     # one hue, light to dark with lead time


def reliability_figure(path: Path | None = None, name: str = "phase3_reliability.png") -> Path:
    """How often the gauge came in at or below each stated percentile, when snow was possible."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rel = json.loads((data_dir() / "model" / "reliability.json").read_text())
    fig, ax = plt.subplots(figsize=(6.4, 5.6), dpi=160, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.plot([0, 100], [0, 100], color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.text(56, 61, "perfectly reliable", color=MUTED, fontsize=9, rotation=45, rotation_mode="anchor")
    for lead, colour in LEAD_COLOURS.items():
        r = rel[str(lead)]
        x, y = [100 * v for v in r["levels"]], [100 * v for v in r["observed"]]
        ax.plot(x, y, color=colour, lw=2, marker="o", ms=4.5, mec=SURFACE, mew=1, label=f"{lead} h", zorder=3)
    ax.axvspan(0, 45, color="#f0efec", zorder=0)
    ax.text(3, 93, "Below the median, dry days tie with\na forecast of zero, so the lower end\ncannot be judged from this chart.", color=MUTED, fontsize=8.5, va="top")
    ax.set_xlim(0, 100), ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 101, 10)), ax.set_yticks(range(0, 101, 10))
    ax.set_xlabel("Stated percentile of the forecast", color=INK)
    ax.set_ylabel("Share of windows at or below it (%)", color=INK)
    ax.set_title("Are the forecast ranges honest?", color=INK, fontsize=12, loc="left", pad=22)
    ax.text(0, 1.02, "Mid-mountain water, leave-one-season-out, windows where snow was possible",
            transform=ax.transAxes, color=MUTED, fontsize=9)
    ax.grid(color=GRID, lw=0.8)
    ax.tick_params(colors=MUTED, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    leg = ax.legend(title="Lead time", loc="lower right", frameon=False, fontsize=9, title_fontsize=9)
    plt.setp(leg.get_texts(), color=INK), leg.get_title().set_color(MUTED)
    path = path or ROOT / "figures" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return path
