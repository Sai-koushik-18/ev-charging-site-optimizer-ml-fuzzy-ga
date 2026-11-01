"""
Campus pipeline: Data -> ML -> Fuzzy -> GA on the real VIT-AP campus.

Candidate sites and zones are built from OpenStreetMap features inside the
OSM campus boundary (way 750542244). Each stage reads the previous stage's
file, so stages can run one at a time through run_pipeline.py:

  candidates  data/raw/osm/*.geojson, data/external/vitap_zone_polygons.geojson,
              data/external/vitap_campus_power_points.geojson,
              data/processed/candidates_campus.csv
  ml          data/raw/weather/weather_hourly_2024.csv, data/synthetic/demand_history.csv,
              models/demand_predictor_campus_*, outputs/ml_results/,
              data/processed/candidates_campus_demand.csv
  fuzzy       data/processed/candidates_demo_fuzzy_scored.csv
  ga          data/processed/ga_solution_demo.csv, outputs/ga_results/
"""

import json
import random
import sys
from pathlib import Path
from typing import Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from loguru import logger
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, polygonize, unary_union

from src.data.candidate_generator import CandidateGenerator
from src.data.config_loader import get_config
from src.data.ocm_loader import OCMDataLoader
from src.data.provenance import Provenance, ProvenanceTracker
from src.data.vahan_loader import ev_adoption
from src.data.synthetic_generator import generate_synthetic_demand
from src.data.weather_loader import WeatherLoader
from src.fuzzy.scorer import FuzzyEVChargingScorer
from src.ga.optimizer import GeneticAlgorithmOptimizer
from src.ml.demand_predictor import EVChargingDemandPredictor
from src.ml.ga_tuner import tune_random_forest_ga

CAMPUS_OSM_ID = "way/750542244"  # amenity=university, name=VIT-AP University
OSM_BBOX = (16.4860, 80.4915, 16.5030, 80.5075)  # south, west, north, east
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UTM_CRS = "EPSG:32644"
WGS84_CRS = "EPSG:4326"

# Zones built from named OSM features; order sets priority where zones overlap
ZONE_MEMBERS = {
    "commercial": ["Food Street", "Rock Plaza"],
    "hostel_girls": ["LH-1", "LH-2", "LH-3"],
    "hostel_boys": ["MH-1", "MH-2", "MH-3", "MH-4", "MH-5", "MH-6", "MH-7"],
    "academic": ["AB-1", "AB-2", "CB"],
    "sports": None,  # every OSM leisure=pitch inside the campus
}
ZONE_LABELS = {
    "commercial": ("Commercial (Food Street, Rock Plaza)", "commercial"),
    "hostel_girls": ("Ladies' Hostels (LH-1 to LH-3)", "residential"),
    "hostel_boys": ("Men's Hostels (MH-1 to MH-7)", "residential"),
    "academic": ("Academic Blocks (AB-1, AB-2, CB)", "academic"),
    "sports": ("Sports Grounds", "sports"),
    "gate": ("Campus Entry Points", "access"),
    "campus_general": ("Rest of Campus", "mixed"),
}
HULL_ZONES = {"commercial", "hostel_girls", "hostel_boys", "academic"}  # sports: pitches only
ZONE_BUFFER_M = 30
GATE_BUFFER_M = 60
GATE_MERGE_M = 80
DRIVABLE = {"secondary", "tertiary", "unclassified", "residential", "service"}
LINEAR_KEYS = ("highway", "barrier", "railway", "waterway")
ROADSIDE_AREA_SQM = 250  # assumed charging bay: 10 chargers x 25 sqm
ROADSIDE_MAX_DIST_M = 150
DEDUPE_M = 60
POI_KEYS = ["amenity", "shop", "leisure", "public_transport", "office", "building", "tourism"]

def fetch_osm(cache: Path, refresh: bool) -> dict:
    """Load the campus OSM extract, downloading it from Overpass if needed."""
    if cache.exists() and not refresh:
        logger.info(f"Using cached OSM extract {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))

    s, w, n, e = OSM_BBOX
    query = f"[out:json][timeout:180];nwr({s},{w},{n},{e});out geom;"
    headers = {"User-Agent": "vitap-ev-charging-planner/1.0 (student project)"}
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            logger.info(f"Downloading OSM extract from {endpoint}")
            resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=240)
            resp.raise_for_status()
            data = resp.json()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Saved {len(data['elements'])} OSM elements to {cache}")
            return data
        except (requests.RequestException, ValueError) as exc:
            logger.warning(f"Overpass endpoint failed ({endpoint}): {exc}")
    raise RuntimeError("All Overpass endpoints failed; rerun later or supply data/raw/osm/campus_osm.json")


def element_geometry(el: dict):
    """Build a shapely geometry from an Overpass 'out geom' element."""
    tags = el.get("tags", {})
    if el["type"] == "node":
        return Point(el["lon"], el["lat"])
    if el["type"] == "way":
        coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(coords) < 2:
            return None
        is_linear = any(k in tags for k in LINEAR_KEYS) or tags.get("power") == "line"
        if len(coords) >= 4 and coords[0] == coords[-1] and not is_linear:
            return Polygon(coords)
        return LineString(coords)
    if el["type"] == "relation":
        lines = [
            LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
            for m in el.get("members", [])
            if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and len(m.get("geometry", [])) >= 2
        ]
        polys = list(polygonize(unary_union(lines))) if lines else []
        return unary_union(polys) if polys else None
    return None


def osm_to_gdf(data: dict) -> gpd.GeoDataFrame:
    """Convert tagged OSM elements to a GeoDataFrame (untagged nodes are skipped)."""
    rows = []
    for el in data["elements"]:
        tags = el.get("tags")
        if not tags:
            continue
        geom = element_geometry(el)
        if geom is None or geom.is_empty:
            continue
        row = {"osm_id": f"{el['type']}/{el['id']}", "geometry": geom}
        for key in ["name", "highway", "power", "parking", "leisure"] + POI_KEYS:
            row[key] = tags.get(key)
        rows.append(row)
    return gpd.GeoDataFrame(rows, crs=WGS84_CRS)


def export_osm_layers(features: gpd.GeoDataFrame, out_dir: Path):
    """Write the real OSM campus layers the dashboard map displays."""
    utm = features.to_crs(UTM_CRS)
    campus = utm.loc[utm["osm_id"] == CAMPUS_OSM_ID].geometry.iloc[0]
    near = utm.geometry.intersects(campus.buffer(300))
    out_dir.mkdir(parents=True, exist_ok=True)

    boundary = features[features["osm_id"] == CAMPUS_OSM_ID][["osm_id", "name", "geometry"]]
    boundary.to_file(out_dir / "campus_boundary.geojson", driver="GeoJSON")

    roads = utm[near & utm["highway"].notna() & (utm.geom_type == "LineString")].copy()
    roads["length_m"] = roads.geometry.length.round(1)
    roads["ref"] = None
    roads["surface"] = None
    roads = roads[["osm_id", "highway", "name", "ref", "surface", "length_m", "geometry"]].to_crs(WGS84_CRS)
    roads.to_file(out_dir / "roads.geojson", driver="GeoJSON")

    buildings = utm[near & utm["building"].notna() & utm.geom_type.isin(["Polygon", "MultiPolygon"])]
    buildings = buildings[["osm_id", "name", "building", "amenity", "geometry"]].to_crs(WGS84_CRS)
    buildings.to_file(out_dir / "buildings.geojson", driver="GeoJSON")

    parking = utm[near & (utm["amenity"] == "parking")][["osm_id", "name", "parking", "geometry"]].to_crs(WGS84_CRS)
    parking.to_file(out_dir / "parking.geojson", driver="GeoJSON")
    logger.info(f"Exported OSM layers: {len(roads)} roads, {len(buildings)} buildings, {len(parking)} parking")


def point_inside(poly, pt: Point, inset_m: float = 15) -> Point:
    """Return pt if it lies inside poly, else the nearest point a little inside its edge."""
    inner = poly.buffer(-inset_m)
    if inner.is_empty:  # a sliver thinner than the inset: use the shape itself
        inner = poly
    if inner.contains(pt):
        return pt
    if inner.geom_type == "MultiPolygon":  # area in several pieces: the nearest piece's outer edge
        inner = min(inner.geoms, key=lambda g: g.distance(pt))
    return nearest_points(inner.exterior, pt)[0]


def zone_members(features_utm: gpd.GeoDataFrame, campus, zone_id: str) -> gpd.GeoDataFrame:
    """OSM features that define a zone, with a display label."""
    names = ZONE_MEMBERS[zone_id]
    if names is None:
        members = features_utm[(features_utm["leisure"] == "pitch")
                               & features_utm.geometry.centroid.within(campus)].copy()
        members["label"] = members["name"].fillna("Sports Pitch")
        return members
    members = features_utm[features_utm["name"].isin(names)].drop_duplicates("name").copy()
    missing = sorted(set(names) - set(members["name"]))
    if missing:
        logger.warning(f"Zone {zone_id}: OSM features not found: {missing}")
    members["label"] = members["name"]
    return members


def build_zones(features_utm: gpd.GeoDataFrame, campus, config):
    """Derive zone polygons and gate points from named OSM features (UTM)."""
    zone_defs = config._data["zones"]["zone_definitions"]
    zones, taken = [], None

    def add_zone(zone_id, geom, note):
        nonlocal taken
        geom = geom.intersection(campus)
        if taken is not None:
            geom = geom.difference(taken)
        if geom.is_empty or geom.area < 100:
            logger.warning(f"Zone {zone_id} is empty after clipping - skipped")
            return
        taken = geom if taken is None else taken.union(geom)
        name, use = ZONE_LABELS[zone_id]
        zones.append({
            "zone_id": zone_id,
            "zone_name": name,
            "land_cost_tier": zone_defs[zone_id]["land_cost_tier"],
            "primary_use": use,
            "description": note,
            "provenance": "DERIVED",
            "source": "osm_named_features",
            "note": "Derived from OpenStreetMap features inside the OSM campus boundary. NOT an official VIT-AP zoning plan.",
            "geometry": geom,
        })

    for zone_id in ZONE_MEMBERS:
        members = zone_members(features_utm, campus, zone_id)
        if members.empty:
            continue
        geom = unary_union(list(members.geometry))
        geom = geom.convex_hull.buffer(ZONE_BUFFER_M) if zone_id in HULL_ZONES else geom.buffer(ZONE_BUFFER_M / 2)
        add_zone(zone_id, geom, f"Around OSM features: {', '.join(sorted(members['label']))}")

    # Gates: where drivable roads cross the campus boundary
    roads = features_utm[features_utm["highway"].isin(DRIVABLE) & (features_utm.geom_type == "LineString")]
    crossings = []
    for line in roads.geometry:
        hit = line.intersection(campus.exterior)
        if hit.is_empty:
            continue
        pts = [hit] if hit.geom_type == "Point" else [g for g in getattr(hit, "geoms", []) if g.geom_type == "Point"]
        crossings.extend(pts)
    gates = []
    for pt in crossings:
        if all(pt.distance(g) > GATE_MERGE_M for g in gates):
            gates.append(pt)
    if gates:
        add_zone("gate", unary_union([g.buffer(GATE_BUFFER_M) for g in gates]),
                 f"{len(gates)} points where drivable OSM roads cross the campus boundary")
    add_zone("campus_general", campus, "Remainder of the OSM campus boundary")

    return gpd.GeoDataFrame(zones, crs=UTM_CRS), gates


def build_sites(features_utm: gpd.GeoDataFrame, campus, gates):
    """Candidate sites: OSM parking lots, roadside bays next to named buildings, and gates (UTM)."""
    sites = []

    parking = features_utm[(features_utm["amenity"] == "parking") & (features_utm.geom_type == "Polygon")]
    for _, p in parking.iterrows():
        if campus.buffer(10).contains(p.geometry.centroid):
            sites.append({
                "geometry": point_inside(campus, p.geometry.centroid),
                "site_label": f"OSM Parking Lot ({p['osm_id']})",
                "site_source": "osm_parking",
                "area_sqm": round(p.geometry.area, 1),
                "parking_type": p["parking"] or "surface",
            })

    roads = features_utm[features_utm["highway"].isin(DRIVABLE) & (features_utm.geom_type == "LineString")]
    road_net = unary_union(list(roads.geometry)).intersection(campus.buffer(-5))
    for zone_id in ZONE_MEMBERS:
        for _, member in zone_members(features_utm, campus, zone_id).iterrows():
            if road_net.is_empty:
                continue
            name = member["label"]
            footprint = member.geometry  # nearest road to the building's edge, not its centre
            if road_net.distance(footprint) > ROADSIDE_MAX_DIST_M:
                logger.warning(f"No drivable road within {ROADSIDE_MAX_DIST_M} m of {name} - no roadside site")
                continue
            sites.append({
                "geometry": nearest_points(road_net, footprint)[0],
                "site_label": f"Roadside near {name}",
                "site_source": "roadside_near_osm_feature",
                "area_sqm": ROADSIDE_AREA_SQM,
                "parking_type": "street",
            })

    for i, g in enumerate(gates, start=1):
        sites.append({
            "geometry": point_inside(campus, g),
            "site_label": f"Entry Point {i}",
            "site_source": "gate_crossing",
            "area_sqm": ROADSIDE_AREA_SQM,
            "parking_type": "street",
        })

    kept = []
    for s in sites:
        if all(s["geometry"].distance(k["geometry"]) > DEDUPE_M for k in kept):
            kept.append(s)
    logger.info(f"Candidate sites: {len(kept)} kept of {len(sites)} (deduplicated within {DEDUPE_M} m)")
    return gpd.GeoDataFrame(kept, crs=UTM_CRS)


def load_existing_chargers(config) -> gpd.GeoDataFrame:
    """Real public chargers from OpenChargeMap (key from OCM_API_KEY), or the cached copy."""
    loader = OCMDataLoader(config)
    stations = loader.fetch_stations()
    if stations.empty:
        print(f"Existing chargers (OpenChargeMap): none {loader._scope()}")
    else:
        d = stations["distance_to_campus_km"]
        nearest = stations.loc[d.idxmin()]
        bands = ", ".join(f"{int((d <= km).sum())} within {km} km" for km in (50, 100, 300))
        print(f"Existing chargers (OpenChargeMap): {len(stations)} {loader._scope()} ({bands}); "
              f"nearest {nearest['distance_to_campus_km']:.1f} km ({nearest['name']}, {nearest['town']})")
    return stations


def build_candidates(features: gpd.GeoDataFrame, config):
    """Build zones, power points and candidate features on the real campus."""
    features_utm = features.to_crs(UTM_CRS)
    campus_row = features_utm[features_utm["osm_id"] == CAMPUS_OSM_ID]
    if campus_row.empty:
        raise RuntimeError(f"Campus boundary {CAMPUS_OSM_ID} not found in OSM extract")
    campus = campus_row.geometry.iloc[0]
    logger.info(f"Campus boundary area: {campus.area / 1e4:.1f} ha")

    zones_utm, gates = build_zones(features_utm, campus, config)
    zones = zones_utm.to_crs(WGS84_CRS)
    zones.to_file("data/external/vitap_zone_polygons.geojson", driver="GeoJSON")
    logger.info(f"Saved {len(zones)} zones: {list(zones['zone_id'])}")

    # OSM maps no transformer or substation here; the campus generator house stands in for the grid point
    power = features_utm[features_utm["power"].isin(["generator", "transformer", "substation"])
                         & features_utm.geometry.centroid.within(campus)].copy()
    power["geometry"] = power.geometry.centroid
    power["power_type"] = "transformer"
    power["note"] = "ASSUMED grid connection point: OSM-mapped campus power building. Not a surveyed transformer."
    power = power[["osm_id", "power", "power_type", "note", "geometry"]].to_crs(WGS84_CRS)
    power.to_file("data/external/vitap_campus_power_points.geojson", driver="GeoJSON")
    logger.info(f"Grid connection points: {list(power['osm_id'])}")

    cands = build_sites(features_utm, campus, gates).to_crs(WGS84_CRS)
    gates_wgs84 = list(gpd.GeoSeries(gates, crs=UTM_CRS).to_crs(WGS84_CRS))

    pois = features[features[POI_KEYS].notna().any(axis=1) & features["highway"].isna()].copy()
    pois["geometry"] = pois.to_crs(UTM_CRS).geometry.centroid.to_crs(WGS84_CRS)
    roads = features[features["highway"].notna()]
    no_data = gpd.GeoDataFrame(geometry=[], crs=WGS84_CRS)

    gen = CandidateGenerator(config)
    cands = gen._assign_zones(cands, zones[["zone_id", "land_cost_tier", "primary_use", "geometry"]])
    cands = cands[~cands.index.duplicated(keep="first")]
    cands = gen._calculate_max_chargers(cands)
    cands = gen._calculate_grid_connection(cands, power)
    cands = gen._calculate_poi_score(cands, pois)
    cands = cands.rename(columns={"poi_score": "poi_score_raw"})
    cands["poi_score"] = cands["poi_score_raw"] / max(cands["poi_score_raw"].max(), 1e-9)
    cands = gen._calculate_catchment_population(cands, no_data)
    stations = load_existing_chargers(config)
    cands = gen._calculate_competition(cands, stations)
    cands = gen._calculate_traffic_accessibility(cands, roads, gates_wgs84=gates_wgs84)
    cands = gen._calculate_costs(cands)

    cands = cands.sort_values(["zone", "site_label"]).reset_index(drop=True)
    cands["candidate_id"] = [f"CAND_{i + 1:03d}" for i in range(len(cands))]
    cands["name"] = cands["site_label"]
    cands["latitude"] = cands.geometry.y
    cands["longitude"] = cands.geometry.x

    tracker = ProvenanceTracker("candidates_campus", "realign_campus.build_candidates")
    prov = {
        "latitude": (Provenance.DERIVED, "osm", "Site position from OSM parking centroid, nearest drivable road, or boundary crossing"),
        "longitude": (Provenance.DERIVED, "osm", "Site position from OSM parking centroid, nearest drivable road, or boundary crossing"),
        "site_source": (Provenance.META, "realign_campus", "How the site position was derived"),
        "zone": (Provenance.DERIVED, "zone_polygons", "Zone from OSM-derived zone polygons (spatial join)"),
        "land_cost_tier": (Provenance.SYNTHETIC, "settings.zone_definitions", "Assumed tier per zone"),
        "area_sqm": (Provenance.DERIVED, "osm", f"OSM parking polygon area; roadside and entry sites ASSUME {ROADSIDE_AREA_SQM} sqm"),
        "grid_dist_m": (Provenance.SYNTHETIC, "assumption", "Distance to ASSUMED grid point (OSM campus power building)"),
        "grid_connection_cost_inr_lakhs": (Provenance.SYNTHETIC, "assumption", "From assumed grid point distance and configured rate"),
        "poi_score_raw": (Provenance.DERIVED, "osm", "Weighted, distance-decayed POI count within 1 km"),
        "poi_score": (Provenance.DERIVED, "osm", "poi_score_raw scaled 0-1 by the campus maximum"),
        "traffic_accessibility_score": (Provenance.DERIVED, "osm", "Inverse distance to OSM-derived campus entry points"),
        "existing_chargers_500m": (Provenance.DERIVED, "openchargemap", "OpenChargeMap stations within 500 m"),
        "existing_chargers_1km": (Provenance.DERIVED, "openchargemap", "OpenChargeMap stations within 1 km"),
        "existing_chargers_3km": (Provenance.DERIVED, "openchargemap", "OpenChargeMap stations within 3 km"),
        "catchment_population": (Provenance.SYNTHETIC, "assumption", "0 - no building population data"),
        "total_capex_inr_lakhs": (Provenance.SYNTHETIC, "cost_config", "Land + assumed grid + construction"),
    }
    for col, (p, source, desc) in prov.items():
        tracker.add_column(col, p, source, desc)
    cands = tracker.finalize(cands, notes="Realigned to the OSM VIT-AP campus boundary.")
    return cands


def add_ml_demand(cands: pd.DataFrame, config) -> Tuple[pd.DataFrame, dict]:
    """Train the demand model on synthetic history and add annual demand per site."""
    weather = WeatherLoader(config).fetch_all_years([2024])
    if weather.empty:
        raise RuntimeError("Weather data unavailable (Open-Meteo); cannot generate demand history")
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])

    adoption = ev_adoption("Andhra Pradesh")  # real share of new vehicles that are electric (Vahan)
    demand = generate_synthetic_demand(cands, weather, config, demand_scale=adoption["demand_scale"] if adoption else 1.0)
    df_c = pd.DataFrame(cands.drop(columns="geometry", errors="ignore"))

    default_metrics = EVChargingDemandPredictor(config=config).train(demand, df_c, test_size=0.2, temporal_split=True)

    # GA + ML hybrid: the GA searches Random Forest settings on a validation window
    tuning = EVChargingDemandPredictor(config=config)
    feats = tuning.engineer_features(demand, df_c)
    X, y = tuning.prepare_training_data(feats)
    best_params, tuning_history = tune_random_forest_ga(X, y, feats["timestamp"].values)
    Path("outputs/ml_results").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(tuning_history).to_csv("outputs/ml_results/ga_tuning_history.csv", index=False)

    predictor = EVChargingDemandPredictor(config=config)
    predictor.model.set_params(**best_params)
    metrics = predictor.train(demand, df_c, test_size=0.2, temporal_split=True)
    predictor.save_model("models/demand_predictor_campus")

    # Baseline: mean demand per site, hour and weekend flag over the training period
    ordered = demand.sort_values("timestamp")
    split = int(len(ordered) * 0.8)
    train, test = ordered.iloc[:split], ordered.iloc[split:]
    keys = ["candidate_id", "hour", "is_weekend"]
    base = train.groupby(keys)["demand_kwh"].mean().rename("baseline")
    test = test.join(base, on=keys)
    baseline_rmse = float(np.sqrt(np.mean((test["demand_kwh"] - test["baseline"].fillna(train["demand_kwh"].mean())) ** 2)))

    preds = predictor.predict(demand.drop(columns=["demand_kwh"]), df_c)
    annual = pd.Series(preds, index=demand.index).groupby(demand["candidate_id"]).sum()
    df_c["predicted_annual_demand_kwh"] = df_c["candidate_id"].map(annual)
    df_c["demand_index"] = 100 * df_c["predicted_annual_demand_kwh"] / df_c["predicted_annual_demand_kwh"].max()

    print("\n=== ML demand model (trained on SYNTHETIC demand history, tested on last 20% of 2024) ===")
    print(f"Baseline (site x hour x weekend average): RMSE {baseline_rmse:.3f} kWh/h")
    print(f"Random Forest, default settings:          RMSE {default_metrics['test_rmse']:.3f} kWh/h | R^2 {default_metrics['test_r2']:.3f}")
    print(f"Random Forest, GA-tuned settings:         RMSE {metrics['test_rmse']:.3f} kWh/h | R^2 {metrics['test_r2']:.3f}")
    print(f"GA-tuned settings: {best_params}")
    if adoption:
        print(f"Demand level: {adoption['state']} EV share {adoption['ev_share']:.2%} in {adoption['year']} (Vahan) "
              f"-> x{adoption['demand_scale']:.2f} of the 3% baseline")

    summary = {
        "training_data": f"SYNTHETIC demand history ({len(df_c)} sites x 2024 hourly), Open-Meteo weather"
                         + (f", scaled to {adoption['state']}'s REAL EV share (Vahan)" if adoption else ""),
        "ev_adoption": adoption,
        "split": "temporal: first 80% train, last 20% test",
        "baseline_rmse": baseline_rmse,
        "default_rf": {k: float(v) for k, v in default_metrics.items()},
        "ga_tuned_rf": {k: float(v) for k, v in metrics.items()},
        "ga_tuned_params": best_params,
    }
    return df_c, summary



CANDIDATES_CSV = "data/processed/candidates_campus.csv"
DEMAND_CSV = "data/processed/candidates_campus_demand.csv"
SCORED_CSV = "data/processed/candidates_demo_fuzzy_scored.csv"
SOLUTION_CSV = "data/processed/ga_solution_demo.csv"
GA_CONFIG = "config/ga_config_demo.yaml"


def stage_candidates(config=None, refresh_osm: bool = False) -> pd.DataFrame:
    """Build zones and candidate sites from the OSM campus extract."""
    config = config or get_config("settings")
    features = osm_to_gdf(fetch_osm(Path(config.paths.raw) / "osm" / "campus_osm.json", refresh_osm))
    export_osm_layers(features, Path(config.paths.raw) / "osm")
    cands = pd.DataFrame(build_candidates(features, config).drop(columns="geometry"))
    cands.to_csv(CANDIDATES_CSV, index=False)
    print(f"Candidates: {len(cands)} sites in zones {cands['zone'].value_counts().to_dict()}")
    return cands


def stage_ml(config=None) -> pd.DataFrame:
    """Train the GA-tuned demand model and add predicted annual demand per site."""
    config = config or get_config("settings")
    df_c, summary = add_ml_demand(pd.read_csv(CANDIDATES_CSV), config)
    df_c.to_csv(DEMAND_CSV, index=False)
    Path("outputs/ml_results").mkdir(parents=True, exist_ok=True)
    Path("outputs/ml_results/metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return df_c


def stage_fuzzy() -> pd.DataFrame:
    """Score every site with the fuzzy suitability system."""
    scored = FuzzyEVChargingScorer().score_candidates(pd.read_csv(DEMAND_CSV))
    scored.to_csv(SCORED_CSV, index=False)
    s = scored["fuzzy_suitability_score"]
    print(f"Fuzzy suitability: {s.min():.3f} - {s.max():.3f} | unscored: {int(s.isna().sum())}")
    return scored


def run_ga(scored_path: str = SCORED_CSV, n_stations=None, n_runs: int = 10):
    """Run the placement GA from n_runs seeds and keep the best run.

    Returns (ga, best, history, feasible) for the best run. Independent restarts
    guard against a single run converging on a local optimum.
    """
    results = []
    for seed in range(n_runs):
        random.seed(seed)
        np.random.seed(seed)
        ga = GeneticAlgorithmOptimizer(GA_CONFIG)
        if n_stations is not None:
            ga.config["n_stations_target"] = n_stations
        ga.load_candidates(scored_path)
        best, history, feasible = ga.optimize()
        results.append((ga, best, history, feasible))
    return max(results, key=lambda r: (r[3], r[1].fitness))


def stage_ga() -> pd.DataFrame:
    """Pick the station set, save it, and report how many stations the budget allows."""
    ga, best, history, _ = run_ga()
    solution, feasible = ga.get_solution_details(best)
    if not feasible:
        solution = pd.DataFrame(columns=list(ga.candidates_df.columns) + ["selected_in_ga", "ga_rank"])
    solution.to_csv(SOLUTION_CSV, index=False)
    out = Path("outputs/ga_results")
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"generation": range(len(history)), "best_fitness": history}).to_csv(
        out / "fitness_history.csv", index=False)

    print(f"\n=== GA (best of 10 runs, {len(history)} generations, K={ga.config['n_stations_target']}) ===")
    if feasible:
        print(solution[["ga_rank", "candidate_id", "name", "zone", "fuzzy_suitability_score",
                        "total_capex_inr_lakhs"]].to_string(index=False))
    else:
        print("NO FEASIBLE SOLUTION under the demo config (budget and minimum separation)")

    rows = []
    for k in range(2, 7):
        _, best_k, _, ok = run_ga(n_stations=k, n_runs=5)
        comps = best_k.fitness_components or {}
        rows.append({"n_stations": k, "feasible": ok, "fitness": best_k.fitness,
                     "total_cost_lakhs": comps.get("total_cost_lakhs", float("nan"))})
    capacity = pd.DataFrame(rows)
    capacity.to_csv(out / "capacity.csv", index=False)
    print("\nStations that fit the budget and spacing rules:")
    print(capacity.to_string(index=False))
    return solution


def run_all(refresh_osm: bool = False):
    """Run every campus stage in order."""
    config = get_config("settings")
    stage_candidates(config, refresh_osm)
    stage_ml(config)
    stage_fuzzy()
    stage_ga()
