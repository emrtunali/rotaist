"""Rotaist's solver: Adaptive Large Neighbourhood Search for the time-dependent VRP with time windows.

Travel time depends on *when* a van leaves: ``T[h, i, j]`` holds the shortest time from stop i to j for a
departure in hour h, and departures between hour centres are interpolated linearly. A plan that looks
perfect on an average-traffic matrix can therefore turn out late once the evening rush is taken into
account, and this solver sees that while it plans.

Objective (seconds): duty time of every van (leave depot → return)
                     + LATE_WEIGHT × lateness after each time window
                     + OVERTIME_WEIGHT × time back after the end of the shift.
Capacity is a hard constraint. Vans leave the depot as late as possible for their first stop.

The search loop is plain Python; route simulation and insertion scoring are compiled with numba.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from numba import njit

LATE_WEIGHT = 5.0
OVERTIME_WEIGHT = 3.0


@dataclass
class Instance:
    T: np.ndarray            # (24, n+1, n+1) float64 seconds; index 0 is the depot
    tw_start: np.ndarray     # (n+1,) seconds since midnight; depot = shift start
    tw_end: np.ndarray       # (n+1,) depot = shift end
    service: np.ndarray      # (n+1,) seconds
    size: np.ndarray         # (n+1,) parcels
    n_vehicles: int
    capacity: int

    @property
    def n(self) -> int:
        return len(self.tw_start) - 1


# ---------------------------------------------------------------- compiled core

@njit(cache=True, inline="always")
def travel(T, i, j, t):
    h = t / 3600.0 - 0.5
    if h <= 0.0:
        return T[0, i, j]
    h0 = int(h)
    if h0 >= T.shape[0] - 1:
        return T[T.shape[0] - 1, i, j]
    f = h - h0
    return T[h0, i, j] * (1.0 - f) + T[h0 + 1, i, j] * f


@njit(cache=True)
def departure(T, seq, n, a0, a):
    """Latest sensible departure: arrive at the first stop when its window opens (never before shift)."""
    if n == 0:
        return a0
    first = seq[0]
    t = a[first] - T[min(int(a[first] / 3600.0), T.shape[0] - 1), 0, first]
    return max(a0, t)


@njit(cache=True)
def simulate(T, seq, n, a, b, s, dep):
    """Run a route; returns (return time, total lateness, per-stop arrival times)."""
    arr = np.empty(n)
    t = dep
    prev = 0
    late = 0.0
    for k in range(n):
        c = seq[k]
        t += travel(T, prev, c, t)
        arr[k] = t
        if t < a[c]:
            t = a[c]
        if t > b[c]:
            late += t - b[c]
        t += s[c]
        prev = c
    if n > 0:
        t += travel(T, prev, 0, t)
    return t, late, arr


@njit(cache=True)
def route_cost(T, seq, n, a, b, s):
    if n == 0:
        return 0.0
    dep = departure(T, seq, n, a[0], a)
    end, late, _ = simulate(T, seq, n, a, b, s, dep)
    return (end - dep) + LATE_WEIGHT * late + OVERTIME_WEIGHT * max(0.0, end - b[0])


@njit(cache=True)
def best_insertions(T, route, n, cands, a, b, s):
    """Cheapest insertion delta and position of every candidate into one route."""
    base = route_cost(T, route, n, a, b, s)
    buf = np.empty(n + 1, dtype=np.int64)
    best = np.full(len(cands), np.inf)
    pos = np.zeros(len(cands), dtype=np.int64)
    for ci in range(len(cands)):
        c = cands[ci]
        for p in range(n + 1):
            buf[:p] = route[:p]
            buf[p] = c
            buf[p + 1:] = route[p:n]
            d = route_cost(T, buf, n + 1, a, b, s) - base
            if d < best[ci]:
                best[ci] = d
                pos[ci] = p
    return best, pos


@njit(cache=True)
def removal_gains(T, route, n, a, b, s):
    base = route_cost(T, route, n, a, b, s)
    out = np.empty(n)
    buf = np.empty(max(n - 1, 1), dtype=np.int64)
    for p in range(n):
        buf[:p] = route[:p]
        buf[p:n - 1] = route[p + 1:n]
        out[p] = base - route_cost(T, buf, n - 1, a, b, s)
    return out


# ---------------------------------------------------------------- solution

@dataclass
class Solution:
    routes: list[list[int]]
    cost: float = 0.0
    route_costs: list[float] = field(default_factory=list)

    def copy(self) -> Solution:
        return Solution([r[:] for r in self.routes], self.cost, self.route_costs[:])


class ALNS:
    DESTROY = ("random", "worst", "related", "string")
    REPAIR = ("greedy", "regret2")

    def __init__(self, inst: Instance, seed: int = 0):
        self.I = inst
        self.rng = np.random.default_rng(seed)
        self.T = np.ascontiguousarray(inst.T, dtype=np.float64)
        self.a = inst.tw_start.astype(np.float64)
        self.b = inst.tw_end.astype(np.float64)
        self.s = inst.service.astype(np.float64)
        # relatedness for Shaw removal: mean travel time + time-window distance
        mean_t = self.T[8:21].mean(axis=0)
        self.rel = (mean_t + mean_t.T) / 2 + 0.5 * np.abs(self.a[:, None] - self.a[None, :])

    # -- helpers
    def _cost(self, r: list[int]) -> float:
        return route_cost(self.T, np.array(r, dtype=np.int64), len(r), self.a, self.b, self.s)

    def _evaluate(self, sol: Solution) -> Solution:
        sol.route_costs = [self._cost(r) for r in sol.routes]
        sol.cost = sum(sol.route_costs)
        return sol

    def _load(self, r: list[int]) -> int:
        return int(self.I.size[r].sum()) if r else 0

    # -- repair
    def repair(self, sol: Solution, removed: list[int], kind: str, noise: bool) -> Solution:
        K, Q = self.I.n_vehicles, self.I.capacity
        loads = [self._load(r) for r in sol.routes]
        pending = list(removed)
        delta = np.full((len(pending), K), np.inf)
        pos = np.zeros((len(pending), K), dtype=np.int64)

        def score_route(k: int):
            if not pending:
                return
            cands = np.array(pending, dtype=np.int64)
            r = np.array(sol.routes[k], dtype=np.int64)
            d, p = best_insertions(self.T, r, len(r), cands, self.a, self.b, self.s)
            over = loads[k] + self.I.size[cands] > Q
            d[over] = np.inf
            delta[:len(pending), k] = d
            pos[:len(pending), k] = p

        for k in range(K):
            score_route(k)
        while pending:
            m = len(pending)
            D = delta[:m].copy()
            if noise:
                D = D * self.rng.uniform(0.85, 1.15, size=D.shape)
            if kind == "regret2" and K > 1:
                part = np.sort(D, axis=1)
                regret = np.where(np.isfinite(part[:, 1]), part[:, 1] - part[:, 0], 1e12)
                regret[~np.isfinite(part[:, 0])] = -1
                i = int(np.argmax(regret))
            else:
                i = int(np.argmin(D.min(axis=1)))
            k = int(np.argmin(D[i]))
            if not np.isfinite(D[i, k]):
                # no feasible van (capacity): force into the emptiest van, cost counts it
                k = int(np.argmin(loads))
                p = len(sol.routes[k])
            else:
                p = int(pos[i, k])
            c = pending[i]
            sol.routes[k].insert(p, c)
            loads[k] += int(self.I.size[c])
            # drop row i, rescore route k
            pending.pop(i)
            delta[i:m - 1] = delta[i + 1:m]
            pos[i:m - 1] = pos[i + 1:m]
            score_route(k)
        return self._evaluate(sol)

    # -- destroy
    def destroy(self, sol: Solution, kind: str, q: int) -> tuple[Solution, list[int]]:
        sol = sol.copy()
        where = {c: (k, i) for k, r in enumerate(sol.routes) for i, c in enumerate(r)}
        customers = np.array(list(where))
        if kind == "random":
            removed = list(self.rng.choice(customers, size=q, replace=False))
        elif kind == "worst":
            gains = {}
            for r in sol.routes:
                if r:
                    g = removal_gains(self.T, np.array(r, dtype=np.int64), len(r), self.a, self.b, self.s)
                    gains.update(zip(r, g))
            order = sorted(gains, key=gains.get, reverse=True)
            removed = []
            while len(removed) < q:
                j = int(len(order) * self.rng.random() ** 4)  # randomised, biased to the worst
                removed.append(order.pop(j))
        elif kind == "related":
            seed = int(self.rng.choice(customers))
            order = customers[np.argsort(self.rel[seed, customers])]
            removed = [int(x) for x in order[:q]]
        else:  # string: cut consecutive stops from routes near a seed stop (SISR-style)
            seed = int(self.rng.choice(customers))
            near = customers[np.argsort(self.rel[seed, customers])]
            removed, touched = [], set()
            for c in near:
                k, _ = where[int(c)]
                if k in touched or len(removed) >= q:
                    continue
                touched.add(k)
                r = sol.routes[k]
                L = int(self.rng.integers(1, min(len(r), max(2, q // 2)) + 1))
                i = r.index(int(c))
                start = int(np.clip(i - self.rng.integers(0, L), 0, len(r) - L))
                removed += r[start:start + L]
            removed = removed[:q]
        rem = set(int(x) for x in removed)
        sol.routes = [[c for c in r if c not in rem] for r in sol.routes]
        return sol, [int(x) for x in removed]

    # -- main loop
    def initial(self) -> Solution:
        sol = Solution([[] for _ in range(self.I.n_vehicles)])
        return self.repair(sol, list(range(1, self.I.n + 1)), "regret2", noise=False)

    def solve(self, iterations: int = 3000, time_limit: float = 60.0, start: Solution | None = None,
              verbose: bool = False) -> tuple[Solution, dict]:
        t0 = time.time()
        cur = start.copy() if start else self.initial()
        best = cur.copy()
        nD, nR = len(self.DESTROY), len(self.REPAIR)
        wD, wR = np.ones(nD), np.ones(nR)
        sD, sR = np.zeros(nD), np.zeros(nR)
        uD, uR = np.zeros(nD), np.zeros(nR)
        # SA: accept a 2 % worse solution with probability 0.5 at the start, cool to ~0
        temp0 = -0.02 * cur.cost / math.log(0.5)
        temp, alpha = temp0, (0.002 / 1.0) ** (1.0 / max(iterations, 1))
        history = [best.cost]
        it = 0
        n = self.I.n
        for it in range(1, iterations + 1):
            if time.time() - t0 > time_limit:
                break
            di = self.rng.choice(nD, p=wD / wD.sum())
            ri = self.rng.choice(nR, p=wR / wR.sum())
            q = int(self.rng.integers(max(4, n // 40), max(6, int(0.12 * n)) + 1))
            cand, removed = self.destroy(cur, self.DESTROY[di], q)
            cand = self.repair(cand, removed, self.REPAIR[ri], noise=self.rng.random() < 0.3)
            score = 0
            if cand.cost < best.cost - 1e-6:
                best, cur, score = cand.copy(), cand, 33
            elif cand.cost < cur.cost - 1e-6:
                cur, score = cand, 9
            elif self.rng.random() < math.exp(-(cand.cost - cur.cost) / max(temp, 1e-9)):
                cur, score = cand, 13
            sD[di] += score; sR[ri] += score; uD[di] += 1; uR[ri] += 1
            temp *= alpha
            if it % 100 == 0:
                wD = np.maximum(0.8 * wD + 0.2 * np.where(uD > 0, sD / np.maximum(uD, 1), 0), 0.05)
                wR = np.maximum(0.8 * wR + 0.2 * np.where(uR > 0, sR / np.maximum(uR, 1), 0), 0.05)
                sD[:] = sR[:] = uD[:] = uR[:] = 0
                history.append(best.cost)
                if verbose:
                    print(f"it {it:5d}  best {best.cost / 3600:8.2f} h  cur {cur.cost / 3600:8.2f}  "
                          f"{time.time() - t0:5.1f}s")
        info = {"iterations": it, "seconds": round(time.time() - t0, 1), "history": history,
                "destroy_weights": dict(zip(self.DESTROY, wD.round(2).tolist())),
                "repair_weights": dict(zip(self.REPAIR, wR.round(2).tolist()))}
        return best, info


# ---------------------------------------------------------------- execution

def execute(T: np.ndarray, routes: list[list[int]], inst: Instance, departures: list[float] | None = None) -> dict:
    """Drive the plan through travel times ``T`` (e.g. the real traffic of that day) and measure it.

    Vans keep the planned stop order and planned departure time — drivers follow the manifest.
    """
    T = np.ascontiguousarray(T, dtype=np.float64)
    a, b, s = (x.astype(np.float64) for x in (inst.tw_start, inst.tw_end, inst.service))
    arrivals, late_each, duty, overtime, used, deps, ends = {}, [], 0.0, 0.0, 0, [], []
    for k, r in enumerate(routes):
        seq = np.array(r, dtype=np.int64)
        if not r:
            deps.append(np.nan); ends.append(np.nan)
            continue
        used += 1
        dep = departures[k] if departures is not None else departure(T, seq, len(r), a[0], a)
        end, _, arr = simulate(T, seq, len(r), a, b, s, dep)
        for c, t in zip(r, arr):
            arrivals[c] = float(t)
            late_each.append(max(0.0, max(t, a[c]) - b[c]))
        duty += end - dep
        overtime += max(0.0, end - b[0])
        deps.append(float(dep)); ends.append(float(end))
    late = np.array(late_each)
    return {"on_time_pct": float(100 * (late <= 0).mean()), "late_orders": int((late > 0).sum()),
            "late_minutes": float(late.sum() / 60), "duty_hours": float(duty / 3600),
            "overtime_minutes": float(overtime / 60), "vans": used,
            "arrivals": arrivals, "departures": deps, "returns": ends}
