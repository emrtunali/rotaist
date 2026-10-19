"""The industry-standard baseline: Google OR-Tools VRPTW on one static travel-time matrix.

This is how most last-mile planners work today: one matrix for the whole day (free-flow, or an average),
soft time windows, guided local search. It gets the same soft-window objective as Rotaist
(duty time + LATE_WEIGHT × lateness), so the only differences are the solver and what it knows about traffic.
"""

from __future__ import annotations

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .solver import LATE_WEIGHT, OVERTIME_WEIGHT, Instance


def solve_ortools(inst: Instance, M: np.ndarray, seconds: int = 30) -> list[list[int]]:
    """Plan with OR-Tools on static matrix ``M`` (n+1, n+1) seconds; returns stop sequences per van."""
    n1, K = len(M), inst.n_vehicles
    Mi = np.rint(M).astype(int)
    svc = inst.service.astype(int)
    mgr = pywrapcp.RoutingIndexManager(n1, K, 0)
    rt = pywrapcp.RoutingModel(mgr)

    def transit(i, j):
        a, b = mgr.IndexToNode(i), mgr.IndexToNode(j)
        return int(Mi[a, b] + svc[a])

    cb = rt.RegisterTransitCallback(transit)
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    shift_start, shift_end = int(inst.tw_start[0]), int(inst.tw_end[0])
    horizon = 30 * 3600  # past midnight is allowed but paid as overtime, so a plan always exists
    rt.AddDimension(cb, horizon, horizon, False, "time")
    td = rt.GetDimensionOrDie("time")
    td.SetSpanCostCoefficientForAllVehicles(1)
    for c in range(1, n1):
        idx = mgr.NodeToIndex(c)
        td.CumulVar(idx).SetRange(int(inst.tw_start[c]), horizon)
        td.SetCumulVarSoftUpperBound(idx, int(inst.tw_end[c]), int(LATE_WEIGHT))
    for k in range(K):
        td.CumulVar(rt.Start(k)).SetRange(shift_start, horizon)
        td.SetCumulVarSoftUpperBound(rt.End(k), shift_end, int(OVERTIME_WEIGHT))
        rt.AddVariableMinimizedByFinalizer(td.CumulVar(rt.Start(k)))
        rt.AddVariableMinimizedByFinalizer(td.CumulVar(rt.End(k)))

    size = inst.size.astype(int)
    dc = rt.RegisterUnaryTransitCallback(lambda i: int(size[mgr.IndexToNode(i)]))
    rt.AddDimensionWithVehicleCapacity(dc, 0, [int(inst.capacity)] * K, True, "load")

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = seconds
    sol = rt.SolveWithParameters(p)
    if sol is None:
        raise RuntimeError("OR-Tools found no solution")
    routes = []
    for k in range(K):
        i, r = rt.Start(k), []
        while not rt.IsEnd(i):
            node = mgr.IndexToNode(i)
            if node:
                r.append(node)
            i = sol.Value(rt.NextVar(i))
        routes.append(r)
    return routes
