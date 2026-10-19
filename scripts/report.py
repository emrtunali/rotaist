"""Turn the back-test into results tables and the data behind the web app.

    python scripts/report.py

Writes results/summary.json, results/RESULTS.md and site/data/*.json.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from rotaist.demand import sample_orders
from rotaist.experiment import METHODS, World
from rotaist.forecast import evaluate

ROOT = Path(__file__).resolve().parents[1]
RES, SITE = ROOT / "results", ROOT / "site/data"
SHOW_VANS = 5
SHOW_PLANS = ("ortools_freeflow", "ortools_average", "rotaist")
FORECASTS = {"free_flow": "No traffic (free flow)", "daily_mean": "Cell average, all day",
             "last_week": "Same hour last week", "hour_of_week": "Hour-of-week profile",
             "model": "Rotaist gradient boosting"}


def load_runs() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((RES / "backtest").glob("*.json"))]


def aggregate(runs: list[dict]) -> dict:
    by = defaultdict(lambda: defaultdict(list))
    for r in runs:
        for m, p in r["plans"].items():
            row = by[(r["vans"], m)]
            for k, v in p["actual"].items():
                row[k].append(v)
            for k in ("eta_mae_min", "eta_p90_min", "eta_bias_min"):
                row[k].append(p[k])
            row["planned_on_time_pct"].append(p["planned"]["on_time_pct"])
    out = {}
    for (vans, m), row in sorted(by.items()):
        out.setdefault(str(vans), {})[m] = {k: float(np.mean(v)) for k, v in row.items()} | {
            "days": len(row["on_time_pct"]),
            "worst_day_on_time_pct": float(np.min(row["on_time_pct"])),
        }
    return out


def paired_wins(runs: list[dict], a: str, b: str) -> dict:
    """On how many (day, fleet) cases is plan a at least as punctual as plan b."""
    diff = [r["plans"][a]["actual"]["late_minutes"] - r["plans"][b]["actual"]["late_minutes"] for r in runs]
    return {"cases": len(diff), "a_better_or_equal": int(sum(d <= 0 for d in diff))}


_edge_cache: dict = {}


def edge_times(w: World, ratio_h: np.ndarray, key) -> dict:
    if key not in _edge_cache:
        sec = w.net.edge_seconds(ratio_h)
        best: dict = {}
        for u, v, s in zip(w.net.u.tolist(), w.net.v.tolist(), sec.tolist()):
            if s < best.get((u, v), np.inf):
                best[(u, v)] = s
        _edge_cache[key] = best
    return _edge_cache[key]


def leg_geometry(w: World, day: str, nodes: list[int], route: list[int], dep: float, arrivals: dict,
                 service: list[float], tw_start: list[float], ratio: np.ndarray) -> tuple[list, list]:
    """Street-level polyline of a van's day with a timestamp per vertex (actual execution).

    Each leg follows the fastest street path at its departure hour; vertex times are spread along the
    leg in proportion to edge travel times and scaled to the simulated arrival time.
    """
    coords, times = [], []
    stops = [0] + route + [0]
    t = dep
    for k in range(len(stops) - 1):
        a, b = nodes[stops[k]], nodes[stops[k + 1]]
        h = min(int(t // 3600), 23)
        path = w.net.path(a, b, ratio[h])
        et = edge_times(w, ratio[h], (day, h))
        cum = np.r_[0, np.cumsum([et.get((p, q), 0.0) for p, q in zip(path[:-1], path[1:])])]
        nxt = stops[k + 1]
        dur = (arrivals[nxt] - t) if nxt else cum[-1]
        frac = cum / cum[-1] if cum[-1] > 0 else np.linspace(0, 1, len(cum))
        coords += np.round(w.net.xy[path], 5).tolist()
        times += np.round(t + frac * dur).tolist()
        t += dur
        if nxt:
            t = max(t, tw_start[nxt]) + service[nxt]
            coords.append(coords[-1]); times.append(round(t))
    return coords, times


def export_day(w: World, run: dict) -> dict:
    d = w.traffic.dates.index(run["date"])
    ratio = w.ratio_for(d, "actual")
    od = run["orders_data"]
    nodes = od["nodes"]
    o = sample_orders(w.buildings, run["orders"], run["seed"])  # same seed → same orders
    assert np.allclose(o.lon, od["lon"])
    service = [0.0] + o.service.astype(float).tolist()
    plans = {}
    for m in SHOW_PLANS:
        p = run["plans"][m]
        arrivals = {int(k): v for k, v in p["arrivals"].items()}
        promised = {int(k): v for k, v in p["promised"].items()}
        vans = []
        for k, r in enumerate(p["routes"]):
            if not r:
                continue
            coords, times = leg_geometry(w, run["date"], nodes, r, p["departures"][k], arrivals, service,
                                         [0.0] + od["tw_start"], ratio)
            vans.append({"stops": r, "coords": coords, "times": times})
        plans[m] = {"label": METHODS[m], "kpi": p["actual"] | {"eta_mae_min": p["eta_mae_min"]},
                    "vans": vans,
                    "arrival": [round(arrivals.get(i, 0)) for i in range(1, len(od["lon"]) + 1)],
                    "promised": [round(promised.get(i, 0)) for i in range(1, len(od["lon"]) + 1)]}
    return {"date": run["date"], "vans": run["vans"], "orders": len(od["lon"]),
            "depot": w.net.xy[nodes[0]].round(5).tolist(),
            "stops": {"lon": np.round(od["lon"], 5).tolist(), "lat": np.round(od["lat"], 5).tolist(),
                      "tw_start": od["tw_start"], "tw_end": od["tw_end"]},
            "traffic": {"lon": w.traffic.lon.round(5).tolist(), "lat": w.traffic.lat.round(5).tolist(),
                        "ratio": np.round(ratio, 2).tolist(),
                        "forecast": np.round(w.ratio_for(d, "model"), 2).tolist()},
            "plans": plans}


def markdown(summary: dict) -> str:
    lines = ["# Rotaist — results", "",
             f"Back-test on {summary['backtest_days']} held-out weekdays of January 2025, "
             f"{summary['orders']} synthetic orders per day, plans executed on that day's real İBB traffic.", ""]
    for vans, rows in summary["backtest"].items():
        lines += [f"## {vans} vans", "",
                  "| Planner | On time (real) | On time (planned) | Late min/day | Duty h | ETA error (MAE) "
                  "| ETA bias | Worst day |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for m, label in METHODS.items():
            r = rows.get(m)
            if r:
                lines.append(f"| {label} | {r['on_time_pct']:.1f} % | {r['planned_on_time_pct']:.1f} % | "
                             f"{r['late_minutes']:.0f} | {r['duty_hours']:.1f} | {r['eta_mae_min']:.1f} min | "
                             f"{r['eta_bias_min']:+.1f} min | {r['worst_day_on_time_pct']:.1f} % |")
        lines.append("")
    lines += ["## Day-ahead traffic forecast (January 2025, vehicle-weighted)", "",
              "| Forecast | Speed MAE | at 08:00 | at 18:00 |", "|---|---:|---:|---:|"]
    for k, label in FORECASTS.items():
        f = summary["forecast"][k]
        lines.append(f"| {label} | {f['mae_kmh']:.2f} km/h | {f['by_hour'][8]:.1f} | {f['by_hour'][18]:.1f} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    w = World.load()
    tr = w.traffic
    test = np.array([i for i, d in enumerate(tr.dates) if d >= "2025-01-01"])
    fc = evaluate(w.forecaster, w.ratio, tr.dates, test, tr.lat, tr.lon, w.ff, tr.vehicles)
    runs = load_runs()
    with np.errstate(all="ignore"):
        hourly = np.nanmedian(tr.speed[w.train], axis=(0, 2))
    summary = {
        "backtest_days": len({r["date"] for r in runs}), "orders": runs[0]["orders"],
        "backtest": aggregate(runs),
        "wins_vs_ortools_average": paired_wins(runs, "rotaist", "ortools_average"),
        "wins_vs_alns_average": paired_wins(runs, "rotaist", "alns_average"),
        "forecast": fc,
        "network": {"nodes": int(len(w.net.node_ids)), "edges": int(len(w.net.u)), "cells": len(tr.cells),
                    "days": len(tr.dates), "median_speed_by_hour": np.round(hourly, 1).tolist()},
        "per_day": [{"date": r["date"], "vans": r["vans"],
                     **{m: {"on_time_pct": p["actual"]["on_time_pct"], "late_minutes": p["actual"]["late_minutes"],
                            "eta_mae_min": p["eta_mae_min"]} for m, p in r["plans"].items()}} for r in runs],
    }
    RES.mkdir(exist_ok=True)
    (RES / "summary.json").write_text(json.dumps(summary, indent=1))
    (RES / "RESULTS.md").write_text(markdown(summary))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "summary.json").write_text(json.dumps(summary))
    show = [r for r in runs if r["vans"] == SHOW_VANS] or runs
    # showcase: the day where static planning hurt most, plus a typical day
    show.sort(key=lambda r: r["plans"]["ortools_average"]["actual"]["late_minutes"])
    picks = [show[-1], show[len(show) // 2]]
    days = []
    for r in picks:
        (SITE / f"day_{r['date']}.json").write_text(json.dumps(export_day(w, r), separators=(",", ":")))
        days.append(r["date"])
    (SITE / "days.json").write_text(json.dumps(days))
    print(markdown(summary))


if __name__ == "__main__":
    main()
