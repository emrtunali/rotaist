"""Synthetic but grounded daily orders.

Order *locations* are drawn in proportion to residential floor area from real OpenStreetMap buildings
(footprint × levels, 4 levels when untagged), so dense Kadıköy blocks get many more parcels than parks.
Delivery *slots* are 2-hour windows between 09:00 and 21:00, weighted toward the evening the way
e-commerce customers pick them. Order sizes and service times are random within realistic ranges.
Nothing here is real customer data — see the README.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

NON_RESIDENTIAL = {"shed", "school", "commercial", "construction", "mosque", "industrial", "roof", "retail",
                   "garage", "garages", "warehouse", "hospital", "university", "church", "office", "public",
                   "kiosk", "service", "hotel", "civic", "government", "train_station", "transportation",
                   "stadium", "sports_centre", "greenhouse", "ruins", "collapsed", "bridge", "parking",
                   "kindergarten", "college", "religious", "toilets", "hangar", "barn", "farm_auxiliary"}
SLOTS = [(9, 11), (11, 13), (13, 15), (15, 17), (17, 19), (19, 21)]
SLOT_WEIGHTS = np.array([0.12, 0.13, 0.13, 0.15, 0.24, 0.23])

# A hypothetical micro-hub in Kozyatağı, next to the D-100 — central to the three districts.
DEPOT = (29.0985, 40.9770)


@dataclass
class Orders:
    lon: np.ndarray
    lat: np.ndarray
    tw_start: np.ndarray   # seconds since midnight
    tw_end: np.ndarray
    service: np.ndarray    # seconds
    size: np.ndarray       # parcels

    def __len__(self) -> int:
        return len(self.lon)


def building_weights(buildings: pd.DataFrame) -> pd.DataFrame:
    b = buildings[~buildings["building"].isin(NON_RESIDENTIAL)].copy()
    lv = pd.to_numeric(b.get("building:levels"), errors="coerce").fillna(4).clip(1, 30)
    b["weight"] = b["area_m2"].clip(30, 5000) * lv
    return b[b["weight"] > 0][["lon", "lat", "weight"]].reset_index(drop=True)


def sample_orders(weighted: pd.DataFrame, n: int, seed: int) -> Orders:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(weighted), size=n, p=weighted["weight"] / weighted["weight"].sum())
    slot = rng.choice(len(SLOTS), size=n, p=SLOT_WEIGHTS / SLOT_WEIGHTS.sum())
    a = np.array([SLOTS[s][0] for s in slot]) * 3600
    b = np.array([SLOTS[s][1] for s in slot]) * 3600
    service = rng.integers(120, 241, size=n)              # 2-4 minutes at the door
    size = rng.choice([1, 1, 1, 1, 2, 2, 3], size=n)       # parcels per order
    return Orders(weighted["lon"].to_numpy()[idx], weighted["lat"].to_numpy()[idx], a, b, service, size)
