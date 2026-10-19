"""İBB hourly traffic density data → a compact (day, hour, cell) speed tensor for the study area.

Source: İstanbul Büyükşehir Belediyesi Açık Veri Portalı, "Hourly Traffic Density Data Set"
(average, minimum and maximum vehicle speed and vehicle count per geohash-6 cell per hour;
İBB Open Data License).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Traffic:
    cells: list[str]          # geohash-6 ids
    lat: np.ndarray           # cell centre latitude
    lon: np.ndarray
    dates: list[str]          # ISO dates, one per day
    speed: np.ndarray         # (days, 24, cells) average speed km/h, NaN where no observation
    vehicles: np.ndarray      # (days, 24, cells) vehicle count, 0 where no observation

    def save(self, path: Path) -> None:
        np.savez_compressed(path, cells=np.array(self.cells), lat=self.lat, lon=self.lon,
                            dates=np.array(self.dates), speed=self.speed.astype(np.float32),
                            vehicles=self.vehicles.astype(np.float32))

    @classmethod
    def load(cls, path: Path) -> Traffic:
        z = np.load(path)
        return cls(list(z["cells"]), z["lat"], z["lon"], list(z["dates"]), z["speed"], z["vehicles"])


def load_months(files: list[Path], bbox: tuple[float, float, float, float], margin: float = 0.01) -> Traffic:
    """Read monthly CSVs, keep cells inside the bounding box (south, west, north, east)."""
    s, w, n, e = bbox
    frames = []
    for f in files:
        df = pd.read_csv(f, usecols=["DATE_TIME", "LATITUDE", "LONGITUDE", "GEOHASH", "AVERAGE_SPEED",
                                     "NUMBER_OF_VEHICLES"], encoding="utf-8-sig")
        df = df[(df.LATITUDE >= s - margin) & (df.LATITUDE <= n + margin)
                & (df.LONGITUDE >= w - margin) & (df.LONGITUDE <= e + margin)]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(df.DATE_TIME)
    df["date"], df["hour"] = ts.dt.strftime("%Y-%m-%d"), ts.dt.hour
    cells = sorted(df.GEOHASH.unique())
    cidx = {c: i for i, c in enumerate(cells)}
    dates = sorted(df.date.unique())
    didx = {d: i for i, d in enumerate(dates)}
    speed = np.full((len(dates), 24, len(cells)), np.nan, dtype=np.float32)
    vehicles = np.zeros((len(dates), 24, len(cells)), dtype=np.float32)
    di, hi, ci = df.date.map(didx).to_numpy(), df.hour.to_numpy(), df.GEOHASH.map(cidx).to_numpy()
    speed[di, hi, ci] = df.AVERAGE_SPEED.to_numpy()
    vehicles[di, hi, ci] = df.NUMBER_OF_VEHICLES.to_numpy()
    centre = df.groupby("GEOHASH")[["LATITUDE", "LONGITUDE"]].first().loc[cells]
    return Traffic(cells, centre.LATITUDE.to_numpy(), centre.LONGITUDE.to_numpy(), dates, speed, vehicles)
