import numpy as np
import pytest

from rotaist.baseline import solve_ortools
from rotaist.solver import ALNS, Instance, execute, route_cost, travel


def grid_instance(n=30, vans=3, rush=1.0, seed=0) -> Instance:
    rng = np.random.default_rng(seed)
    xy = np.r_[[[0.5, 0.5]], rng.random((n, 2))] * 8000            # metres
    d = np.abs(xy[:, None, :] - xy[None, :, :]).sum(-1)            # Manhattan
    T = np.repeat((d / (30 / 3.6))[None], 24, axis=0)              # 30 km/h
    T[16:20] *= rush                                               # evening rush
    a = np.r_[8 * 3600, rng.choice([9, 11, 13, 15, 17, 19], n) * 3600]
    b = a + np.r_[14 * 3600, np.full(n, 2 * 3600)]
    return Instance(T, a.astype(float), b.astype(float), np.r_[0, np.full(n, 180.0)], np.r_[0, np.ones(n, int)],
                    vans, 15)


def test_travel_interpolates_between_hour_centres():
    T = np.zeros((24, 2, 2))
    T[8, 0, 1], T[9, 0, 1] = 100, 200
    assert travel(T, 0, 1, 8.5 * 3600) == pytest.approx(100)
    assert travel(T, 0, 1, 9.0 * 3600) == pytest.approx(150)
    assert travel(T, 0, 1, 9.5 * 3600) == pytest.approx(200)


def test_alns_serves_every_order_once_and_respects_capacity():
    inst = grid_instance()
    sol, info = ALNS(inst, seed=1).solve(iterations=300, time_limit=30)
    served = sorted(c for r in sol.routes for c in r)
    assert served == list(range(1, inst.n + 1))
    assert all(inst.size[r].sum() <= inst.capacity for r in sol.routes if r)
    assert info["history"][-1] <= info["history"][0]


def test_alns_improves_on_construction():
    inst = grid_instance(n=40)
    s = ALNS(inst, seed=2)
    start = s.initial()
    best, _ = s.solve(iterations=400, time_limit=30, start=start)
    assert best.cost <= start.cost


def test_time_dependent_plan_beats_static_plan_in_rush_hour():
    """A plan made on the true time-dependent matrix should not lose to one made on a static matrix."""
    real = grid_instance(n=35, rush=2.5, seed=3)
    static = Instance(np.repeat(real.T[8:21].mean(0, keepdims=True), 24, 0), real.tw_start, real.tw_end,
                      real.service, real.size, real.n_vehicles, real.capacity)
    td, _ = ALNS(real, 0).solve(800, 30)
    st, _ = ALNS(static, 0).solve(800, 30)
    cost = lambda routes: sum(route_cost(real.T, np.array(r, dtype=np.int64), len(r), real.tw_start, real.tw_end,
                                         real.service) for r in routes)
    assert cost(td.routes) <= cost(st.routes) * 1.02


def test_execute_reports_lateness():
    inst = grid_instance(n=5, vans=1)
    T = inst.T * 50                                                # everything takes forever
    r = execute(T, [[1, 2, 3, 4, 5]], inst)
    assert r["late_orders"] > 0 and r["on_time_pct"] < 100
    assert set(r["arrivals"]) == {1, 2, 3, 4, 5}


def test_ortools_baseline_returns_a_complete_plan():
    inst = grid_instance(n=20)
    routes = solve_ortools(inst, inst.T[10], seconds=2)
    assert sorted(c for r in routes for c in r) == list(range(1, 21))
