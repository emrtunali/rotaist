"""Download the open data and build the processed inputs (~600 MB download, ~5 min).

    python scripts/prepare_data.py

1. İBB "Hourly Traffic Density Data Set", October 2024 – January 2025 (İBB Open Data License)
2. OpenStreetMap drivable streets and buildings for Kadıköy, Üsküdar and Ataşehir (ODbL)
3. data/processed/traffic.npz — the (day, hour, cell) speed tensor for the study area
"""

from __future__ import annotations

from pathlib import Path

import httpx
import osmnx as ox

from rotaist.traffic import load_months

ROOT = Path(__file__).resolve().parents[1]
RAW, PROC = ROOT / "data/raw", ROOT / "data/processed"
DATASET = "https://data.ibb.gov.tr/dataset/3ee6d744-5da2-40c8-9cd6-0e3e41f1928f/resource"
MONTHS = {
    "202410": "d291989c-429d-4e61-9c70-1f76294b96b8",
    "202411": "bedd5ab2-9a00-4966-9921-9672d4478a51",
    "202412": "76671ebe-2fd2-426f-b85a-e3772263f483",
    "202501": "57cb067b-1a0b-460b-8342-7884bd4537e8",
}
PLACES = ["Kadıköy, İstanbul, Türkiye", "Üsküdar, İstanbul, Türkiye", "Ataşehir, İstanbul, Türkiye"]


def download(url: str, dest: Path) -> None:
    if dest.exists():
        return
    print("downloading", dest.name)
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)
    for m, rid in MONTHS.items():
        download(f"{DATASET}/{rid}/download/traffic_density_{m}.csv", RAW / f"traffic_density_{m}.csv")

    ox.settings.cache_folder = str(RAW / "osm_cache")
    area = ox.geocode_to_gdf(PLACES)
    area.to_file(RAW / "area.geojson", driver="GeoJSON")
    if not (RAW / "anadolu_drive.graphml").exists():
        print("downloading street network")
        ox.save_graphml(ox.graph_from_place(PLACES, network_type="drive"), RAW / "anadolu_drive.graphml")
    if not (RAW / "buildings.parquet").exists():
        print("downloading buildings")
        b = ox.features_from_polygon(area.union_all(), {"building": True})
        b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        metric = b.to_crs(32635)
        b["area_m2"] = metric.area
        centre = metric.centroid.to_crs(4326)
        b["lon"], b["lat"] = centre.x, centre.y
        cols = [c for c in ["building", "building:levels", "area_m2", "lon", "lat"] if c in b.columns]
        b[cols].reset_index(drop=True).to_parquet(RAW / "buildings.parquet")

    w, s, e, n = area.total_bounds
    tr = load_months(sorted(RAW.glob("traffic_density_*.csv")), (s, w, n, e))
    tr.save(PROC / "traffic.npz")
    print(f"traffic tensor: {len(tr.cells)} cells × {len(tr.dates)} days")


if __name__ == "__main__":
    main()
