"""
Static campus map (single HTML file) for sharing results without the dashboard.

Layers: satellite and street base maps, the OSM campus boundary and buildings,
OSM-derived zones, all candidate sites and the GA-selected stations.
"""

import json
from pathlib import Path

import folium
from folium.plugins import MarkerCluster
import pandas as pd

ZONE_COLORS = {
    "academic": "#1f77b4",
    "hostel_boys": "#ff7f0e",
    "hostel_girls": "#e377c2",
    "commercial": "#9467bd",
    "sports": "#8c564b",
    "gate": "#bcbd22",
    "campus_general": "#7f7f7f",
}


def _geojson(path: str):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def build_campus_map(scored_csv: str, solution_csv: str, out_html: str = "outputs/maps/campus_map.html") -> str:
    """Write the campus map and return its path."""
    sites = pd.read_csv(scored_csv)
    solution = pd.read_csv(solution_csv)
    selected = set(solution["candidate_id"]) if len(solution) else set()

    m = folium.Map(location=[sites["latitude"].mean(), sites["longitude"].mean()], zoom_start=16, tiles=None,
        minZoom=4, max_bounds=True, min_lat=5.5, max_lat=37.5, min_lon=67.5, max_lon=98.0,
        max_bounds_viscosity=1.0,  # stop at India: no repeated world maps when zooming out
    )
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        name="Satellite (Esri)", max_zoom=19, no_wrap=True,
    ).add_to(m)
    # Esri only: CARTO now requires an API key, and OSM's tile servers block pages opened from disk
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, OpenStreetMap contributors, and the GIS User Community",
        name="Street Map (Esri)", max_zoom=19, no_wrap=True,
    ).add_to(m)

    boundary = _geojson("data/raw/osm/campus_boundary.geojson")
    if boundary:
        folium.GeoJson(boundary, name="Campus Boundary (OSM)",
                       style_function=lambda f: {"color": "#ffffff", "weight": 3, "fillOpacity": 0, "dashArray": "6 4"}).add_to(m)
    buildings = _geojson("data/raw/osm/buildings.geojson")
    if buildings:
        folium.GeoJson(buildings, name="Buildings (OSM)",
                       style_function=lambda f: {"color": "#f2a541", "weight": 1, "fillColor": "#f2a541", "fillOpacity": 0.35},
                       tooltip=folium.GeoJsonTooltip(fields=["name"], aliases=["Building:"])).add_to(m)
    zones = _geojson("data/external/vitap_zone_polygons.geojson")
    if zones:
        folium.GeoJson(zones, name="Zones (from OSM buildings)",
                       style_function=lambda f: {"color": ZONE_COLORS.get(f["properties"]["zone_id"], "#999999"),
                                                 "fillColor": ZONE_COLORS.get(f["properties"]["zone_id"], "#999999"),
                                                 "weight": 2, "fillOpacity": 0.15},
                       tooltip=folium.GeoJsonTooltip(fields=["zone_name"], aliases=["Zone:"])).add_to(m)

    chargers = _geojson("data/raw/openchargemap/stations.geojson")
    state = _geojson("data/external/andhra_pradesh_boundary.geojson")
    if state:
        folium.GeoJson(state, name="Andhra Pradesh Boundary (OSM)",
                       style_function=lambda f: {"color": "#8e44ad", "weight": 2, "fillOpacity": 0, "dashArray": "8 6"}).add_to(m)
    existing = MarkerCluster(name=f"Existing Public Chargers, Andhra Pradesh (OpenChargeMap, {len((chargers or {}).get('features', []))})")
    for f in (chargers or {}).get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        folium.Marker([lat, lon], tooltip=f"{p['name']} ({p['distance_to_campus_km']:.1f} km from campus)",
                      icon=folium.Icon(color="purple", icon="plug", prefix="fa")).add_to(existing)
    existing.add_to(m)

    candidates = folium.FeatureGroup(name=f"Candidate Sites ({len(sites)})")
    stations = folium.FeatureGroup(name=f"GA-Selected Stations ({len(selected)})")
    for _, r in sites.iterrows():
        score = "not scored" if pd.isna(r["fuzzy_suitability_score"]) else f"{r['fuzzy_suitability_score']:.3f}"
        popup = (f"<b>{r['name']}</b><br>{r['candidate_id']} · {r['zone']}<br>"
                 f"Fuzzy suitability: {score}<br>Predicted demand: {r.get('predicted_annual_demand_kwh', 0):,.0f} kWh/yr<br>"
                 f"CAPEX: ₹{r['total_capex_inr_lakhs']:.1f} L")
        is_sel = r["candidate_id"] in selected
        folium.Marker(
            [r["latitude"], r["longitude"]],
            popup=folium.Popup(popup, max_width=280),
            tooltip=f"{r['candidate_id']}: {r['name']}" + (" (GA selected)" if is_sel else ""),
            icon=folium.Icon(color="green" if is_sel else "blue", icon="bolt" if is_sel else "circle", prefix="fa"),
        ).add_to(stations if is_sel else candidates)
    candidates.add_to(m)
    stations.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    out = Path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out))
    print(f"Saved campus map to {out}")
    return str(out)
