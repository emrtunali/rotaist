"""Plan the evening before, drive the next day: the back-test behind every number in the README.

For each held-out weekday of January 2025 we draw that day's orders, plan them with every method using
only what that method would know the evening before, and then *execute* each plan on the traffic that
actually happened that day (İBB hourly speeds). Promised ETAs are the planned arrival times.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmnx as ox
import pandas as pd

from .baseline import solve_ortools
from .demand import DEPOT, Orders, building_weights, sample_orders
from .forecast import HOLIDAYS, Forecaster
from .network import Network, congestion_ratio, free_flow_reference
from .solver import ALNS, Instance, departure, execute
from .traffic import Traffic

ROOT = Path(__file__).resolve().parents[1]
SHIFT = (8 * 3600, 22 * 3600)

METHODS = {
    "ortools_freeflow": "OR-Tools · free-flow matrix",
    "ortools_average": "OR-Tools · typical daytime matrix",
    "alns_average": "Rotaist solver · typical daytime matrix",
    "rotaist": "Rotaist · forecast, time-dependent",
    "oracle": "Oracle · actual traffic known in advance",
}


@dataclass
class World:
    traffic: Traffic
    ratio: np.ndarray
    ff: np.ndarray
    net: Network
    forecaster: Forecaster
    buildings: pd.DataFrame
    train: np.ndarray
    test_days: list[int]

    @classmethod
    def load(cls, root: Path = ROOT) -> World:
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        tr = Traffic.load(root / "data/processed/traffic.npz")
        train = np.array([i for i, d in enumerate(tr.dates) if d < "2025-01-01"])
        ff = free_flow_reference(tr, train)
        ratio = congestion_ratio(tr, ff)
        net = Network.from_graph(ox.load_graphml(root / "data/raw/anadolu_drive.graphml"), tr)
        fc = Forecaster.fit(ratio, tr.dates, train, tr.lat, tr.lon)
        b = building_weights(pd.read_parquet(root / "data/raw/buildings.parquet"))
        dow = pd.to_datetime(pd.Series(tr.dates)).dt.dayofweek
        test = [i for i, d in enumerate(tr.dates) if d >= "2025-01-01" and dow[i] < 5 and d not in HOLIDAYS]
        return cls(tr, ratio, ff, net, fc, b, train, test)

    def filled(self, r: np.ndarray) -> np.ndarray:
        """Fill unobserved cells with the training mean so every street has a speed."""
        return np.where(np.isfinite(r), r, self.forecaster.daily[None, :])

    def ratio_for(self, d: int, method: str) -> np.ndarray:
        tr = self.traffic
        if method == "actual":
            return self.filled(self.ratio[d])
        return self.filled(self.forecaster.predict(self.ratio, tr.dates, d, tr.lat, tr.lon, method))


def instance(T: np.ndarray, o: Orders, vans: int, capacity: int) -> Instance:
    return Instance(T, np.r_[SHIFT[0], o.tw_start], np.r_[SHIFT[1], o.tw_end], np.r_[0, o.service],
                    np.r_[0, o.size], vans, capacity)


def static(T: np.ndarray) -> np.ndarray:
    """One matrix for the whole day — the 08:00–21:00 average — repeated for every hour.

    Built from the hour-of-week profile, so it is the "typical traffic" matrix a static planner would buy.
    """
    return np.repeat(T[8:21].mean(axis=0, keepdims=True), T.shape[0], axis=0)


def planned(T: np.ndarray, routes: list[list[int]], inst: Instance) -> tuple[list[float], dict]:
    """The plan as the planner sees it: departure times and promised arrivals under its own matrix."""
    a = inst.tw_start.astype(float)
    deps = [float(departure(np.ascontiguousarray(T), np.array(r, dtype=np.int64), len(r), a[0], a)) if r else 0.0
            for r in routes]
    return deps, execute(T, routes, inst, deps)


def run_day(w: World, d: int, n_orders: int, vans: int, capacity: int, seed: int,
            alns_iters: int = 4000, alns_seconds: float = 60, ortools_seconds: int = 30,
            methods: tuple[str, ...] = tuple(METHODS)) -> dict:
    o = sample_orders(w.buildings, n_orders, seed)
    nodes = w.net.nearest_node(np.r_[DEPOT[0], o.lon], np.r_[DEPOT[1], o.lat])
    T_act = w.net.matrices(nodes, w.ratio_for(d, "actual"))
    T_fc = w.net.matrices(nodes, w.ratio_for(d, "model"))
    T_avg = static(w.net.matrices(nodes, w.ratio_for(d, "hour_of_week")))
    T_ff = static(w.net.matrices(nodes, np.ones((24, len(w.ff)))))
    inst = instance(T_act, o, vans, capacity)
    out = {"date": w.traffic.dates[d], "seed": seed, "orders": n_orders, "vans": vans, "plans": {}}
    for m in methods:
        if m == "ortools_freeflow":
            T_plan, routes = T_ff, solve_ortools(inst, T_ff[0], ortools_seconds)
        elif m == "ortools_average":
            T_plan, routes = T_avg, solve_ortools(inst, T_avg[0], ortools_seconds)
        else:
            T_plan = {"alns_average": T_avg, "rotaist": T_fc, "oracle": T_act}[m]
            p_inst = instance(T_plan, o, vans, capacity)
            sol, _ = ALNS(p_inst, seed).solve(alns_iters, alns_seconds)
            routes = sol.routes
        deps, plan = planned(T_plan, routes, inst)
        real = execute(T_act, routes, inst, deps)
        eta_err = np.array([real["arrivals"][c] - plan["arrivals"][c] for c in plan["arrivals"]]) / 60
        out["plans"][m] = {
            "routes": routes, "departures": deps,
            "planned": {k: plan[k] for k in ("on_time_pct", "late_minutes", "duty_hours")},
            "actual": {k: real[k] for k in ("on_time_pct", "late_orders", "late_minutes", "duty_hours",
                                            "overtime_minutes", "vans")},
            "eta_mae_min": float(np.abs(eta_err).mean()), "eta_p90_min": float(np.percentile(np.abs(eta_err), 90)),
            "eta_bias_min": float(eta_err.mean()),
            "arrivals": real["arrivals"], "promised": plan["arrivals"],
        }
    out["orders_data"] = {"lon": o.lon.tolist(), "lat": o.lat.tolist(), "tw_start": o.tw_start.tolist(),
                          "tw_end": o.tw_end.tolist(), "size": o.size.tolist(), "nodes": nodes.tolist()}
    return out
