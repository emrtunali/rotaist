"""Command line: plan one day and compare it with the static baselines on real traffic.

    python -m rotaist plan --date 2025-01-14 --orders 250 --vans 5
    python -m rotaist forecast
"""

from __future__ import annotations

import argparse

import numpy as np

from .experiment import METHODS, World, run_day
from .forecast import evaluate


def main() -> None:
    ap = argparse.ArgumentParser(prog="rotaist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="plan a held-out day with every method and drive it on real traffic")
    p.add_argument("--date", default="2025-01-14")
    p.add_argument("--orders", type=int, default=250)
    p.add_argument("--vans", type=int, default=5)
    p.add_argument("--capacity", type=int, default=150)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--seconds", type=float, default=40, help="ALNS time limit per plan")
    sub.add_parser("forecast", help="day-ahead traffic forecast accuracy on January 2025")
    a = ap.parse_args()

    w = World.load()
    if a.cmd == "forecast":
        tr = w.traffic
        test = np.array([i for i, d in enumerate(tr.dates) if d >= "2025-01-01"])
        for m, r in evaluate(w.forecaster, w.ratio, tr.dates, test, tr.lat, tr.lon, w.ff, tr.vehicles).items():
            print(f"{m:14s} speed MAE {r['mae_kmh']:.2f} km/h")
        return

    d = w.traffic.dates.index(a.date)
    r = run_day(w, d, a.orders, a.vans, a.capacity, a.seed, alns_seconds=a.seconds)
    print(f"{a.date}: {a.orders} orders, {a.vans} vans — plans driven on that day's real traffic\n")
    print(f"{'planner':44s} {'on time':>8s} {'late min':>9s} {'duty h':>7s} {'ETA err':>8s}")
    for m, p in r["plans"].items():
        k = p["actual"]
        print(f"{METHODS[m]:44s} {k['on_time_pct']:7.1f}% {k['late_minutes']:9.0f} {k['duty_hours']:7.1f} "
              f"{p['eta_mae_min']:7.1f}m")


if __name__ == "__main__":
    main()
