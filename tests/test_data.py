import networkx as nx
import numpy as np
import pandas as pd

from rotaist.demand import SLOTS, building_weights, sample_orders
from rotaist.forecast import Forecaster
from rotaist.network import Network, congestion_ratio, free_flow_reference
from rotaist.traffic import Traffic


def toy_traffic(days=21, cells=4, seed=0) -> Traffic:
    rng = np.random.default_rng(seed)
    hours = np.arange(24)
    rush = 1 - 0.5 * np.exp(-((hours - 18) ** 2) / 4)                 # slow at 18:00
    speed = 50 * rush[None, :, None] * rng.uniform(0.9, 1.1, (days, 24, cells))
    dates = [str(d.date()) for d in pd.date_range("2024-11-04", periods=days)]
    return Traffic([f"c{i}" for i in range(cells)], np.linspace(40.98, 41.0, cells), np.linspace(29.05, 29.1, cells),
                   dates, speed.astype(np.float32), np.ones((days, 24, cells), np.float32))


def test_congestion_ratio_is_low_in_rush_hour():
    tr = toy_traffic()
    ratio = congestion_ratio(tr, free_flow_reference(tr, slice(0, 14)))
    assert np.nanmean(ratio[:, 18]) < 0.6 < np.nanmean(ratio[:, 3])


def test_forecaster_learns_the_daily_profile():
    tr = toy_traffic()
    ratio = congestion_ratio(tr, free_flow_reference(tr, slice(0, 14)))
    f = Forecaster.fit(ratio, tr.dates, np.arange(14), tr.lat, tr.lon)
    pred = f.predict(ratio, tr.dates, 18, tr.lat, tr.lon)
    assert pred.shape == (24, 4)
    assert np.abs(pred - ratio[18]).mean() < np.abs(1 - ratio[18]).mean()   # beats "no traffic"


def test_network_slows_down_with_congestion():
    G = nx.MultiDiGraph()
    for i, (x, y) in enumerate([(29.05, 40.98), (29.06, 40.98), (29.07, 40.98)]):
        G.add_node(i, x=x, y=y)
    for a, b in [(0, 1), (1, 2), (1, 0), (2, 1)]:
        G.add_edge(a, b, length=850.0, highway="primary")
    net = Network.from_graph(G, toy_traffic(cells=1))
    free = net.matrices(np.array([0, 2]), np.ones((1, 1)))[0]
    slow = net.matrices(np.array([0, 2]), np.full((1, 1), 0.5))[0]
    assert free[0, 1] == np.float64(1700 / (50 / 3.6))
    assert slow[0, 1] == 2 * free[0, 1]
    assert net.path(0, 2, np.ones(1)) == [0, 1, 2]


def test_orders_follow_building_density_and_slots():
    b = pd.DataFrame({"building": ["apartments", "school", "yes"], "building:levels": ["10", "3", None],
                      "area_m2": [500.0, 2000.0, 100.0], "lon": [29.0, 29.1, 29.2], "lat": [41.0, 41.0, 41.0]})
    w = building_weights(b)
    assert len(w) == 2                                              # the school is not a home
    o = sample_orders(w, 2000, seed=0)
    assert (o.lon == 29.0).mean() > 0.85                            # 5000 m² of floor area vs 400 m²
    assert set(zip(o.tw_start // 3600, o.tw_end // 3600)) <= set(SLOTS)
