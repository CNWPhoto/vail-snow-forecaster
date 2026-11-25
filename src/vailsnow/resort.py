"""Resort configuration: one TOML file per mountain."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FT_PER_M = 3.280839895


def _load_env() -> None:
    """Read KEY=VALUE lines from .env into the environment (without overriding)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()


def game_dir() -> Path:
    return Path(os.environ.get("VAILSNOW_GAME_DIR", "~/Games/Copper Ski Resort Game")).expanduser()


def cache_dir() -> Path:
    p = Path(os.environ.get("VAILSNOW_CACHE_DIR", str(ROOT / "data" / "cache"))).expanduser()
    return p if p.is_absolute() else ROOT / p


def data_dir() -> Path:
    """Where built tables and models live. One folder per resort: set VAILSNOW_DATA_DIR for a second one."""
    p = Path(os.environ.get("VAILSNOW_DATA_DIR", str(ROOT / "data"))).expanduser()
    return p if p.is_absolute() else ROOT / p


def web_data_dir() -> Path:
    """Where the map's forecast files are written (VAILSNOW_WEB_DATA_DIR for a second resort)."""
    p = Path(os.environ.get("VAILSNOW_WEB_DATA_DIR", str(ROOT / "web" / "app" / "data"))).expanduser()
    return p if p.is_absolute() else ROOT / p


def default_resort() -> str:
    return os.environ.get("VAILSNOW_RESORT", "vail")


@dataclass(frozen=True)
class Zone:
    index: int          # 1-based id used in rasters (0 = no zone)
    id: str
    name: str
    group: str
    lifts: tuple[str, ...]
    trails: tuple[str, ...]


@dataclass(frozen=True)
class Band:
    index: int          # 1-based
    id: str
    label: str
    top_m: float        # upper bound, metres


@dataclass(frozen=True)
class Resort:
    id: str
    name: str
    epsg: int
    osm_boundary_name: str
    region: dict
    fine: dict
    sources: dict
    bands: tuple[Band, ...]
    points: tuple[dict, ...]
    zones: tuple[Zone, ...]
    snotel: tuple[dict, ...] = ()
    valley: tuple[dict, ...] = ()
    wind_check: dict | None = None      # a ridge-level station used to check forecast gusts
    lift_wind: dict | None = None       # gust thresholds for lift holds, and opening hours
    alerts: dict | None = None          # powder-alert thresholds
    view: dict | None = None            # what the map opens on

    def source_path(self, key: str) -> Path:
        return game_dir() / self.sources[key]

    def out_dir(self) -> Path:
        p = data_dir() / "terrain" / self.id
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_resort(resort_id: str | None = None) -> Resort:
    raw = tomllib.loads((ROOT / "resorts" / f"{resort_id or default_resort()}.toml").read_text())
    zones = tuple(
        Zone(i + 1, z["id"], z["name"], z["group"], tuple(z.get("lifts", [])), tuple(z.get("trails", [])))
        for i, z in enumerate(raw["zones"])
    )
    bands = tuple(Band(i + 1, b["id"], b["label"], b["top_ft"] / FT_PER_M) for i, b in enumerate(raw["bands"]))
    ids = [z.id for z in zones]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate zone ids in resort config")
    seen: dict[str, str] = {}
    for z in zones:
        for t in z.trails:
            if t in seen:
                raise ValueError(f"trail {t!r} is listed in both {seen[t]!r} and {z.id!r}")
            seen[t] = z.id
    return Resort(raw["id"], raw["name"], raw["epsg"], raw["osm_boundary_name"], raw["region"], raw["fine"],
                  raw["sources"], bands, tuple(raw.get("points", [])), zones,
                  tuple(raw.get("snotel", [])), tuple(raw.get("valley", [])),
                  raw.get("wind_check"), raw.get("lift_wind"), raw.get("alerts"), raw.get("view"))
