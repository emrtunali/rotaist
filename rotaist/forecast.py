"""Day-ahead traffic forecast: tomorrow's congestion ratio for every cell and hour.

A dispatcher plans the evening before, so the forecast for day d may use data up to day d-1 only.

Baselines:
* ``free_flow``     — no congestion at all (what distance- or speed-limit-based planners assume),
* ``daily_mean``    — the cell's average ratio over the training period, the same at every hour
                      (what a planner with one fixed travel-time matrix per day assumes),
* ``hour_of_week``  — the cell's average ratio for that weekday and hour,
* ``last_week``     — the same cell, weekday and hour one week earlier.
Model: gradient-boosted trees on hour-of-week profile, last week, yesterday, calendar and location.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

# Public holidays and eves in the data window (Türkiye)
HOLIDAYS = {"2024-10-29": 1.0, "2024-10-28": 0.5, "2025-01-01": 1.0, "2024-12-31": 0.5}


@dataclass
class Forecaster:
    profile: np.ndarray        # (7, 24, cells) hour-of-week mean ratio from training days
    daily: np.ndarray          # (cells,) overall mean ratio
    model: HistGradientBoostingRegressor | None = None

    @staticmethod
    def _dow(dates: list[str]) -> np.ndarray:
        return pd.to_datetime(pd.Series(dates)).dt.dayofweek.to_numpy()

    @classmethod
    def fit(cls, ratio: np.ndarray, dates: list[str], train_idx: np.ndarray, lat: np.ndarray, lon: np.ndarray,
            seed: int = 0) -> Forecaster:
        warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)
        dow = cls._dow(dates)
        prof = np.full((7, 24, ratio.shape[2]), np.nan)
        with np.errstate(all="ignore"):
            for k in range(7):
                days = train_idx[(dow[train_idx] == k) & np.array([dates[i] not in HOLIDAYS for i in train_idx])]
                prof[k] = np.nanmean(ratio[days], axis=0)
            daily = np.nanmean(ratio[train_idx], axis=(0, 1))
        prof = np.where(np.isfinite(prof), prof, daily[None, None, :])
        f = cls(prof, daily)
        X, y = f._features(ratio, dates, train_idx[train_idx >= 7], lat, lon, with_target=True)
        ok = np.isfinite(y)
        f.model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=63,
                                                l2_regularization=1.0, random_state=seed)
        f.model.fit(X[ok], y[ok])
        return f

    def _features(self, ratio, dates, days, lat, lon, with_target=False):
        warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)
        dow = self._dow(dates)
        rows, ys = [], []
        C = ratio.shape[2]
        for d in days:
            hol = HOLIDAYS.get(dates[d], 0.0)
            lw = ratio[d - 7] if d >= 7 else np.full((24, C), np.nan)
            yd = ratio[d - 1] if d >= 1 else np.full((24, C), np.nan)
            yday_mean = np.nanmean(yd, axis=0) if np.isfinite(yd).any() else np.full(C, np.nan)
            for h in range(24):
                rows.append(np.c_[
                    self.profile[dow[d], h], self.daily, lw[h], yd[h], yday_mean - self.daily,
                    np.full(C, h), np.full(C, dow[d]), np.full(C, hol), lat, lon,
                ])
                if with_target:
                    ys.append(ratio[d, h])
        X = np.vstack(rows)
        return (X, np.concatenate(ys)) if with_target else X

    def predict(self, ratio: np.ndarray, dates: list[str], d: int, lat, lon, method: str = "model") -> np.ndarray:
        """(24, cells) forecast ratio for day index d."""
        dow = self._dow(dates)[d]
        if method == "free_flow":
            return np.ones((24, ratio.shape[2]))
        if method == "daily_mean":
            return np.tile(self.daily, (24, 1))
        if method == "hour_of_week":
            return self.profile[dow].copy()
        if method == "last_week":
            lw = ratio[d - 7]
            return np.where(np.isfinite(lw), lw, self.profile[dow])
        X = self._features(ratio, dates, [d], lat, lon)
        return np.clip(self.model.predict(X).reshape(24, -1), 0.05, 1.2)


def evaluate(f: Forecaster, ratio, dates, test_idx, lat, lon, ff_kmh: np.ndarray, weights: np.ndarray) -> dict:
    """Mean absolute error of the forecast speed (km/h) on test days, vehicle-weighted and per hour."""
    methods = ["free_flow", "daily_mean", "hour_of_week", "last_week", "model"]
    out = {}
    for m in methods:
        err, w, by_hour = [], [], np.zeros(24)
        hour_w = np.zeros(24)
        for d in test_idx:
            pred = f.predict(ratio, dates, d, lat, lon, m) * ff_kmh[None, :]
            truth = ratio[d] * ff_kmh[None, :]
            ok = np.isfinite(truth)
            e = np.abs(pred - truth)
            err.append(e[ok]); w.append(weights[d][ok])
            by_hour += np.nansum(np.where(ok, e * weights[d], 0), axis=1)
            hour_w += np.nansum(np.where(ok, weights[d], 0), axis=1)
        e, ww = np.concatenate(err), np.concatenate(w)
        out[m] = {"mae_kmh": float(np.average(e, weights=ww)), "mae_kmh_unweighted": float(e.mean()),
                  "by_hour": (by_hour / np.maximum(hour_w, 1)).round(2).tolist()}
    return out
