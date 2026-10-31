"""
Minimal Streamlit Dashboard for EV Charging Demo
Reads existing demo output files — no ML/Fuzzy/GA re-runs.
Phase 2: OSM roads + manually digitized zone polygons as toggleable map layers.
"""
import streamlit as st
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
import plotly.express as px
import json
import os
from math import radians, sin, cos, sqrt, asin

def haversine(lat1, lon1, lat2, lon2):
    """Calculate great-circle distance between two points in meters."""
    R = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    return 2*R*asin(sqrt(a))

st.set_page_config(
    page_title="EV Charging Demo — VIT-AP Campus",
    page_icon="⚡",
    layout="wide"
)

# ─── LOAD DATA ───
# Cached per file modification time, so rerunning the pipeline shows up on refresh
def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else None


@st.cache_data
def _read_csv(path, mtime):
    return pd.read_csv(path)


@st.cache_data
def _read_geojson(path, mtime):
    if mtime is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_csv(path):
    return _read_csv(path, _mtime(path))


def load_geojson(path):
    return _read_geojson(path, _mtime(path))


def load_all_candidates():
    return load_csv("data/processed/candidates_demo_fuzzy_scored.csv")


def load_ga_solution():
    return load_csv("data/processed/ga_solution_demo.csv")


def load_roads_geojson():
    return load_geojson("data/raw/osm/roads.geojson")


def load_zone_polygons():
    return load_geojson("data/external/vitap_zone_polygons.geojson")


def load_osm_layer(name):
    """Real OpenStreetMap campus layer exported by the campus pipeline."""
    return load_geojson(f"data/raw/osm/{name}.geojson")

df_all = load_all_candidates()
df_ga = load_ga_solution()
roads_geojson = load_roads_geojson()
zone_geojson = load_zone_polygons()
boundary_geojson = load_osm_layer("campus_boundary")
buildings_geojson = load_osm_layer("buildings")
parking_geojson = load_osm_layer("parking")
chargers_geojson = load_geojson("data/raw/openchargemap/stations.geojson")


def fuzzy_text(score):
    return "not scored (no rule fired)" if pd.isna(score) else f"{score:.3f}"

# Identify GA-selected candidate IDs
ga_selected_ids = set(df_ga["candidate_id"].tolist()) if len(df_ga) > 0 else set()
df_all["ga_selected"] = df_all["candidate_id"].isin(ga_selected_ids)

# ─── KPI CARDS ───
st.subheader("📊 Key Metrics")
col1, col2, col3, col4 = st.columns(4)

n_total = len(df_all)
n_selected = int(df_all["ga_selected"].sum())
total_capex = df_all.loc[df_all["ga_selected"], "total_capex_inr_lakhs"].sum()
budget = 300.0
remaining = budget - total_capex

col1.metric("Total Candidates", f"{n_total}")
col2.metric("Stations Selected", f"{n_selected}")
col3.metric("Total CAPEX (Selected)", f"₹{total_capex:.1f} L")
existing = (chargers_geojson or {}).get("features", [])
if existing:
    dists = [f["properties"]["distance_to_campus_km"] for f in existing]
    nearest = min(existing, key=lambda f: f["properties"]["distance_to_campus_km"])["properties"]
    bands = ", ".join(f"{sum(d <= km for d in dists)} within {km} km" for km in (50, 100, 300))
    st.caption(f"Existing public chargers (OpenChargeMap): {len(existing):,} across India ({bands}). "
               f"The nearest is {nearest['distance_to_campus_km']:.1f} km away ({nearest['name']}, {nearest['town']}). "
               "Zoom out on the map to see them all.")
col4.metric("Remaining Budget", f"₹{remaining:.1f} L", delta=f"{remaining:.1f} L", delta_color="normal" if remaining >= 0 else "inverse")

# ─── MAP ───
st.subheader("🗺️ Campus Map — VIT-AP")

# Create base map centered on candidates
center_lat = df_all["latitude"].mean()
center_lon = df_all["longitude"].mean()
m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles=None)

# Base maps (free, no API key) — switch between them in the layer control
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    name="Satellite (Esri)",
    max_zoom=19,
).add_to(m)
# Esri only: CARTO now requires an API key, and OSM's tile servers block pages opened from disk
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, OpenStreetMap contributors, and the GIS User Community",
    name="Street Map (Esri)", max_zoom=19,
).add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attr="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors",
    name="Light Map (Esri)", max_zoom=16,
).add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    attr="Labels &copy; Esri",
    name="Place Labels",
    overlay=True,
    control=True,
    max_zoom=19,
).add_to(m)

# ─── COMPUTE CAMPUS BOUNDS FROM VERIFIED DATA ONLY ───
# Use candidates + zone polygons (verified campus area)
# DO NOT include OSM roads — they cover entire Amaravati/Guntur region
campus_lats = []
campus_lons = []
campus_lats.extend(df_all["latitude"].tolist())
campus_lons.extend(df_all["longitude"].tolist())
# Include the OSM campus boundary so the whole campus is always in view
if boundary_geojson and boundary_geojson.get("features"):
    for ring_lon, ring_lat in boundary_geojson["features"][0]["geometry"]["coordinates"][0]:
        campus_lons.append(ring_lon)
        campus_lats.append(ring_lat)
if zone_geojson and zone_geojson.get("features"):
    for feat in zone_geojson["features"]:
        geom = feat["geometry"]
        if geom["type"] == "Polygon":
            for coord in geom["coordinates"][0]:
                campus_lons.append(coord[0])
                campus_lats.append(coord[1])

# Campus bounding box (verified overlap of candidates + zones)
if campus_lats and campus_lons:
    campus_min_lat, campus_max_lat = min(campus_lats), max(campus_lats)
    campus_min_lon, campus_max_lon = min(campus_lons), max(campus_lons)
    campus_bounds = {
        "min_lat": campus_min_lat, "max_lat": campus_max_lat,
        "min_lon": campus_min_lon, "max_lon": campus_max_lon
    }
    # Fit map to campus bounds with small padding
    sw = [campus_min_lat - 0.001, campus_min_lon - 0.001]
    ne = [campus_max_lat + 0.001, campus_max_lon + 0.001]
    m.fit_bounds([sw, ne])
else:
    # Fallback to candidates center if no zones
    m.location = [center_lat, center_lon]
    m.zoom_start = 16
    campus_bounds = None

# Layer control will be added at the end
# We'll create FeatureGroups for each layer

# 1. OSM Roads Layer — filtered to campus bounds for display
roads_fg = folium.FeatureGroup(name="OSM Roads (Real, Campus Area)", show=True, overlay=True, control=True)
if roads_geojson and roads_geojson.get("features") and campus_bounds:
    # Campus bounds already computed above
    campus_bounds = {
        "min_lat": campus_min_lat, "max_lat": campus_max_lat,
        "min_lon": campus_min_lon, "max_lon": campus_max_lon
    }

    def roads_style(feature):
        highway = feature["properties"].get("highway", "")
        if highway in ["motorway", "trunk", "primary"]:
            return {"color": "#d73027", "weight": 3, "opacity": 0.8}
        elif highway in ["secondary", "tertiary"]:
            return {"color": "#fc8d59", "weight": 2.5, "opacity": 0.7}
        elif highway in ["residential", "unclassified", "service"]:
            return {"color": "#fee08b", "weight": 1.5, "opacity": 0.6}
        else:
            return {"color": "#999999", "weight": 1.5, "opacity": 0.5}

    def roads_highlight(feature):
        return {"color": "#ffff00", "weight": 4, "opacity": 1.0}

    def road_in_campus(feature):
        """Filter: keep only LineString segments that intersect campus bounds."""
        geom = feature["geometry"]
        if geom["type"] != "LineString":
            return False
        for coord in geom["coordinates"]:
            lon, lat = coord[0], coord[1]
            if (campus_bounds["min_lon"] <= lon <= campus_bounds["max_lon"] and
                campus_bounds["min_lat"] <= lat <= campus_bounds["max_lat"]):
                return True
        return False

    # Filter features to campus area
    campus_roads = {
        "type": "FeatureCollection",
        "features": [f for f in roads_geojson["features"] if road_in_campus(f)]
    }

    if campus_roads["features"]:
        folium.GeoJson(
            campus_roads,
            name="OSM Roads (Campus)",
            style_function=roads_style,
            highlight_function=roads_highlight,
            tooltip=folium.GeoJsonTooltip(
                fields=["highway", "name", "ref", "surface", "length_m"],
                aliases=["Type:", "Name:", "Ref:", "Surface:", "Length (m):"],
                localize=True,
                sticky=False
            )
        ).add_to(roads_fg)
        st.caption(f"Showing {len(campus_roads['features'])} of {len(roads_geojson['features'])} OSM road features within campus bounds.")
    else:
        st.warning("No OSM roads found within campus bounds.")
elif roads_geojson and roads_geojson.get("features"):
    # Fallback: show all roads if campus bounds not yet computed
    def roads_style(feature):
        highway = feature["properties"].get("highway", "")
        if highway in ["motorway", "trunk", "primary"]:
            return {"color": "#d73027", "weight": 3, "opacity": 0.8}
        elif highway in ["secondary", "tertiary"]:
            return {"color": "#fc8d59", "weight": 2.5, "opacity": 0.7}
        elif highway in ["residential", "unclassified", "service"]:
            return {"color": "#fee08b", "weight": 1.5, "opacity": 0.6}
        else:
            return {"color": "#999999", "weight": 1.5, "opacity": 0.5}

    def roads_highlight(feature):
        return {"color": "#ffff00", "weight": 4, "opacity": 1.0}

    folium.GeoJson(
        roads_geojson,
        name="OSM Roads (All)",
        style_function=roads_style,
        highlight_function=roads_highlight,
        tooltip=folium.GeoJsonTooltip(
            fields=["highway", "name", "ref", "surface", "length_m"],
            aliases=["Type:", "Name:", "Ref:", "Surface:", "Length (m):"],
            localize=True,
            sticky=False
        )
    ).add_to(roads_fg)
    st.info("Displaying all OSM roads (campus bounds not available for filtering).")

roads_fg.add_to(m)

# 1b. Real OSM campus layers
boundary_fg = folium.FeatureGroup(name="Campus Boundary (OSM)", show=True, overlay=True, control=True)
if boundary_geojson and boundary_geojson.get("features"):
    folium.GeoJson(
        boundary_geojson,
        style_function=lambda f: {"color": "#ffffff", "weight": 3, "fillOpacity": 0, "dashArray": "6 4"},
        tooltip=folium.GeoJsonTooltip(fields=["name"], aliases=["Campus:"]),
    ).add_to(boundary_fg)
boundary_fg.add_to(m)

buildings_fg = folium.FeatureGroup(name="Buildings (OSM)", show=True, overlay=True, control=True)
if buildings_geojson and buildings_geojson.get("features"):
    folium.GeoJson(
        buildings_geojson,
        style_function=lambda f: {"color": "#f2a541", "weight": 1, "fillColor": "#f2a541", "fillOpacity": 0.35},
        tooltip=folium.GeoJsonTooltip(fields=["name", "building"], aliases=["Name:", "Type:"]),
    ).add_to(buildings_fg)
buildings_fg.add_to(m)

parking_fg = folium.FeatureGroup(name="Parking Lots (OSM)", show=True, overlay=True, control=True)
if parking_geojson and parking_geojson.get("features"):
    folium.GeoJson(
        parking_geojson,
        style_function=lambda f: {"color": "#4fd1b5", "weight": 2, "fillColor": "#4fd1b5", "fillOpacity": 0.5},
        tooltip=folium.GeoJsonTooltip(fields=["osm_id"], aliases=["OSM parking:"]),
    ).add_to(parking_fg)
parking_fg.add_to(m)

chargers_fg = MarkerCluster(name=f"Existing Public Chargers, India (OpenChargeMap, {len(existing):,})",
                            show=True, overlay=True, control=True)
for f in existing:
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(f"<b>{p['name']}</b><br>{p['town'] or ''}{', ' + p['state'] if p.get('state') else ''}<br>{p['distance_to_campus_km']:.1f} km from campus<br>"
                           f"Points: {p['num_connectors']} | Status: {p['status'] or 'unknown'}<br>Source: OpenChargeMap", max_width=260),
        tooltip=f"🔌 {p['name']} ({p['distance_to_campus_km']:.1f} km)",
        icon=folium.Icon(color="purple", icon="plug", prefix="fa"),
    ).add_to(chargers_fg)
chargers_fg.add_to(m)

# 2. Zone Polygons Layer
zones_fg = folium.FeatureGroup(name="Campus Zones (from OSM buildings)", show=True, overlay=True, control=True)
if zone_geojson and zone_geojson.get("features"):
    # Color map for zones
    zone_colors = {
        "academic": "#1f77b4",
        "hostel_boys": "#ff7f0e",
        "hostel_girls": "#e377c2",
        "staff_quarters": "#2ca02c",
        "admin": "#d62728",
        "commercial": "#9467bd",
        "sports": "#8c564b",
        "gate_north": "#e377c2",
        "gate_south": "#7f7f7f",
        "gate_main": "#bcbd22",
        "gate": "#bcbd22",
        "campus_general": "#7f7f7f",
    }

    def zones_style(feature):
        zone_id = feature["properties"].get("zone_id", "")
        return {
            "fillColor": zone_colors.get(zone_id, "#999999"),
            "color": zone_colors.get(zone_id, "#999999"),
            "weight": 2,
            "fillOpacity": 0.15,
            "opacity": 0.8
        }

    def zones_highlight(feature):
        return {"weight": 3, "fillOpacity": 0.3, "opacity": 1.0}

    folium.GeoJson(
        zone_geojson,
        name="Campus Zones",
        style_function=zones_style,
        highlight_function=zones_highlight,
        tooltip=folium.GeoJsonTooltip(
            fields=["zone_name", "zone_id", "land_cost_tier", "primary_use"],
            aliases=["Zone:", "ID:", "Land Tier:", "Primary Use:"],
            localize=True,
            sticky=False
        )
    ).add_to(zones_fg)
else:
    st.warning("Zone polygons not available or empty.")

zones_fg.add_to(m)

# 3. Candidate Markers Layer — all 25, with GA-selected highlighted in green
candidates_fg = folium.FeatureGroup(name=f"Candidate Sites ({len(df_all)})", show=True, overlay=True, control=True)
for _, row in df_all.iterrows():
    is_selected = row["ga_selected"]
    color = "green" if is_selected else "blue"  # folium.Icon only accepts named colours
    icon_name = "bolt" if is_selected else "circle"
    prefix = "fa"

    popup_html = f"""
    <b>{row['name']}</b><br>
    Candidate ID: {row['candidate_id']}<br>
    Zone: {row['zone']}<br>
    {'✅ <b>GA SELECTED</b><br>' if is_selected else ''}
    Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
    Fuzzy Suitability: {fuzzy_text(row['fuzzy_suitability_score'])}<br>
    Total CAPEX: ₹{row['total_capex_inr_lakhs']:.2f} L<br>
    Land: ₹{row['land_cost_inr_lakhs']:.2f} L | Grid: ₹{row['grid_connection_cost_inr_lakhs']:.2f} L | Construction: ₹{row['construction_cost_inr_lakhs']:.2f} L
    """
    folium.Marker(
        location=[row["latitude"], row["longitude"]],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=f"{row['candidate_id']}: {row['name']} {'✅' if is_selected else ''}",
        icon=folium.Icon(color=color, icon=icon_name, prefix=prefix)
    ).add_to(candidates_fg)

candidates_fg.add_to(m)

# 4. GA-Selected Only Layer (optional toggle — shows ONLY the 2 selected)
# Note: When both "Proposed Candidates (25)" and this layer are ON, the 2 selected
# sites appear twice. Use this layer alone for a clean view of just the GA-selected sites.
ga_selected_fg = folium.FeatureGroup(name=f"GA-Selected Only ({len(df_ga)})", show=False, overlay=True, control=True)
for _, row in df_ga.iterrows():
    popup_html = f"""
    <b>{row['name']} ✅ GA SELECTED</b><br>
    Candidate ID: {row['candidate_id']}<br>
    Zone: {row['zone']}<br>
    Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
    Fuzzy Suitability: {fuzzy_text(row['fuzzy_suitability_score'])}<br>
    Total CAPEX: ₹{row['total_capex_inr_lakhs']:.2f} L<br>
    Land: ₹{row['land_cost_inr_lakhs']:.2f} L | Grid: ₹{row['grid_connection_cost_inr_lakhs']:.2f} L | Construction: ₹{row['construction_cost_inr_lakhs']:.2f} L
    """
    folium.Marker(
        location=[row["latitude"], row["longitude"]],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=f"{row['candidate_id']}: {row['name']} ✅",
        icon=folium.Icon(color="green", icon="bolt", prefix="fa")
    ).add_to(ga_selected_fg)

ga_selected_fg.add_to(m)

# 4b. Top-5 by Fuzzy Suitability (computed from the data) — NOT GA-selected
illustrative_top5_ids = set(df_all.nlargest(5, "fuzzy_suitability_score")["candidate_id"])
illustrative_fg = folium.FeatureGroup(
    name="Top-5 by Fuzzy Suitability",
    show=False, overlay=True, control=True
)
for _, row in df_all.iterrows():
    if row["candidate_id"] in illustrative_top5_ids:
        popup_html = f"""
        <b>{row['name']}</b><br>
        Candidate ID: {row['candidate_id']}<br>
        Zone: {row['zone']}<br>
        <b>TOP-5 BY FUZZY SUITABILITY</b><br>
        <span style="color:#1e7e34;">NOT GA-selected — for demonstration only</span><br>
        Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
        Fuzzy Suitability: {fuzzy_text(row['fuzzy_suitability_score'])}<br>
        Total CAPEX: ₹{row['total_capex_inr_lakhs']:.2f} L<br>
        Land: ₹{row['land_cost_inr_lakhs']:.2f} L | Grid: ₹{row['grid_connection_cost_inr_lakhs']:.2f} L | Construction: ₹{row['construction_cost_inr_lakhs']:.2f} L
        """
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{row['candidate_id']}: {row['name']} ⭐ Top-5 fuzzy",
            icon=folium.Icon(color="green", icon="bolt", prefix="fa")
        ).add_to(illustrative_fg)

illustrative_fg.add_to(m)


# Layer control
folium.LayerControl(collapsed=False).add_to(m)

# Custom legend (HTML) — force dark text on all elements, no FontAwesome
legend_html = """
<div style="
    position: fixed;
    bottom: 50px; left: 50px;
    width: 300px;
    background-color: #ffffff;
    border: 2px solid #333333;
    z-index: 10000;
    font-size: 12px;
    line-height: 1.5;
    padding: 14px 16px;
    border-radius: 6px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    user-select: none;
    -webkit-user-select: none;
    -moz-user-select: none;
    -ms-user-select: none;
    color: #111111;
">
    <div style="font-weight: 700; font-size: 13px; margin-bottom: 8px; color: #111111;">Map Legend</div>
    <div style="border-bottom: 1px solid #ddd; margin: 6px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Roads (OSM, Real)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#d73027; font-weight: bold;">━━</span> Primary / Trunk</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#fc8d59; font-weight: bold;">━━</span> Secondary / Tertiary</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#fee08b; font-weight: bold;">━━</span> Residential / Service</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#999999; font-weight: bold;">━━</span> Other / Unclassified</div>
    <div style="border-bottom: 1px solid #ddd; margin: 8px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Real Campus Data (OSM)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#ffffff; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Campus boundary (OSM, dashed)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#f2a541; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Buildings (OSM)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#4fd1b5; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Parking lots (OSM)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#8e44ad; font-size:14px; margin-right:6px; vertical-align:middle;">●</span> Existing public chargers, India (OpenChargeMap; clustered, zoom out)</div>
    <div style="border-bottom: 1px solid #ddd; margin: 8px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Zones (from OSM buildings)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#1f77b4; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Academic</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#ff7f0e; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Men's Hostels</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#e377c2; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Ladies' Hostels</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#9467bd; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Commercial</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#8c564b; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Sports</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#bcbd22; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Entry Points</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#7f7f7f; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Rest of Campus</div>
    <div style="border-bottom: 1px solid #ddd; margin: 8px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Candidates</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1e7e34; font-size:14px; margin-right:6px; vertical-align:middle;">⚡</span> GA-Selected (__N_SEL__)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1f77b4; font-size:12px; margin-right:6px; vertical-align:middle;">●</span> Candidate sites (__N_ALL__)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1e7e34; font-size:14px; margin-right:6px; vertical-align:middle;">⚡</span> Top-5 by Fuzzy Suitability</div>
    <div style="margin-top: 6px; color: #555555;">Hover a building for its OSM name</div>
</div>
"""
legend_html = legend_html.replace("__N_SEL__", str(len(df_ga))).replace("__N_ALL__", str(len(df_all)))
m.get_root().html.add_child(folium.Element(legend_html))

# Render map
st_folium(m, width='stretch', height=650, returned_objects=[])

# ─── RANKING TABLE ───
st.subheader("📈 All Candidates Ranked by Predicted Annual Demand")

df_ranked = df_all.sort_values("predicted_annual_demand_kwh", ascending=False).reset_index(drop=True)
df_ranked["rank"] = range(1, len(df_ranked) + 1)

display_cols = [
    "rank", "candidate_id", "name", "zone", "latitude", "longitude",
    "predicted_annual_demand_kwh", "fuzzy_suitability_score",
    "total_capex_inr_lakhs", "land_cost_inr_lakhs",
    "grid_connection_cost_inr_lakhs", "construction_cost_inr_lakhs",
    "ga_selected"
]

rank_df = df_ranked[display_cols].copy()
rank_df.columns = [
    "Rank", "Candidate ID", "Name", "Zone", "Latitude", "Longitude",
    "Annual Demand (kWh)", "Fuzzy Suitability",
    "Total CAPEX (₹L)", "Land (₹L)", "Grid (₹L)", "Construction (₹L)",
    "GA Selected"
]

st.dataframe(
    rank_df.style.format({
        "Latitude": "{:.6f}",
        "Longitude": "{:.6f}",
        "Annual Demand (kWh)": "{:,.0f}",
        "Fuzzy Suitability": "{:.3f}",
        "Total CAPEX (₹L)": "₹{:.2f} L",
        "Land (₹L)": "₹{:.2f} L",
        "Grid (₹L)": "₹{:.2f} L",
        "Construction (₹L)": "₹{:.2f} L",
    }).apply(
        lambda x: ["background-color: #d4edda" if v else "" for v in x],
        subset=["GA Selected"]
    ),
    width="stretch",
    height=500
)

# ─── SUITABILITY SCORES CHART ───
st.subheader("🎯 Fuzzy Suitability Scores (All Candidates)")

suit_cols = ["candidate_id", "name", "zone", "fuzzy_suitability_score", "ga_selected"]
suit_df = df_all[suit_cols].copy()
suit_df.columns = ["Candidate ID", "Name", "Zone", "Suitability (0–1)", "GA Selected"]

fig = px.bar(
    suit_df.sort_values("Suitability (0–1)", ascending=False),
    x="Candidate ID",
    y="Suitability (0–1)",
    color="Zone",
    pattern_shape="GA Selected",
    pattern_shape_sequence=["", "/"],
    text="Suitability (0–1)",
    hover_data=["Name", "Zone", "GA Selected"]
)
fig.update_traces(texttemplate="%{text:.3f}", textposition="outside")
fig.update_layout(yaxis_range=[0, max(suit_df["Suitability (0–1)"]) * 1.2], showlegend=True)
st.plotly_chart(fig, width="stretch")

# ─── GA-SELECTED COST BREAKDOWN ───
if n_selected > 0:
    st.subheader("💰 GA-Selected Stations Cost Breakdown")

    cost_cols = ["candidate_id", "name", "zone", "land_cost_inr_lakhs",
                 "grid_connection_cost_inr_lakhs", "construction_cost_inr_lakhs", "total_capex_inr_lakhs"]
    cost_df = df_ga[cost_cols].copy()
    cost_df.columns = ["Candidate ID", "Name", "Zone", "Land (₹L)", "Grid (₹L)", "Construction (₹L)", "Total CAPEX (₹L)"]
    cost_df = cost_df.set_index("Candidate ID")
    st.dataframe(cost_df.style.format({
        "Land (₹L)": "₹{:.2f} L",
        "Grid (₹L)": "₹{:.2f} L",
        "Construction (₹L)": "₹{:.2f} L",
        "Total CAPEX (₹L)": "₹{:.2f} L"
    }))

# ─── EVALUATION: GA VS BASELINES ───
comparison_path = "outputs/evaluation/comparison.csv"
summary_path = "outputs/evaluation/summary.json"
if os.path.exists(comparison_path) and os.path.exists(summary_path):
    st.subheader("🏁 How Good Is the GA Plan?")
    with open(summary_path, encoding="utf-8") as f:
        eval_summary = json.load(f)
    comparison = pd.read_csv(comparison_path)
    ecol1, ecol2, ecol3 = st.columns(3)
    ecol1.metric("Possible plans", f"{eval_summary['n_combinations']:,}")
    ecol2.metric("Plans meeting all rules", f"{eval_summary.get('n_valid_plans', 0):,}")
    ecol3.metric("GA matches true optimum", "Yes" if eval_summary.get("ga_matches_optimum") else "No")
    st.caption("Every method picks the same number of stations under the same budget and spacing rules, "
               "scored with the GA's fitness function. The exhaustive search checks every possible plan.")
    fig_eval = px.bar(
        comparison.dropna(subset=["fitness"]).sort_values("fitness"),
        x="fitness", y="method", orientation="h", text="fitness",
        labels={"fitness": "Fitness (higher is better)", "method": ""},
        color_discrete_sequence=["#127466"],
    )
    fig_eval.update_traces(texttemplate="%{text:.3f}", textposition="outside")
    fig_eval.update_layout(xaxis_range=[0, comparison["fitness"].max() * 1.15], height=360)
    st.plotly_chart(fig_eval, width="stretch")

ml_metrics_path = "outputs/ml_results/metrics.json"
if os.path.exists(ml_metrics_path):
    st.subheader("🤖 Demand Model Accuracy (GA-tuned Random Forest)")
    with open(ml_metrics_path, encoding="utf-8") as f:
        ml_metrics = json.load(f)
    mcol1, mcol2, mcol3 = st.columns(3)
    mcol1.metric("Simple average — error", f"{ml_metrics['baseline_rmse']:.3f} kWh/h")
    mcol2.metric("Default Random Forest — error", f"{ml_metrics['default_rf']['test_rmse']:.3f} kWh/h")
    mcol3.metric("GA-tuned Random Forest — error", f"{ml_metrics['ga_tuned_rf']['test_rmse']:.3f} kWh/h",
                 delta=f"{ml_metrics['ga_tuned_rf']['test_rmse'] - ml_metrics['baseline_rmse']:.3f} vs average",
                 delta_color="inverse")
    st.caption(f"Tested on the last 20% of 2024, which the model never saw. Trained on synthetic demand history "
               f"(R² {ml_metrics['ga_tuned_rf']['test_r2']:.3f}), so this measures the method, not real-world accuracy.")

# ─── RAW DATA (optional) ───
with st.expander(f"📋 Full Data (All {len(df_all)} Candidates)"):
    display_cols = [
        "candidate_id", "name", "zone", "latitude", "longitude",
        "predicted_annual_demand_kwh", "fuzzy_suitability_score",
        "total_capex_inr_lakhs", "land_cost_inr_lakhs",
        "grid_connection_cost_inr_lakhs", "construction_cost_inr_lakhs",
        "demand_prediction_source", "ga_selected"
    ]
    st.dataframe(df_all[display_cols])

st.caption("Data source: data/processed/candidates_demo_fuzzy_scored.csv and ga_solution_demo.csv, generated by python run_pipeline.py all")