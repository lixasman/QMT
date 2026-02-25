from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .constants import KDE_TOP_N_ZONES
from .types import DenseZone


@dataclass(frozen=True)
class KdeZones:
    dense_zones: list[DenseZone]


def load_kde_zones(*, etf_code: str, trade_date: date, base_dir: str = "data/kde_zones") -> KdeZones:
    p = Path(str(base_dir)) / f"{str(etf_code)}_{trade_date.isoformat()}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    zones = []
    for item in raw.get("dense_zones", []):
        zones.append(
            DenseZone(
                upper=float(item["upper"]),
                lower=float(item["lower"]),
                strength=float(item.get("strength", 0.0)),
            )
        )
    zones.sort(key=lambda z: float(z.strength), reverse=True)
    top = zones[: int(KDE_TOP_N_ZONES)]
    return KdeZones(dense_zones=list(top))


def find_nearest_support(*, zones: list[DenseZone], price: float) -> Optional[float]:
    px = float(price)
    candidates = [float(z.upper) for z in list(zones) if float(z.upper) <= px]
    if not candidates:
        return None
    return float(max(candidates))

