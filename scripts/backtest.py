"""Back-test every planner on every held-out January 2025 weekday and fleet size.

    python scripts/backtest.py --workers 5            # ~30 min on a laptop
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/backtest"
_world = None


def job(args):
    global _world
    from rotaist.experiment import World, run_day
    d, vans, n_orders, capacity = args
    if _world is None:
        _world = World.load()
    if (OUT / f"{_world.traffic.dates[d]}_v{vans}.json").exists():
        return _world.traffic.dates[d], vans, {}
    r = run_day(_world, d, n_orders, vans, capacity, seed=d, alns_seconds=40, ortools_seconds=20)
    (OUT / f"{r['date']}_v{vans}.json").write_text(json.dumps(r))
    return r["date"], vans, {m: p["actual"]["on_time_pct"] for m, p in r["plans"].items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--vans", type=int, nargs="+", default=[4, 5, 6])
    ap.add_argument("--orders", type=int, default=250)
    ap.add_argument("--capacity", type=int, default=150)
    a = ap.parse_args()
    from rotaist.experiment import World
    days = World.load().test_days
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(d, v, a.orders, a.capacity) for v in a.vans for d in days]
    with ProcessPoolExecutor(a.workers) as ex:
        for date, vans, ot in ex.map(job, jobs):
            print(date, vans, {k: round(v, 1) for k, v in ot.items()}, flush=True)


if __name__ == "__main__":
    main()
