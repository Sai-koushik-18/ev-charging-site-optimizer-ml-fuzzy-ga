"""
Minimal Streamlit Dashboard for EV Charging Demo
Reads existing demo output files — no ML/Fuzzy/GA re-runs.
Phase 2: OSM roads + manually digitized zone polygons as toggleable map layers.
"""
import streamlit as st
import pandas as pd
import folium
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
@st.cache_data
def load_all_candidates():
    return pd.read_csv("data/processed/candidates_demo_fuzzy_scored.csv")

@st.cache_data
def load_ga_solution():
    return pd.read_csv("data/processed/ga_solution_demo.csv")

@st.cache_data
def load_roads_geojson():
    path = "data/raw/osm/roads.geojson"
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"Could not load roads.geojson: {e}")
        return None

@st.cache_data
def load_zone_polygons():
    path = "data/external/vitap_zone_polygons.geojson"
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"Could not load zone polygons: {e}")
        return None

df_all = load_all_candidates()
df_ga = load_ga_solution()
roads_geojson = load_roads_geojson()
zone_geojson = load_zone_polygons()

# ─── GOOGLE MAPS REFERENCE LANDMARKS ───
# Verified actual campus landmarks from Google Maps (user-supplied)
# These are NOT derived from project data — they are independent ground truth
GM_LANDMARKS = [
    {"name": "Central Block", "lat": 16.495039, "lon": 80.499163},
    {"name": "Library", "lat": 16.495696, "lon": 80.500117},
    {"name": "AB2", "lat": 16.495526, "lon": 80.498143},
    {"name": "MH-3", "lat": 16.492502, "lon": 80.501472},
    {"name": "MH-4", "lat": 16.492120, "lon": 80.500710},
    {"name": "LH3", "lat": 16.492581, "lon": 80.497547},
    {"name": "LH-2", "lat": 16.491820, "lon": 80.497651},
    {"name": "Stupa", "lat": 16.497080, "lon": 80.499219},
]

# ─── ILLUSTRATIVE CAMPUS PLACEMENTS (Not Surveyed) ───
# Plausible locations NEAR actual Google Maps campus landmarks.
# NOT derived from project data. NOT verified charging sites.
ILLUSTRATIVE_CAMPUS_PLACEMENTS = [
    {
        "name": "Central Block / Library Parking (Illustrative)",
        "lat": 16.495300,
        "lon": 80.499500,
        "description": "Plausible surface parking between Central Block and Library. NOT surveyed."
    },
    {
        "name": "AB2 Academic Zone Parking (Illustrative)",
        "lat": 16.495600,
        "lon": 80.497800,
        "description": "Plausible parking west of AB2 academic building. NOT surveyed."
    },
]

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
col4.metric("Remaining Budget", f"₹{remaining:.1f} L", delta=f"{remaining:.1f} L", delta_color="normal" if remaining >= 0 else "inverse")

# ─── MAP ───
st.subheader("🗺️ Campus Map — VIT-AP (Demo — Coordinates Provisional)")
st.caption("Demo coordinates and zones are provisional; campus alignment is unverified.")

# Create base map centered on candidates
center_lat = df_all["latitude"].mean()
center_lon = df_all["longitude"].mean()
m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles="OpenStreetMap")

# ─── COMPUTE CAMPUS BOUNDS FROM VERIFIED DATA ONLY ───
# Use candidates + zone polygons (verified campus area)
# DO NOT include OSM roads — they cover entire Amaravati/Guntur region
campus_lats = []
campus_lons = []
campus_lats.extend(df_all["latitude"].tolist())
campus_lons.extend(df_all["longitude"].tolist())
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
else:
    st.warning("OSM roads data not available or empty.")

roads_fg.add_to(m)

# 2. Zone Polygons Layer
zones_fg = folium.FeatureGroup(name="Campus Zones (Provisional — Manual, Not Official)", show=True, overlay=True, control=True)
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
candidates_fg = folium.FeatureGroup(name="Proposed Candidates (25)", show=True, overlay=True, control=True)
for _, row in df_all.iterrows():
    is_selected = row["ga_selected"]
    color = "#1e7e34" if is_selected else "#1f77b4"  # green / blue (hex)
    icon_name = "bolt" if is_selected else "circle"
    prefix = "fa"

    popup_html = f"""
    <b>{row['name']}</b><br>
    Candidate ID: {row['candidate_id']}<br>
    Zone: {row['zone']}<br>
    {'✅ <b>GA SELECTED</b><br>' if is_selected else ''}
    Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
    Fuzzy Suitability: {row['fuzzy_suitability_score']:.3f}<br>
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
ga_selected_fg = folium.FeatureGroup(name="GA-Selected Only (2)", show=False, overlay=True, control=True)
for _, row in df_ga.iterrows():
    popup_html = f"""
    <b>{row['name']} ✅ GA SELECTED</b><br>
    Candidate ID: {row['candidate_id']}<br>
    Zone: {row['zone']}<br>
    Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
    Fuzzy Suitability: {row['fuzzy_suitability_score']:.3f}<br>
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

# 4b. Illustrative Top-5 Selection Layer (by Fuzzy Suitability) — NOT GA-selected
# Top 5 candidates by fuzzy_suitability_score: CAND_017, CAND_018, CAND_022, CAND_023, CAND_024
# All have score 0.187838. Clearly labeled as illustrative selection.
illustrative_top5_ids = {"CAND_017", "CAND_018", "CAND_022", "CAND_023", "CAND_024"}
illustrative_fg = folium.FeatureGroup(
    name="Illustrative Top-5 (by Fuzzy Suitability)",
    show=True, overlay=True, control=True
)
for _, row in df_all.iterrows():
    if row["candidate_id"] in illustrative_top5_ids:
        popup_html = f"""
        <b>{row['name']}</b><br>
        Candidate ID: {row['candidate_id']}<br>
        Zone: {row['zone']}<br>
        <b>ILLUSTRATIVE SELECTION (by Fuzzy Suitability)</b><br>
        <span style="color:#1e7e34;">NOT GA-selected — for demonstration only</span><br>
        Predicted Annual Demand: {row['predicted_annual_demand_kwh']:,.0f} kWh<br>
        Fuzzy Suitability: {row['fuzzy_suitability_score']:.3f}<br>
        Total CAPEX: ₹{row['total_capex_inr_lakhs']:.2f} L<br>
        Land: ₹{row['land_cost_inr_lakhs']:.2f} L | Grid: ₹{row['grid_connection_cost_inr_lakhs']:.2f} L | Construction: ₹{row['construction_cost_inr_lakhs']:.2f} L
        """
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{row['candidate_id']}: {row['name']} ⭐ Illustrative",
            icon=folium.Icon(color="green", icon="bolt", prefix="fa")
        ).add_to(illustrative_fg)

illustrative_fg.add_to(m)

# 5. Google Maps Reference Landmarks Layer (toggleable)
gm_fg = folium.FeatureGroup(name="Google Maps Campus Landmarks (Reference)", show=True, overlay=True, control=True)
for lm in GM_LANDMARKS:
    folium.Marker(
        location=[lm["lat"], lm["lon"]],
        popup=f"<b>{lm['name']}</b><br>Google Maps verified location",
        tooltip=f"📍 {lm['name']} (Google Maps)",
        icon=folium.Icon(color="red", icon="map-marker", prefix="fa")
    ).add_to(gm_fg)

gm_fg.add_to(m)

# 6. Illustrative Campus Placements Layer (toggleable, distinct style)
campus_placement_fg = folium.FeatureGroup(
    name="Illustrative Campus Placements (Not Surveyed)",
    show=False, overlay=True, control=True
)
for cp in ILLUSTRATIVE_CAMPUS_PLACEMENTS:
    popup_html = f"""
    <b>{cp['name']}</b><br>
    <span style="color:#e67e22;"><b>ILLUSTRATIVE CAMPUS PLACEMENT — NOT SURVEYED</b></span><br>
    {cp['description']}<br>
    <i>Not from project data. Not a verified charging site.</i>
    """
    folium.Marker(
        location=[cp["lat"], cp["lon"]],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=f"📍 {cp['name']} (Illustrative)",
        icon=folium.Icon(color="orange", icon="charging-station", prefix="fa")
    ).add_to(campus_placement_fg)

campus_placement_fg.add_to(m)

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
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Zones (Manual, Not Official)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#1f77b4; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Academic</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#ff7f0e; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Boys Hostel</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#e377c2; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Girls Hostel</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#2ca02c; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Staff Quarters</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#d62728; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Administrative</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#9467bd; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Commercial</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#8c564b; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Sports Complex</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#e377c2; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> North Gate</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#7f7f7f; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> South Gate</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="display:inline-block; width:14px; height:14px; background:#bcbd22; border-radius:2px; margin-right:6px; vertical-align:middle; border:1px solid #999;"></span> Main Gate</div>
    <div style="border-bottom: 1px solid #ddd; margin: 8px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Candidates</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1e7e34; font-size:14px; margin-right:6px; vertical-align:middle;">⚡</span> GA-Selected (2)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1f77b4; font-size:12px; margin-right:6px; vertical-align:middle;">●</span> Proposed (23)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#1e7e34; font-size:14px; margin-right:6px; vertical-align:middle;">⚡</span> Illustrative Top-5 (by Fuzzy Suitability)</div>
    <div style="border-bottom: 1px solid #ddd; margin: 8px 0;"></div>
    <div style="font-weight: 600; color: #111111; margin-bottom: 4px;">Reference Landmarks</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#d73027; font-size:14px; margin-right:6px; vertical-align:middle;">📍</span> Google Maps Campus Landmarks (8)</div>
    <div style="margin-bottom: 2px; color: #111111;"><span style="color:#e67e22; font-size:14px; margin-right:6px; vertical-align:middle;">⚡</span> Illustrative Campus Placements (Not Surveyed)</div>
</div>
"""
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
    use_container_width=True,
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
st.plotly_chart(fig, use_container_width=True)

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

# ─── RAW DATA (optional) ───
with st.expander("📋 Full Data (All 25 Candidates)"):
    display_cols = [
        "candidate_id", "name", "zone", "latitude", "longitude",
        "predicted_annual_demand_kwh", "fuzzy_suitability_score",
        "total_capex_inr_lakhs", "land_cost_inr_lakhs",
        "grid_connection_cost_inr_lakhs", "construction_cost_inr_lakhs",
        "demand_prediction_source", "ga_selected"
    ]
    st.dataframe(df_all[display_cols])

st.caption("Data source: demo_dashboard.py → data/processed/candidates_demo_fuzzy_scored.csv & ga_solution_demo.csv (generated by demo pipeline)")