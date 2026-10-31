"""
Export pipeline outputs for the web dashboard (web/index.html).

Reads the files the pipeline already wrote and copies their values into
web/data/dashboard-data.js. Nothing is recalculated here: every number the
web page shows comes straight from these outputs.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

CANDIDATE_COLUMNS = [
    "candidate_id", "name", "zone", "site_source", "latitude", "longitude",
    "predicted_annual_demand_kwh", "demand_index", "fuzzy_suitability_score", "demand_prediction_source",
    "total_capex_inr_lakhs", "land_cost_inr_lakhs", "grid_connection_cost_inr_lakhs",
    "construction_cost_inr_lakhs", "land_cost_tier", "traffic_accessibility_score", "poi_score",
    "existing_chargers_3km", "grid_dist_m", "max_chargers", "area_sqm",
]
CHARGER_FIELDS = ["station_id", "name", "town", "state", "operator", "status", "num_connectors",
                  "max_power_kw", "distance_to_campus_km"]


def _clean(value):
    """JSON-safe value: NaN and infinity become null."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _records(df: pd.DataFrame, columns=None) -> list:
    """Rows as plain Python values at full precision (pandas' to_json rounds to 10 decimals)."""
    df = df[[c for c in (columns or df.columns) if c in df.columns]]
    rows = []
    for rec in df.to_dict(orient="records"):
        rows.append({k: _clean(v.item() if hasattr(v, "item") else v) for k, v in rec.items()})
    return rows


def _geojson(path: str, keep=None):
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if keep is not None:
        for f in data.get("features", []):
            f["properties"] = {k: f["properties"].get(k) for k in keep}
    data.pop("crs", None)
    return data


def _roads_near_campus(roads, campus):
    """Same rule as the Streamlit dashboard: keep roads with a vertex inside the campus bounds."""
    if not roads or not campus:
        return roads
    lons = [c[0] for f in campus["features"] for c in f["geometry"]["coordinates"][0]]
    lats = [c[1] for f in campus["features"] for c in f["geometry"]["coordinates"][0]]
    w, e, s, n = min(lons), max(lons), min(lats), max(lats)
    roads["features"] = [
        f for f in roads["features"]
        if f["geometry"]["type"] == "LineString"
        and any(w <= lon <= e and s <= lat <= n for lon, lat in f["geometry"]["coordinates"])
    ]
    return roads


def build_web_data(
    scored_csv: str = "data/processed/candidates_demo_fuzzy_scored.csv",
    solution_csv: str = "data/processed/ga_solution_demo.csv",
    ga_config: str = "config/ga_config_demo.yaml",
    fuzzy_config: str = "config/fuzzy_config.yaml",
    out_js: str = "web/data/dashboard-data.js",
) -> str:
    """Write web/data/dashboard-data.js from the pipeline outputs and return its path."""
    candidates = pd.read_csv(scored_csv)
    solution = pd.read_csv(solution_csv)
    ga_cfg = yaml.safe_load(Path(ga_config).read_text(encoding="utf-8"))["ga"]
    fuzzy_cfg = yaml.safe_load(Path(fuzzy_config).read_text(encoding="utf-8"))["fuzzy"]

    campus = _geojson("data/raw/osm/campus_boundary.geojson", keep=["name"])
    comparison = pd.read_csv("outputs/evaluation/comparison.csv")
    history = pd.read_csv("outputs/ga_results/fitness_history.csv")
    capacity = pd.read_csv("outputs/ga_results/capacity.csv")

    data = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": ["OpenStreetMap", "Open-Meteo", "OpenChargeMap", "Esri World Imagery"],
        },
        "candidates": _records(candidates, CANDIDATE_COLUMNS),
        "selected": _records(solution, ["candidate_id", "ga_rank"]),
        "ga": {
            "n_stations": ga_cfg["n_stations_target"],
            "population_size": ga_cfg["population_size"],
            "max_generations": ga_cfg["max_generations"],
            "mutation_probability": ga_cfg["mutation"]["probability"],
            "crossover_probability": ga_cfg["crossover"]["probability"],
            "elitism": ga_cfg["elitism_count"],
            "weights": ga_cfg["fitness_weights"],
            "budget_lakhs": ga_cfg["fitness_params"]["budget_lakhs"],
            "min_spacing_m": ga_cfg["fitness_params"]["min_station_distance_m"],
            "coverage_radius_m": ga_cfg["fitness_params"]["coverage_radius_m"],
            "fitness_history": history["best_fitness"].tolist(),
            "capacity": _records(capacity),
        },
        "fuzzy": {"n_rules": len(fuzzy_cfg["rules"]), "inputs": list(fuzzy_cfg["inputs"])},
        "evaluation": {
            "summary": json.loads(Path("outputs/evaluation/summary.json").read_text(encoding="utf-8")),
            "methods": _records(comparison),
        },
        "ml": json.loads(Path("outputs/ml_results/metrics.json").read_text(encoding="utf-8")),
        "geo": {
            "campus": campus,
            "zones": _geojson("data/external/vitap_zone_polygons.geojson", keep=["zone_id", "zone_name"]),
            "buildings": _geojson("data/raw/osm/buildings.geojson", keep=["name", "building"]),
            "roads": _roads_near_campus(
                _geojson("data/raw/osm/roads.geojson", keep=["highway", "name", "ref", "surface", "length_m"]), campus),
            "parking": _geojson("data/raw/osm/parking.geojson", keep=["osm_id"]),
            "chargers": _geojson("data/raw/openchargemap/stations.geojson", keep=CHARGER_FIELDS),
            "state": _geojson("data/external/andhra_pradesh_boundary.geojson", keep=["name"]),
        },
    }

    out = Path(out_js)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_clean(data), separators=(",", ":"), ensure_ascii=False)
    out.write_text(
        "// Generated by `python run_pipeline.py web` from the pipeline outputs. Do not edit by hand.\n"
        f"window.DASHBOARD_DATA = {payload};\n",
        encoding="utf-8",
    )
    print(f"Saved web dashboard data to {out} ({out.stat().st_size / 1024:.0f} KB)")
    return str(out)
