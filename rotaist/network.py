"""Time-dependent road network: OpenStreetMap streets with İBB traffic speeds, hour by hour.

Each street segment gets a free-flow speed from OSM (``maxspeed`` or a default for its road class)
and belongs to the geohash cell that contains its midpoint. For a given day and hour, the segment's
speed is its free-flow speed scaled by the cell's *congestion ratio*

    ratio = observed average speed in the cell / the cell's free-flow speed (90th percentile)

so a cell crawling at 40 % of its usual free-flow speed slows every street in it by the same factor.
Shortest travel times between stops are computed for each hour with Dijkstra (scipy, in C).
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from .traffic import Traffic

# Urban defaults (km/h) when OSM has no maxspeed; Turkish urban limits are 50 km/h unless signed otherwise.
DEFAULT_KMH = {"motorway": 90, "motorway_link": 50, "trunk": 70, "trunk_link": 40, "primary": 50,
               "primary_link": 35, "secondary": 50, "secondary_link": 30, "tertiary": 40, "tertiary_link": 30,
               "residential": 30, "unclassified": 30, "living_street": 15, "service": 20}
MIN_RATIO = 0.12  # never slower than ~12 % of free flow (keeps gridlocked cells finite)


def _first(v):
    return v[0] if isinstance(v, list) else v


def _maxspeed(v) -> float | None:
    v = _first(v)
    if v is None:
        return None
    try:
        return float(str(v).split()[0].replace("TR:urban", "50"))
    except ValueError:
        return None


@dataclass
class Network:
    node_ids: np.ndarray        # OSM node ids (index -> id)
    xy: np.ndarray              # (nodes, 2) lon, lat
    u: np.ndarray               # edge tail index
    v: np.ndarray               # edge head index
    length: np.ndarray          # metres
    ff_kmh: np.ndarray          # free-flow speed per edge
    cell: np.ndarray            # traffic cell index per edge (-1 = no cell nearby)

    @classmethod
    def from_graph(cls, G: nx.MultiDiGraph, traffic: Traffic, max_cell_km: float = 1.5) -> Network:
        ids = np.array(list(G.nodes))
        index = {n: i for i, n in enumerate(ids)}
        xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in ids])
        u, v, length, ff = [], [], [], []
        for a, b, d in G.edges(data=True):
            u.append(index[a]); v.append(index[b]); length.append(float(d["length"]))
            hw = _first(d.get("highway", "residential"))
            ff.append(_maxspeed(d.get("maxspeed")) or DEFAULT_KMH.get(hw, 30))
        u, v = np.array(u), np.array(v)
        mid = (xy[u] + xy[v]) / 2
        # nearest cell centre (degrees scaled to km at Istanbul's latitude)
        scale = np.array([85.0, 111.0])
        tree = cKDTree(np.c_[traffic.lon, traffic.lat] * scale)
        dist, cell = tree.query(mid * scale)
        cell[dist > max_cell_km] = -1
        return cls(ids, xy, u, v, np.array(length), np.array(ff, dtype=float), cell)

    def nearest_node(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Snap points to the nearest node of the largest strongly connected component.

        One-way dead ends and clipped streets at the area boundary can't be both reached and left,
        so stops are only ever placed where a van can drive in and out.
        """
        n = len(self.node_ids)
        g = csr_matrix((np.ones(len(self.u)), (self.u, self.v)), shape=(n, n))
        _, label = connected_components(g, directed=True, connection="strong")
        ok = np.flatnonzero(label == np.bincount(label).argmax())
        scale = np.array([85.0, 111.0])
        return ok[cKDTree(self.xy[ok] * scale).query(np.c_[lon, lat] * scale)[1]]

    def edge_seconds(self, ratio_by_cell: np.ndarray) -> np.ndarray:
        """Travel time per edge for one hour given the congestion ratio of every cell."""
        r = np.where(self.cell >= 0, ratio_by_cell[np.maximum(self.cell, 0)], np.nanmedian(ratio_by_cell))
        r = np.clip(np.nan_to_num(r, nan=1.0), MIN_RATIO, 1.0)
        return self.length / (self.ff_kmh * r / 3.6)

    def matrices(self, sources: np.ndarray, ratio: np.ndarray) -> np.ndarray:
        """(hours, n, n) shortest travel time in seconds between ``sources`` nodes for each hour.

        ``ratio`` is (hours, cells). Parallel edges are reduced to the fastest one by the CSR build.
        """
        n_nodes = len(self.node_ids)
        out = np.empty((ratio.shape[0], len(sources), len(sources)))
        for h in range(ratio.shape[0]):
            w = self.edge_seconds(ratio[h])
            order = np.argsort(-w)  # csr keeps the last duplicate: put the fastest parallel edge last
            g = csr_matrix((w[order], (self.u[order], self.v[order])), shape=(n_nodes, n_nodes))
            dist = dijkstra(g, directed=True, indices=sources)
            out[h] = dist[:, sources]
        return out

    def path(self, a: int, b: int, ratio_by_cell: np.ndarray) -> list[int]:
        """Node sequence of the fastest path from node index a to b at one hour (for drawing routes)."""
        n_nodes = len(self.node_ids)
        w = self.edge_seconds(ratio_by_cell)
        order = np.argsort(-w)
        g = csr_matrix((w[order], (self.u[order], self.v[order])), shape=(n_nodes, n_nodes))
        _, pred = dijkstra(g, directed=True, indices=[a], return_predecessors=True)
        seq, cur = [b], b
        while cur != a and cur >= 0:
            cur = pred[0, cur]
            seq.append(cur)
        return seq[::-1] if cur == a else [a, b]


def free_flow_reference(traffic: Traffic, train_days: slice) -> np.ndarray:
    """Per-cell free-flow speed: the 90th percentile of hourly average speeds in the training period."""
    with np.errstate(all="ignore"):
        return np.nanpercentile(traffic.speed[train_days].reshape(-1, len(traffic.cells)), 90, axis=0)


def congestion_ratio(traffic: Traffic, ff: np.ndarray) -> np.ndarray:
    """(days, 24, cells) observed speed / free-flow speed, NaN where unobserved."""
    with np.errstate(all="ignore"):
        return np.clip(traffic.speed / ff[None, None, :], 0, 1.2)
