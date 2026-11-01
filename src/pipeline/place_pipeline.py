"""
Plan EV charging stations for any campus or area, with the user's budget.

    python run_pipeline.py place --place "IIT Madras" --budget 300
    python run_pipeline.py place --boundary my_area.geojson --name "My campus" --budget 150

The same Data -> ML -> Fuzzy -> GA method as the VIT-AP pipeline, made
location-independent:

  * the area comes from a free Nominatim search (or a GeoJSON the user drew);
  * zones come from OpenStreetMap tags (hostel, university, shops, sports...)
    instead of hand-listed building names;
  * the demand model is retrained on the place's own synthetic history with
    the settings the GA tuned for VIT-AP (no re-tuning per place);
  * the station count follows the budget: every affordable count is planned
    and the best-scoring plan is chosen, alongside an exact minimum-budget table.

Caches go to data/places/<slug>/, results to outputs/places/<slug>/, and the
web dashboard data to web/data/places/<slug>.js.
"""

import hashlib
import json
import math
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, mapping, shape
from shapely.ops import nearest_points, unary_union

from src.data.candidate_generator import CandidateGenerator
from src.data.config_loader import get_config
from src.data.provenance import Provenance, ProvenanceTracker
from src.data.synthetic_generator import DemandGenerator
from src.data.vahan_loader import ev_adoption
from src.evaluation.evaluate import evaluate, min_budget_table
from src.fuzzy.scorer import FuzzyEVChargingScorer
from src.ga.optimizer import GeneticAlgorithmOptimizer
from src.ml.demand_predictor import EVChargingDemandPredictor
from src.pipeline.campus_pipeline import (
    DRIVABLE, OVERPASS_ENDPOINTS, POI_KEYS, ROADSIDE_MAX_DIST_M, UTM_CRS, WGS84_CRS,
    osm_to_gdf, point_inside,
)

HEADERS = {"User-Agent": "ev-charging-site-optimizer/1.0 (student project)"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
OCM_URL = "https://api.openchargemap.io/v3/poi/"
GA_CONFIG = "config/ga_config_demo.yaml"
VITAP_ML_METRICS = Path("outputs/ml_results/metrics.json")

# Zone rules, in priority order where zones overlap. Each rule is (tag, allowed values);
# "*" means any value. A feature joins the first zone whose rule it matches.
ZONE_RULES = [
    ("commercial", [("amenity", {"food_court", "restaurant", "cafe", "fast_food", "canteen", "bank", "atm", "marketplace"}),
                    ("shop", "*"), ("building", {"commercial", "retail", "supermarket", "kiosk"})]),
    ("residential", [("building", {"dormitory", "residential", "apartments", "house", "hostel", "dormitories"}),
                     ("tourism", {"hostel", "guest_house"}), ("amenity", {"dormitory"})]),
    ("academic", [("building", {"university", "college", "school", "educational", "library"}),
                  ("amenity", {"library", "college", "school", "university", "research_institute"})]),
    ("admin", [("office", "*"), ("building", {"office", "government", "civic"}), ("amenity", {"townhall", "courthouse"})]),
    ("sports", [("leisure", {"pitch", "sports_centre", "stadium", "track", "fitness_centre", "swimming_pool", "sports_hall"})]),
]
ZONE_LABELS = {
    "commercial": ("Commercial", "commercial"), "residential": ("Residences", "residential"),
    "academic": ("Academic", "academic"), "admin": ("Administration", "admin"), "sports": ("Sports", "sports"),
    "gate": ("Entry points", "access"), "campus_general": ("Rest of area", "mixed"),
}
ZONE_BUFFER_M = 30
GATE_BUFFER_M = 60
GATE_MERGE_M = 80
ROADSIDE_AREA_SQM = 250
DEFAULT_GRID_DIST_M = 250  # assumed when OSM maps no power infrastructure near the area
OVERPASS_RACE = OVERPASS_ENDPOINTS[:2] + ["https://maps.mail.ru/osm/tools/overpass/api/interpreter"]


@dataclass
class PlaceSettings:
    budget_lakhs: float
    n_stations: Optional[int] = None  # None = choose the best count the budget allows
    spacing_m: float = 300
    coverage_m: float = 250
    max_sites: int = 40
    max_area_km2: float = 25
    ga_runs: int = 10
    max_stations: int = 12
    ml_day_step: int = 1             # 1 = every day of 2024; 4 = one day in four
    ml_trees: Optional[int] = None   # None = the tree count the GA tuned on VIT-AP
    exhaustive_cap: int = 200_000    # skip the exhaustive optimum check above this many valid plans
    deadline_s: Optional[float] = None  # hosts that stop requests (Vercel: 300 s) finish early with the best plan so far

    @classmethod
    def fast(cls, **kw) -> "PlaceSettings":
        """Quicker settings for the live web planner: 3 GA runs per count, 1 day in 4, 100 trees, up to 8 stations."""
        return cls(**{"ga_runs": 3, "ml_day_step": 4, "ml_trees": 100, "max_stations": 8, "exhaustive_cap": 40_000, **kw})


def write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """Write a GeoDataFrame as GeoJSON; works for empty layers too."""
    Path(path).write_text(gdf.to_crs(WGS84_CRS).to_json() if len(gdf) else '{"type": "FeatureCollection", "features": []}',
                          encoding="utf-8")


def read_geojson(path: Path) -> gpd.GeoDataFrame:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data["features"]:
        return gpd.GeoDataFrame(geometry=[], crs=WGS84_CRS)
    return gpd.GeoDataFrame.from_features(data["features"], crs=WGS84_CRS)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "place"


# ───────────────────────── location ─────────────────────────

def geocode(query: str, cache: Path, refresh: bool = False) -> Dict:
    """Find the place's outline with Nominatim (free OpenStreetMap search)."""
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    resp = requests.get(NOMINATIM, params={"q": query, "format": "jsonv2", "polygon_geojson": 1, "limit": 8},
                        headers=HEADERS, timeout=60)
    resp.raise_for_status()
    hits = [h for h in resp.json() if h.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")]
    if not hits:
        raise RuntimeError(f"No area outline found for '{query}'. Try a more specific name or pass --boundary.")
    preferred = [h for h in hits if (h["category"], h["type"]) in
                 {("amenity", "university"), ("amenity", "college"), ("amenity", "school"), ("landuse", "education")}]
    hit = (preferred or hits)[0]
    place = {"query": query, "name": hit.get("name") or query, "display_name": hit["display_name"],
             "osm": f"{hit['osm_type']}/{hit['osm_id']}", "category": f"{hit['category']}={hit['type']}",
             "geometry": hit["geojson"]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(place), encoding="utf-8")
    return place


def boundary_from_file(path: str, name: str) -> Dict:
    """Use an area the user drew (GeoJSON file with one or more polygons)."""
    gdf = gpd.read_file(path).to_crs(WGS84_CRS)
    return boundary_from_geometry(gdf.geometry.union_all(), name, query=str(path))


def boundary_from_geometry(geom, name: str, query: str = "drawn on the map") -> Dict:
    """Use an area drawn on the website's map (a GeoJSON Polygon or MultiPolygon in WGS84)."""
    from shapely.validation import make_valid

    g = shape(geom) if isinstance(geom, dict) else geom
    if not g.is_valid:
        g = make_valid(g)  # e.g. a boundary drawn crossing itself
    parts = [p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
    g = unary_union(parts) if parts else None
    if g is None or g.is_empty or g.area == 0:
        raise RuntimeError("The drawn area is not a valid shape. Draw at least three points around the area.")
    lon, lat = g.centroid.x, g.centroid.y
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise RuntimeError("The drawn area's coordinates are not longitude/latitude.")
    return {"query": query, "name": name, "display_name": name, "osm": "user-drawn", "category": "user area",
            "geometry": mapping(g)}


def _geometry_hash(geometry: Dict) -> str:
    return hashlib.sha1(json.dumps(geometry, sort_keys=True, default=list).encode()).hexdigest()[:8]


# ───────────────────────── data fetching ─────────────────────────

def fetch_osm_area(bbox, cache: Path, refresh: bool = False) -> Dict:
    """Ways, relations and tagged nodes inside the bbox (south, west, north, east)."""
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    s, w, n, e = bbox
    q = (f"[out:json][timeout:180];(way({s},{w},{n},{e});relation({s},{w},{n},{e});"
         f'node[~"^(amenity|shop|leisure|office|power|tourism|public_transport|building)$"~"."]({s},{w},{n},{e}););out geom;')
    data = _overpass_race(q)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _overpass_race(query: str, timeout: float = 180) -> Dict:
    """Ask the public Overpass mirrors at once and keep the first good answer (their load varies a lot)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    def ask(endpoint):
        resp = requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "elements" not in data:
            raise ValueError("no elements in the answer")
        return data

    pool = ThreadPoolExecutor(len(OVERPASS_RACE))
    futures = {pool.submit(ask, ep): ep for ep in OVERPASS_RACE}
    errors = []
    try:
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for f in done:
                ep = futures.pop(f)
                if f.exception() is None:
                    return f.result()
                errors.append(f"{ep}: {f.exception()}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("OpenStreetMap (Overpass) did not answer. Try again in a minute. " + "; ".join(errors))


def state_of(lat: float, lon: float, cache: Path) -> Optional[str]:
    """The Indian state an area lies in, from Nominatim reverse geocoding (None when unavailable)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("state")
    try:
        resp = requests.get(NOMINATIM.replace("/search", "/reverse"), headers=HEADERS, timeout=30,
                            params={"lat": round(lat, 5), "lon": round(lon, 5), "format": "jsonv2", "zoom": 5,
                                    "accept-language": "en"})
        resp.raise_for_status()
        addr = resp.json().get("address", {})
    except (requests.RequestException, ValueError):
        return None  # not cached: try again next run
    state = addr.get("state") or addr.get("territory") or addr.get("union_territory")
    cache.write_text(json.dumps({"state": state, "country": addr.get("country_code")}), encoding="utf-8")
    return state


def fetch_weather(lat: float, lon: float, cache: Path, year: int = 2024) -> pd.DataFrame:
    """Hourly weather for one year from the Open-Meteo archive (free, no key)."""
    if not cache.exists():
        resp = requests.get(OPEN_METEO, params={
            "latitude": round(lat, 4), "longitude": round(lon, 4), "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31", "hourly": "temperature_2m,relative_humidity_2m,precipitation",
            "timezone": "Asia/Kolkata"}, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        h = resp.json()["hourly"]
        pd.DataFrame({"timestamp": h["time"], "temperature_c": h["temperature_2m"],
                      "relative_humidity_pct": h["relative_humidity_2m"], "precipitation_mm": h["precipitation"]}
                     ).to_csv(cache, index=False)
    weather = pd.read_csv(cache, parse_dates=["timestamp"])
    return weather.ffill().bfill()


def fetch_chargers(area, cache: Path, margin_km: float = 5.0) -> gpd.GeoDataFrame:
    """Public chargers from OpenChargeMap within margin_km of the area (key from OCM_API_KEY)."""
    import os
    key = os.environ.get("OCM_API_KEY", "")
    if not cache.exists():
        min_lon, min_lat, max_lon, max_lat = area.bounds
        d_lat, d_lon = margin_km / 111.0, margin_km / (111.0 * math.cos(math.radians((min_lat + max_lat) / 2)))
        params = {"output": "json", "maxresults": 2000, "compact": "false", "verbose": "false",
                  "boundingbox": f"({max_lat + d_lat},{min_lon - d_lon}),({min_lat - d_lat},{max_lon + d_lon})"}
        headers = dict(HEADERS, **({"X-API-Key": key} if key else {}))
        try:
            resp = requests.get(OCM_URL, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
            stations = resp.json()
        except requests.RequestException:
            stations = None  # no key or API unavailable: competition is treated as unknown (0)
        rows = []
        for st in stations or []:
            a = st.get("AddressInfo") or {}
            status = (st.get("StatusType") or {}).get("IsOperational")
            if status is False or a.get("Latitude") is None:
                continue
            rows.append({"station_id": str(st.get("ID", "")), "name": a.get("Title", ""), "town": a.get("Town", ""),
                         "state": a.get("StateOrProvince", ""), "operator": (st.get("OperatorInfo") or {}).get("Title", ""),
                         "status": (st.get("StatusType") or {}).get("Title", ""),
                         "num_connectors": st.get("NumberOfPoints") or len(st.get("Connections") or []),
                         "max_power_kw": max([c.get("PowerKW") or 0 for c in st.get("Connections") or []], default=0),
                         "geometry": Point(a["Longitude"], a["Latitude"])})
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84_CRS) if rows else \
            gpd.GeoDataFrame(columns=["station_id", "name", "town", "state", "operator", "status",
                                      "num_connectors", "max_power_kw", "geometry"], geometry="geometry", crs=WGS84_CRS)
        centroid = area.centroid
        gdf["distance_to_campus_km"] = [_haversine_km(centroid.y, centroid.x, p.y, p.x) for p in gdf.geometry]
        write_geojson(gdf, cache)
        cache.with_suffix(".status").write_text("fetched" if stations is not None else "unavailable", encoding="utf-8")
    return read_geojson(cache)


def _haversine_km(lat1, lon1, lat2, lon2):
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ───────────────────────── zones and sites ─────────────────────────

def zone_of(row) -> Optional[str]:
    """Zone a feature belongs to by its OSM tags, or None."""
    for zone_id, rules in ZONE_RULES:
        for tag, allowed in rules:
            value = row.get(tag)
            if isinstance(value, str) and value and (allowed == "*" or value in allowed):
                return zone_id
    return None


def build_zones(features_utm: gpd.GeoDataFrame, area, config):
    """Zones from tagged OSM features inside the area, plus entry points and the rest of the area."""
    zone_defs = config._data["zones"]["zone_definitions"]
    inside = features_utm[features_utm.geometry.centroid.within(area)
                          & (features_utm.geometry.area < 0.2 * area.area)].copy()
    inside["zone"] = inside.apply(zone_of, axis=1)
    zones, taken, anchors = [], None, []

    def add(zone_id, geom, note):
        nonlocal taken
        geom = geom.intersection(area)
        if taken is not None:
            geom = geom.difference(taken)
        if geom.is_empty or geom.area < 100:
            return False
        taken = geom if taken is None else taken.union(geom)
        name, use = ZONE_LABELS[zone_id]
        zones.append({"zone_id": zone_id, "zone_name": name, "land_cost_tier": zone_defs[zone_id]["land_cost_tier"],
                      "primary_use": use, "description": note, "provenance": "DERIVED",
                      "source": "osm_tags", "note": "Derived from OpenStreetMap tags. Not an official zoning plan.",
                      "geometry": geom})
        return True

    for zone_id, _ in ZONE_RULES:
        members = inside[inside["zone"] == zone_id]
        if members.empty:
            continue
        if add(zone_id, unary_union([g.buffer(ZONE_BUFFER_M) for g in members.geometry]),
               f"{len(members)} OSM features tagged as {zone_id}"):
            anchors.append(members)

    roads = features_utm[features_utm["highway"].isin(DRIVABLE) & (features_utm.geom_type == "LineString")]
    crossings = []
    for line in roads.geometry:
        hit = line.intersection(area.boundary)
        pts = [hit] if hit.geom_type == "Point" else [g for g in getattr(hit, "geoms", []) if g.geom_type == "Point"]
        crossings.extend(pts)
    gates = []
    for pt in crossings:
        if all(pt.distance(g) > GATE_MERGE_M for g in gates):
            gates.append(pt)
    if gates:
        add("gate", unary_union([g.buffer(GATE_BUFFER_M) for g in gates]), f"{len(gates)} road crossings of the boundary")
    add("campus_general", area, "Remainder of the area")
    anchors = pd.concat(anchors) if anchors else inside.iloc[0:0]
    return gpd.GeoDataFrame(zones, crs=UTM_CRS), gates, anchors, roads


def build_sites(features_utm, area, gates, anchors, roads, max_sites: int) -> gpd.GeoDataFrame:
    """Parking lots, roadside spots beside tagged buildings, and entry points; thinned to max_sites."""
    candidates = []
    parking = features_utm[(features_utm["amenity"] == "parking") & features_utm.geom_type.isin(["Polygon", "MultiPolygon"])]
    for _, p in parking.iterrows():
        if area.buffer(10).contains(p.geometry.centroid):
            candidates.append({"geometry": point_inside(area, p.geometry.centroid), "site_label": f"OSM Parking Lot ({p['osm_id']})",
                               "site_source": "osm_parking", "area_sqm": round(p.geometry.area, 1),
                               "parking_type": p["parking"] or "surface", "priority": 0})

    road_net = unary_union(list(roads.geometry)).intersection(area.buffer(-5)) if len(roads) else None
    if road_net is not None and not road_net.is_empty:
        named = anchors[anchors["name"].notna()]
        unnamed = anchors[anchors["name"].isna()].assign(_a=lambda d: d.geometry.area).sort_values("_a", ascending=False)
        for priority, group in ((1, named), (2, unnamed)):
            for _, a in group.iterrows():
                if road_net.distance(a.geometry) > ROADSIDE_MAX_DIST_M:
                    continue
                label = a["name"] if isinstance(a["name"], str) else f"{ZONE_LABELS[a['zone']][0].lower()} building"
                candidates.append({"geometry": nearest_points(road_net, a.geometry)[0], "site_label": f"Roadside near {label}",
                                   "site_source": "roadside_near_osm_feature", "area_sqm": ROADSIDE_AREA_SQM,
                                   "parking_type": "street", "priority": priority})

    for i, g in enumerate(gates, start=1):
        candidates.append({"geometry": point_inside(area, g), "site_label": f"Entry Point {i}", "site_source": "gate_crossing",
                           "area_sqm": ROADSIDE_AREA_SQM, "parking_type": "street", "priority": 1})

    candidates.sort(key=lambda c: c["priority"])
    spacing = 60.0
    while True:  # widen the minimum gap until the site count fits, which spreads sites evenly
        kept = []
        for c in candidates:
            if all(c["geometry"].distance(k["geometry"]) > spacing for k in kept):
                kept.append(c)
        if len(kept) <= max_sites or spacing > 2000:
            break
        spacing *= 1.15
    if not kept:
        raise RuntimeError("No candidate sites found: OSM has no parking, tagged buildings or roads in this area.")
    return gpd.GeoDataFrame(kept, crs=UTM_CRS).drop(columns="priority")


def build_candidates(features: gpd.GeoDataFrame, area_wgs, config, settings: PlaceSettings, out: Path, cache: Path):
    """Zones, sites and per-site features for the area."""
    features_utm = features.to_crs(UTM_CRS)
    area = gpd.GeoSeries([area_wgs], crs=WGS84_CRS).to_crs(UTM_CRS).iloc[0]
    zones_utm, gates, anchors, roads = build_zones(features_utm, area, config)
    zones = zones_utm.to_crs(WGS84_CRS)
    write_geojson(zones, out / "zones.geojson")

    sites = build_sites(features_utm, area, gates, anchors, roads, settings.max_sites).to_crs(WGS84_CRS)
    gates_wgs84 = list(gpd.GeoSeries(gates, crs=UTM_CRS).to_crs(WGS84_CRS)) if gates else None

    power = features_utm[features_utm["power"].isin(["transformer", "substation", "generator"])
                         & features_utm.geometry.centroid.within(area.buffer(1500))].copy()
    gen = CandidateGenerator(config)
    cands = gen._assign_zones(sites, zones[["zone_id", "land_cost_tier", "primary_use", "geometry"]])
    cands = cands[~cands.index.duplicated(keep="first")]
    cands = gen._calculate_max_chargers(cands)
    if len(power):
        power["geometry"] = power.geometry.centroid
        power["power_type"] = "transformer"
        cands = gen._calculate_grid_connection(cands, power.to_crs(WGS84_CRS))
        grid_note = "Distance to the nearest OSM-mapped power feature (transformer, substation or generator)"
    else:
        cands["grid_dist_m"] = float(DEFAULT_GRID_DIST_M)
        cands["nearest_transformer_id"] = "ASSUMED"
        grid_note = f"ASSUMED {DEFAULT_GRID_DIST_M} m: OSM maps no power infrastructure near this area"

    pois = features[features[POI_KEYS].notna().any(axis=1) & features["highway"].isna()].copy()
    pois["geometry"] = pois.to_crs(UTM_CRS).geometry.centroid.to_crs(WGS84_CRS)
    cands = gen._calculate_poi_score(cands, pois)
    cands = cands.rename(columns={"poi_score": "poi_score_raw"})
    cands["poi_score"] = cands["poi_score_raw"] / max(cands["poi_score_raw"].max(), 1e-9)
    cands = gen._calculate_catchment_population(cands, gpd.GeoDataFrame(geometry=[], crs=WGS84_CRS))
    chargers = fetch_chargers(area_wgs, cache / "chargers.geojson")
    cands = gen._calculate_competition(cands, chargers)
    cands = gen._calculate_traffic_accessibility(cands, features[features["highway"].notna()], gates_wgs84=gates_wgs84)
    cands = gen._calculate_costs(cands)

    cands = cands.sort_values(["zone", "site_label"]).reset_index(drop=True)
    cands["candidate_id"] = [f"CAND_{i + 1:03d}" for i in range(len(cands))]
    cands["name"] = cands["site_label"]
    cands["latitude"] = cands.geometry.y
    cands["longitude"] = cands.geometry.x

    charger_note = ("OpenChargeMap stations within 3 km" if (cache / "chargers.status").read_text() == "fetched"
                    else "0 - OpenChargeMap unavailable (set OCM_API_KEY)")
    tracker = ProvenanceTracker("place_candidates", "place_pipeline.build_candidates")
    for col, (p, src, desc) in {
        "latitude": (Provenance.DERIVED, "osm", "OSM parking centroid, nearest drivable road, or boundary crossing"),
        "longitude": (Provenance.DERIVED, "osm", "OSM parking centroid, nearest drivable road, or boundary crossing"),
        "zone": (Provenance.DERIVED, "osm_tags", "Zone from OSM tags (spatial join)"),
        "land_cost_tier": (Provenance.SYNTHETIC, "settings.zone_definitions", "Assumed tier per zone"),
        "area_sqm": (Provenance.DERIVED, "osm", f"OSM parking polygon area; other sites ASSUME {ROADSIDE_AREA_SQM} sqm"),
        "grid_dist_m": (Provenance.SYNTHETIC, "assumption", grid_note),
        "poi_score": (Provenance.DERIVED, "osm", "Weighted POI score scaled 0-1 by the area maximum"),
        "existing_chargers_3km": (Provenance.DERIVED, "openchargemap", charger_note),
        "total_capex_inr_lakhs": (Provenance.SYNTHETIC, "cost_config", "Land + grid + construction"),
    }.items():
        tracker.add_column(col, p, src, desc)
    cands = tracker.finalize(cands, notes="Generic place pipeline.")
    return cands, zones, chargers, grid_note, charger_note


# ───────────────────────── ML demand ─────────────────────────

def train_demand(cands: pd.DataFrame, weather: pd.DataFrame, config, day_step: int = 1,
                 trees: Optional[int] = None, adoption: Optional[Dict] = None) -> (pd.DataFrame, Dict):
    """Synthetic hourly demand for the place, a Random Forest with the VIT-AP GA-tuned settings, annual demand.

    day_step > 1 keeps one day in day_step (every hour of those days) to train faster; annual demand
    is scaled back up to the full year.
    """
    full_hours = len(weather)
    if day_step > 1:
        day = (weather["timestamp"].dt.normalize() - weather["timestamp"].min().normalize()).dt.days
        weather = weather[day % day_step == 0].reset_index(drop=True)
    gen = DemandGenerator(config, seed=123, demand_scale=adoption["demand_scale"] if adoption else 1.0)
    demand = pd.concat([gen._generate_candidate_year(c, weather, 2024) for _, c in cands.iterrows()], ignore_index=True)

    params = {}
    if VITAP_ML_METRICS.exists():
        params = json.loads(VITAP_ML_METRICS.read_text(encoding="utf-8")).get("ga_tuned_params", {})
    if trees and params:
        params = dict(params, n_estimators=trees)
    predictor = EVChargingDemandPredictor(config=config)
    if params:
        predictor.model.set_params(**params)
    metrics = predictor.train(demand, cands, test_size=0.2, temporal_split=True)

    ordered = demand.sort_values("timestamp")
    split = int(len(ordered) * 0.8)
    train, test = ordered.iloc[:split], ordered.iloc[split:]
    keys = ["candidate_id", "hour", "is_weekend"]
    test = test.join(train.groupby(keys)["demand_kwh"].mean().rename("baseline"), on=keys)
    baseline_rmse = float(np.sqrt(np.mean((test["demand_kwh"] - test["baseline"].fillna(train["demand_kwh"].mean())) ** 2)))

    preds = predictor.predict(demand.drop(columns=["demand_kwh"]), cands)
    annual = pd.Series(preds, index=demand.index).groupby(demand["candidate_id"]).sum() * (full_hours / len(weather))
    out = cands.copy()
    out["predicted_annual_demand_kwh"] = out["candidate_id"].map(annual)
    out["demand_index"] = 100 * out["predicted_annual_demand_kwh"] / out["predicted_annual_demand_kwh"].max()
    summary = {
        "training_data": f"SYNTHETIC demand history ({len(cands)} sites x 2024 hourly"
                         + (f", 1 day in {day_step}" if day_step > 1 else "") + "), Open-Meteo weather"
                         + (f", scaled to {adoption['state']}'s REAL EV share (Vahan)" if adoption else ""),
        "ev_adoption": adoption,
        "split": "temporal: first 80% train, last 20% test",
        "settings": ("GA-tuned on VIT-AP, reused without re-tuning" if params else "Random Forest defaults")
                    + (f" (fast mode: {trees} trees)" if trees and params else ""),
        "baseline_rmse": baseline_rmse,
        "ga_tuned_rf": {k: float(v) for k, v in metrics.items()},
        "ga_tuned_params": params,
    }
    return out, summary


# ───────────────────────── GA ─────────────────────────

def make_ga(scored_csv: str, settings: PlaceSettings, k: int, seed: int) -> GeneticAlgorithmOptimizer:
    random.seed(seed)
    np.random.seed(seed)
    ga = GeneticAlgorithmOptimizer(GA_CONFIG)
    fp = ga.config["fitness_params"]
    fp.update(budget_lakhs=settings.budget_lakhs, min_station_distance_m=settings.spacing_m,
              coverage_radius_m=settings.coverage_m)
    ga.config["n_stations_target"] = k
    ga.load_candidates(scored_csv)
    return ga


def best_ga(scored_csv: str, settings: PlaceSettings, k: int):
    """Best of settings.ga_runs independent GA runs for k stations."""
    runs = []
    for seed in range(settings.ga_runs):
        ga = make_ga(scored_csv, settings, k, seed)
        best, history, feasible = ga.optimize()
        runs.append((ga, best, history, feasible))
    return max(runs, key=lambda r: (r[3], r[1].fitness))


# ───────────────────────── run ─────────────────────────

def run_place(place: Optional[str] = None, boundary: Union[str, Dict, None] = None, name: Optional[str] = None,
              settings: Optional[PlaceSettings] = None, refresh: bool = False,
              progress: Optional[Callable[[str, float], None]] = None, data_root: str = "data/places",
              out_root: str = "outputs/places", export_web: bool = True) -> Dict:
    """Run the whole method for one place and write its outputs and web dashboard data.

    The area is a place name to search for, or `boundary`: a GeoJSON file path or a GeoJSON geometry
    dict (an area drawn on the website's map). progress(message, fraction) is called at each stage (the live web planner shows it). With
    export_web=False nothing is written under web/ and the dashboard data is returned as "web_data".
    """
    t0 = time.time()
    step = progress or (lambda msg, frac: None)
    settings = settings or PlaceSettings(budget_lakhs=300)
    config = get_config("settings")
    step("Finding the area on OpenStreetMap", 0.02)
    if isinstance(boundary, dict):
        label = name or "Drawn area"
        info = boundary_from_geometry(boundary, label)
    elif boundary:
        label = name or Path(boundary).stem
        info = boundary_from_file(boundary, label)
    else:
        label = name or place
        info = None
    # a drawn area's caches are keyed by its shape too, so a redrawn area never reuses old downloads
    slug = slugify(label) + (f"-{_geometry_hash(info['geometry'])}" if info else "")
    cache = Path(data_root) / slug
    out = Path(out_root) / slug
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    if info is None:
        info = geocode(place, cache / "place.json", refresh)
    area_wgs = shape(info["geometry"])
    area_km2 = gpd.GeoSeries([area_wgs], crs=WGS84_CRS).to_crs(UTM_CRS).area.iloc[0] / 1e6
    if area_km2 > settings.max_area_km2:
        raise RuntimeError(f"Area is {area_km2:.1f} km², above the {settings.max_area_km2:g} km² limit "
                           "(use --max-area to raise it, or a smaller boundary).")
    print(f"Place: {info['display_name']} [{info['osm']}, {info['category']}] · {area_km2:.2f} km²")

    step("Downloading buildings, roads and parking", 0.06)
    pad = 0.004
    min_lon, min_lat, max_lon, max_lat = area_wgs.bounds
    features = osm_to_gdf(fetch_osm_area((min_lat - pad, min_lon - pad, max_lat + pad, max_lon + pad),
                                         cache / "osm.json", refresh))
    step("Building candidate sites and checking nearby chargers", 0.15)
    cands, zones, chargers, grid_note, charger_note = build_candidates(features, area_wgs, config, settings, out, cache)
    print(f"Sites: {len(cands)} in zones {cands['zone'].value_counts().to_dict()}")
    print(f"Grid: {grid_note}")
    print(f"Chargers: {len(chargers)} within 5 km of the area ({charger_note})")

    centroid = area_wgs.centroid
    step(f"ML: generating demand history and training the Random Forest ({len(cands)} sites)", 0.22)
    weather = fetch_weather(centroid.y, centroid.x, cache / "weather_2024.csv")
    adoption = ev_adoption(state_of(centroid.y, centroid.x, cache / "state.json"))
    if adoption:
        print(f"EV adoption: {adoption['state']} {adoption['ev_share']:.2%} of new vehicles electric in "
              f"{adoption['year']} (Vahan) -> demand x{adoption['demand_scale']:.2f}")
    df_c, ml_summary = train_demand(pd.DataFrame(cands.drop(columns="geometry")), weather, config,
                                    settings.ml_day_step, settings.ml_trees, adoption)
    (out / "ml_metrics.json").write_text(json.dumps(ml_summary, indent=2), encoding="utf-8")

    step("Fuzzy: scoring every site with 60 rules", 0.45)
    scored = FuzzyEVChargingScorer().score_candidates(df_c)
    scored_csv = str(out / "candidates_scored.csv")
    scored.to_csv(scored_csv, index=False)

    # minimum budget, then the best plan for every affordable station count
    probe = make_ga(scored_csv, settings, 1, 0)
    min_budget = min_budget_table(probe, k_max=settings.max_stations)
    min_budget.to_csv(out / "min_budget.csv", index=False)
    affordable = min_budget[min_budget["possible"] & (min_budget["min_cost_lakhs"] <= settings.budget_lakhs)]
    if affordable.empty:
        floor = min_budget.loc[min_budget["possible"], "min_cost_lakhs"].min()
        raise RuntimeError(f"Budget ₹{settings.budget_lakhs:g} L is below the minimum ₹{floor:.2f} L for one station here.")
    counts = [settings.n_stations] if settings.n_stations else list(affordable["n_stations"])
    plans, best, skipped = [], None, []
    for i, k in enumerate(counts):
        if settings.deadline_s and best is not None and time.time() - t0 > 0.6 * settings.deadline_s:
            skipped = [int(c) for c in counts[i:]]  # out of time: keep the best plan found so far
            break
        step(f"GA: planning {int(k)} station{'s' if k > 1 else ''} ({i + 1} of {len(counts)})", 0.5 + 0.35 * i / len(counts))
        ga, ind, history, feasible = best_ga(scored_csv, settings, int(k))
        comps = ind.fitness_components or {}
        plans.append({"n_stations": int(k), "feasible": bool(feasible), "fitness": ind.fitness,
                      "total_cost_lakhs": comps.get("total_cost_lakhs", float("nan"))})
        if feasible and (best is None or ind.fitness > best[1].fitness):
            best = (ga, ind, history)
    pd.DataFrame(plans).to_csv(out / "plans_by_k.csv", index=False)
    if best is None:
        raise RuntimeError("No station count produced a plan that meets the budget and spacing rules.")
    ga, ind, history = best
    solution, _ = ga.get_solution_details(ind)
    solution.to_csv(out / "solution.csv", index=False)
    pd.DataFrame({"generation": range(len(history)), "best_fitness": history}).to_csv(out / "fitness_history.csv", index=False)

    step("Checking the GA against greedy, random and exhaustive search", 0.87)
    late = settings.deadline_s and time.time() - t0 > 0.75 * settings.deadline_s
    result = evaluate(ga, list(solution["candidate_id"]), cap=0 if late else settings.exhaustive_cap)
    if late:
        result["summary"]["exhaustive_skipped"] = "not enough time left on the server"
    if skipped:
        result["summary"]["station_counts_not_tried"] = skipped
    result["table"].to_csv(out / "comparison.csv", index=False)
    (out / "summary.json").write_text(json.dumps(result["summary"], indent=2), encoding="utf-8")

    # map layers for the dashboard
    feats_utm = features.to_crs(UTM_CRS)
    area_utm = gpd.GeoSeries([area_wgs], crs=WGS84_CRS).to_crs(UTM_CRS).iloc[0]
    near = feats_utm.geometry.intersects(area_utm.buffer(300))
    write_geojson(gpd.GeoDataFrame({"name": [info["name"]]}, geometry=[area_wgs], crs=WGS84_CRS), out / "boundary.geojson")
    bld = feats_utm[near & feats_utm["building"].notna() & feats_utm.geom_type.isin(["Polygon", "MultiPolygon"])]
    write_geojson(bld[["osm_id", "name", "building", "amenity", "geometry"]].to_crs(WGS84_CRS), out / "buildings.geojson")
    rd = feats_utm[near & feats_utm["highway"].notna() & (feats_utm.geom_type == "LineString")].copy()
    rd["length_m"] = rd.geometry.length.round(1)
    rd["ref"], rd["surface"] = None, None
    write_geojson(rd[["osm_id", "highway", "name", "ref", "surface", "length_m", "geometry"]].to_crs(WGS84_CRS), out / "roads.geojson")
    pk = feats_utm[near & (feats_utm["amenity"] == "parking") & feats_utm.geom_type.isin(["Polygon", "MultiPolygon"])]
    write_geojson(pk[["osm_id", "name", "parking", "geometry"]].to_crs(WGS84_CRS), out / "parking.geojson")
    write_geojson(chargers, out / "chargers.geojson")

    short = name or (place if place and len(place) <= 40 else info["name"])
    meta = {"slug": slug, "name": info["name"], "short": short, "display_name": info["display_name"],
            "osm": info["osm"], "area_km2": area_km2, "chargers_scope": "within 5 km of the area",
            "charger_note": charger_note, "grid_note": grid_note, "settings": asdict(settings)}
    (out / "place.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    step("Preparing the dashboard", 0.97)
    from src.visualization.web_export import build_place_web_data, place_web_data
    web_data = build_place_web_data(out, meta) if export_web else place_web_data(out, meta)

    chosen = int(len(solution))
    print(f"\n=== Minimum budget ===\n{min_budget[['n_stations', 'possible', 'min_cost_lakhs']].round(2).to_string(index=False)}")
    print(f"\n=== Plans for a ₹{settings.budget_lakhs:g} L budget ===\n{pd.DataFrame(plans).round(3).to_string(index=False)}")
    print(f"\nChosen: {chosen} stations, ₹{solution['total_capex_inr_lakhs'].sum():.2f} L")
    print(solution[["ga_rank", "candidate_id", "name", "zone", "total_capex_inr_lakhs"]].to_string(index=False))
    s = result["summary"]
    print(f"GA matches the exhaustive optimum: {s.get('ga_matches_optimum', 'not checked: ' + s.get('exhaustive_skipped', ''))}")
    print(f"Done in {time.time() - t0:.0f} s · outputs in {out}" + (f" · dashboard: web/index.html?place={slug}" if export_web else ""))
    step("Done", 1.0)
    res = {"slug": slug, "summary": s, "solution": solution, "min_budget": min_budget, "plans": plans}
    if not export_web:
        res["web_data"] = web_data
    return res
