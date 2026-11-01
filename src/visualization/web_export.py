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


def _round_coords(c, nd=6):
    """Round coordinates to 6 decimals (~10 cm) to keep the dashboard data small."""
    return [_round_coords(x, nd) for x in c] if isinstance(c, list) else round(c, nd)


def _geojson(path: str, keep=None):
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    for f in data.get("features", []):
        if f.get("geometry"):
            f["geometry"]["coordinates"] = _round_coords(f["geometry"]["coordinates"])
    if keep is not None:
        for f in data.get("features", []):
            f["properties"] = {k: f["properties"].get(k) for k in keep}
    data.pop("crs", None)
    return data


def _roads_near_campus(roads, campus):
    """Same rule as the Streamlit dashboard: keep roads with a vertex inside the campus bounds."""
    if not roads or not campus:
        return roads
    from shapely.geometry import shape
    w, s, e, n = shape(campus["features"][0]["geometry"]).bounds
    roads["features"] = [
        f for f in roads["features"]
        if f["geometry"]["type"] == "LineString"
        and any(w <= lon <= e and s <= lat <= n for lon, lat in f["geometry"]["coordinates"])
    ]
    return roads


VITAP_META = {"slug": "vit-ap", "name": "VIT-AP University", "short": "VIT-AP",
              "chargers_scope": "in Andhra Pradesh", "region": "Andhra Pradesh"}
REGISTRY = Path("web/data/places/index.json")


def _ga_block(ga_cfg: dict, history: pd.DataFrame, capacity: pd.DataFrame, overrides: dict = None) -> dict:
    fp = dict(ga_cfg["fitness_params"], **(overrides or {}).get("fitness_params", {}))
    return {
        "n_stations": (overrides or {}).get("n_stations", ga_cfg["n_stations_target"]),
        "population_size": ga_cfg["population_size"],
        "max_generations": ga_cfg["max_generations"],
        "mutation_probability": ga_cfg["mutation"]["probability"],
        "crossover_probability": ga_cfg["crossover"]["probability"],
        "elitism": ga_cfg["elitism_count"],
        "weights": ga_cfg["fitness_weights"],
        "budget_lakhs": fp["budget_lakhs"],
        "min_spacing_m": fp["min_station_distance_m"],
        "coverage_radius_m": fp["coverage_radius_m"],
        "fitness_history": history["best_fitness"].tolist(),
        "capacity": _records(capacity),
    }


def _with_meta(data: dict, meta: dict) -> dict:
    data["meta"] = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sources": ["OpenStreetMap", "Open-Meteo", "OpenChargeMap", "Vahan (MoRTH)", "Esri World Imagery"],
                    "place": meta}
    return _clean(data)


def _write(data: dict, out_js: str, meta: dict) -> str:
    data = _with_meta(data, meta)
    out = Path(out_js)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    out.write_text(
        "// Generated from the pipeline outputs. Do not edit by hand.\n"
        f"window.DASHBOARD_DATA = {payload};\n",
        encoding="utf-8",
    )
    print(f"Saved web dashboard data to {out} ({out.stat().st_size / 1024:.0f} KB)")
    return str(out)


def update_registry(slug: str, name: str, file: str, budget=None) -> None:
    """Keep web/data/places.js listing every exported place, VIT-AP first."""
    entries = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else []
    entries = [e for e in entries if e["slug"] != slug] + [{"slug": slug, "name": name, "file": file, "budget": budget}]
    entries.sort(key=lambda e: (e["slug"] != "vit-ap", e["name"].lower()))
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    Path("web/data/places.js").write_text(
        "// Generated list of places the dashboard can show. Do not edit by hand.\n"
        f"window.EV_PLACES = {json.dumps(entries, ensure_ascii=False)};\n", encoding="utf-8")


def build_web_data(
    scored_csv: str = "data/processed/candidates_demo_fuzzy_scored.csv",
    solution_csv: str = "data/processed/ga_solution_demo.csv",
    ga_config: str = "config/ga_config_demo.yaml",
    fuzzy_config: str = "config/fuzzy_config.yaml",
    out_js: str = "web/data/dashboard-data.js",
) -> str:
    """Write the VIT-AP dashboard data (web/data/dashboard-data.js) from the pipeline outputs."""
    ga_cfg = yaml.safe_load(Path(ga_config).read_text(encoding="utf-8"))["ga"]
    fuzzy_cfg = yaml.safe_load(Path(fuzzy_config).read_text(encoding="utf-8"))["fuzzy"]
    campus = _geojson("data/raw/osm/campus_boundary.geojson", keep=["name"])
    min_budget = Path("outputs/evaluation/min_budget.csv")
    data = {
        "candidates": _records(pd.read_csv(scored_csv), CANDIDATE_COLUMNS),
        "selected": _records(pd.read_csv(solution_csv), ["candidate_id", "ga_rank"]),
        "ga": _ga_block(ga_cfg, pd.read_csv("outputs/ga_results/fitness_history.csv"),
                        pd.read_csv("outputs/ga_results/capacity.csv")),
        "min_budget": _records(pd.read_csv(min_budget)) if min_budget.exists() else [],
        "fuzzy": {"n_rules": len(fuzzy_cfg["rules"]), "inputs": list(fuzzy_cfg["inputs"])},
        "evaluation": {
            "summary": json.loads(Path("outputs/evaluation/summary.json").read_text(encoding="utf-8")),
            "methods": _records(pd.read_csv("outputs/evaluation/comparison.csv")),
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
    path = _write(data, out_js, VITAP_META)
    update_registry(VITAP_META["slug"], VITAP_META["short"], "data/dashboard-data.js", data["ga"]["budget_lakhs"])
    return path


def build_place_web_data(out_dir: Path, meta: dict) -> str:
    """Write web/data/places/<slug>.js from a place run's outputs (outputs/places/<slug>/)."""
    data = _place_data(Path(out_dir), meta)
    rel = f"data/places/{meta['slug']}.js"
    path = _write(data, f"web/{rel}", meta)
    update_registry(meta["slug"], meta.get("short") or meta["name"], rel, meta["settings"]["budget_lakhs"])
    return path


def place_web_data(out_dir: Path, meta: dict) -> dict:
    """The dashboard data for a place run as a JSON-ready dict (what the live planner API returns)."""
    return _with_meta(_place_data(Path(out_dir), meta), meta)


def _place_data(out_dir: Path, meta: dict, ga_config: str = "config/ga_config_demo.yaml",
                fuzzy_config: str = "config/fuzzy_config.yaml") -> dict:
    ga_cfg = yaml.safe_load(Path(ga_config).read_text(encoding="utf-8"))["ga"]
    fuzzy_cfg = yaml.safe_load(Path(fuzzy_config).read_text(encoding="utf-8"))["fuzzy"]
    solution = pd.read_csv(out_dir / "solution.csv")
    st = meta["settings"]
    campus = _geojson(str(out_dir / "boundary.geojson"), keep=["name"])
    data = {
        "candidates": _records(pd.read_csv(out_dir / "candidates_scored.csv"), CANDIDATE_COLUMNS),
        "selected": _records(solution, ["candidate_id", "ga_rank"]),
        "ga": _ga_block(ga_cfg, pd.read_csv(out_dir / "fitness_history.csv"), pd.read_csv(out_dir / "plans_by_k.csv"), {
            "n_stations": len(solution),
            "fitness_params": {"budget_lakhs": st["budget_lakhs"], "min_station_distance_m": st["spacing_m"],
                               "coverage_radius_m": st["coverage_m"]}}),
        "min_budget": _records(pd.read_csv(out_dir / "min_budget.csv")),
        "fuzzy": {"n_rules": len(fuzzy_cfg["rules"]), "inputs": list(fuzzy_cfg["inputs"])},
        "evaluation": {"summary": json.loads((out_dir / "summary.json").read_text(encoding="utf-8")),
                       "methods": _records(pd.read_csv(out_dir / "comparison.csv"))},
        "ml": json.loads((out_dir / "ml_metrics.json").read_text(encoding="utf-8")),
        "geo": {
            "campus": campus,
            "zones": _geojson(str(out_dir / "zones.geojson"), keep=["zone_id", "zone_name"]),
            "buildings": _geojson(str(out_dir / "buildings.geojson"), keep=["name", "building"]),
            "roads": _roads_near_campus(
                _geojson(str(out_dir / "roads.geojson"), keep=["highway", "name", "ref", "surface", "length_m"]), campus),
            "parking": _geojson(str(out_dir / "parking.geojson"), keep=["osm_id"]),
            "chargers": _geojson(str(out_dir / "chargers.geojson"), keep=CHARGER_FIELDS),
            "state": None,
        },
    }
    return data


def export_all_places(root: str = "outputs/places") -> None:
    """Re-export every place run found under outputs/places/<slug>/place.json."""
    for meta_path in sorted(Path(root).glob("*/place.json")):
        build_place_web_data(meta_path.parent, json.loads(meta_path.read_text(encoding="utf-8")))
