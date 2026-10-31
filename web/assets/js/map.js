/* Interactive campus map: real OSM / OpenChargeMap layers, custom markers, floating layer panel. */
(function () {
  "use strict";
  const { D, fmt, zone, COLORS, escape } = EV;

  const INDIA = L.latLngBounds([5.5, 67.5], [37.5, 98.0]);
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const BASEMAPS = [
    { key: "satellite", label: "Satellite", url: ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 19,
      attribution: "Tiles © Esri — Esri, Maxar, Earthstar Geographics, GIS User Community" },
    { key: "street", label: "Street", url: ESRI + "World_Street_Map/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 19,
      attribution: "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors" },
    { key: "light", label: "Light", url: ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 16,
      attribution: "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors" },
  ];
  const ROAD_STYLE = [
    { types: ["motorway", "trunk", "primary"], color: "#ff5d5d", weight: 3.2, label: "Primary / trunk" },
    { types: ["secondary", "tertiary"], color: "#ff9f43", weight: 2.6, label: "Secondary / tertiary" },
    { types: ["residential", "service", "unclassified"], color: "#ffe08a", weight: 1.8, label: "Residential / service" },
    { types: [], color: "#c9c9d6", weight: 1.4, label: "Footpath / other" },
  ];
  const roadStyle = (hw) => ROAD_STYLE.find((s) => s.types.includes(hw)) || ROAD_STYLE[3];

  const PIN = "M17 43S3 27.5 3 16.5a14 14 0 1 1 28 0C31 27.5 17 43 17 43z";
  const BOLT = "M19 7 11 19h6l-2 9 9-12h-6z";

  function pinIcon(c, i) {
    const rank = EV.selectedRank.get(c.candidate_id);
    const sel = rank != null;
    const fill = sel ? COLORS.lime : COLORS.blue;
    const inner = sel ? `<span class="pin__rank">${rank}</span>` : "";
    const glyph = sel ? "" : `<path d="${BOLT}" fill="#fff"/>`;
    const rings = sel && !EV.reduceMotion ? '<span class="pin__ring"></span><span class="pin__ring pin__ring--2"></span>' : "";
    return L.divIcon({
      className: "ev-icon",
      html: `<div class="pin pin__drop" style="--d:${(i * 0.045).toFixed(2)}s">${rings}<svg viewBox="0 0 34 44" aria-hidden="true"><path d="${PIN}" fill="${fill}" stroke="${COLORS.ink}" stroke-width="2"/>${glyph}</svg>${inner}</div>`,
      iconSize: [34, 44], iconAnchor: [17, 43], popupAnchor: [0, -40], tooltipAnchor: [16, -26],
    });
  }

  function popupHtml(c) {
    const z = zone(c.zone);
    const rank = EV.selectedRank.get(c.candidate_id);
    const total = c.total_capex_inr_lakhs || 0;
    const part = (v) => `${((v || 0) / total) * 100}%`;
    return `<div class="pop">
      <div class="pop__top"><span class="pop__id">${escape(c.candidate_id)}</span>
        <span class="pop__zone" style="--c:${z.color}"><i></i>${escape(z.label)}</span>
        ${rank != null ? `<span class="pop__ga">GA selected · #${rank}</span>` : ""}</div>
      <h4>${escape(c.name)}</h4>
      <dl>
        <dt>Predicted annual demand</dt><dd>${fmt.num(c.predicted_annual_demand_kwh)} kWh</dd>
        <dt>Fuzzy suitability</dt><dd>${fmt.score(c.fuzzy_suitability_score)}
          <div class="pop__bar"><i style="width:${(c.fuzzy_suitability_score || 0) * 100}%"></i></div></dd>
        <dt>Total CAPEX</dt><dd>${fmt.lakh(c.total_capex_inr_lakhs)}</dd>
      </dl>
      <div class="pop__cost" aria-hidden="true">
        <i style="width:${part(c.land_cost_inr_lakhs)};background:${COLORS.amber}"></i>
        <i style="width:${part(c.grid_connection_cost_inr_lakhs)};background:${COLORS.blue}"></i>
        <i style="width:${part(c.construction_cost_inr_lakhs)};background:${COLORS.orange}"></i></div>
      <div class="pop__legend"><span><b style="background:${COLORS.amber}"></b>Land ${fmt.lakh(c.land_cost_inr_lakhs)}</span>
        <span><b style="background:${COLORS.blue}"></b>Grid ${fmt.lakh(c.grid_connection_cost_inr_lakhs)}</span>
        <span><b style="background:${COLORS.orange}"></b>Build ${fmt.lakh(c.construction_cost_inr_lakhs)}</span></div>
    </div>`;
  }

  function chargerPopup(p) {
    const where = [p.town, p.state].filter(Boolean).map(escape).join(", ");
    return `<div class="pop"><div class="pop__top"><span class="pop__id">OpenChargeMap #${escape(p.station_id)}</span></div>
      <h4>${escape(p.name)}</h4><dl>
      ${where ? `<dt>Location</dt><dd>${where}</dd>` : ""}
      <dt>From campus</dt><dd>${fmt.num(p.distance_to_campus_km, 1)} km</dd>
      <dt>Charging points</dt><dd>${fmt.num(p.num_connectors)}</dd>
      ${p.max_power_kw ? `<dt>Max power</dt><dd>${fmt.num(p.max_power_kw)} kW</dd>` : ""}
      ${p.operator ? `<dt>Operator</dt><dd>${escape(p.operator)}</dd>` : ""}
      <dt>Status</dt><dd>${escape(p.status || "unknown")}</dd></dl></div>`;
  }

  EV.initMap = function () {
    const map = L.map("leaflet", {
      zoomControl: false, minZoom: 4, maxBounds: INDIA, maxBoundsViscosity: 1, worldCopyJump: false,
      zoomSnap: 0.25, scrollWheelZoom: true,
    });
    const campusBounds = L.geoJSON(D.geo.campus).getBounds();
    map.fitBounds(campusBounds, { padding: [30, 30] });

    // base maps
    const bases = {};
    BASEMAPS.forEach((b) => {
      bases[b.key] = L.tileLayer(b.url, { maxZoom: 20, maxNativeZoom: b.maxNativeZoom, noWrap: true, bounds: INDIA, attribution: b.attribution });
    });
    let activeBase = bases.satellite.addTo(map);
    const skeleton = document.getElementById("map-skeleton");
    activeBase.once("load", () => skeleton.classList.add("is-done"));
    setTimeout(() => skeleton.classList.add("is-done"), 6000);

    const labels = L.tileLayer(ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 20, maxNativeZoom: 19, noWrap: true, bounds: INDIA, attribution: "Labels © Esri" });

    // overlays
    const roads = L.geoJSON(D.geo.roads, {
      style: (f) => { const s = roadStyle(f.properties.highway); return { color: s.color, weight: s.weight, opacity: 0.85 }; },
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(`${escape(p.name || "Unnamed road")} · ${escape(p.highway)}${p.length_m ? ` · ${fmt.num(p.length_m)} m` : ""}`, { sticky: true });
        l.on("mouseover", () => l.setStyle({ weight: roadStyle(p.highway).weight + 3, color: COLORS.lime }));
        l.on("mouseout", () => roads.resetStyle(l));
      },
    });
    const campus = L.geoJSON(D.geo.campus, { style: { color: "#ffffff", weight: 3, dashArray: "8 6", fill: false }, interactive: false });
    const buildings = L.geoJSON(D.geo.buildings, {
      style: { color: COLORS.amber, weight: 1.2, fillColor: COLORS.amber, fillOpacity: 0.32 },
      onEachFeature: (f, l) => l.bindTooltip(`${escape(f.properties.name || "Building")} · ${escape(f.properties.building || "")}`, { sticky: true }),
    });
    const parking = L.geoJSON(D.geo.parking, {
      style: { color: "#4fd1b5", weight: 2, fillColor: "#4fd1b5", fillOpacity: 0.55 },
      onEachFeature: (f, l) => l.bindTooltip(`Parking lot · ${escape(f.properties.osm_id)}`, { sticky: true }),
    });
    const zones = L.geoJSON(D.geo.zones, {
      style: (f) => { const c = zone(f.properties.zone_id).color; return { color: c, weight: 2, dashArray: "6 5", fillColor: c, fillOpacity: 0.16 }; },
      onEachFeature: (f, l) => {
        l.bindTooltip(escape(f.properties.zone_name), { sticky: true });
        l.on("mouseover", () => l.setStyle({ fillOpacity: 0.32 }));
        l.on("mouseout", () => zones.resetStyle(l));
      },
    });
    const state = L.geoJSON(D.geo.state, { style: { color: COLORS.blueLight, weight: 2, dashArray: "10 7", fill: false }, interactive: false });

    const chargerFeatures = (D.geo.chargers && D.geo.chargers.features) || [];
    const chargers = L.markerClusterGroup({
      showCoverageOnHover: false, maxClusterRadius: 48,
      iconCreateFunction: (cl) => {
        const n = cl.getChildCount();
        const size = n < 10 ? 34 : n < 30 ? 42 : 50;
        return L.divIcon({ className: "ev-icon", html: `<div class="cluster" style="width:${size}px;height:${size}px">${n}</div>`, iconSize: [size, size] });
      },
    });
    const plugIcon = L.divIcon({ className: "ev-icon", html: '<div class="plug-pin"><svg viewBox="0 0 24 24"><path d="M9 2v5H7V2zm8 0v5h-2V2zM5 8h14v4a7 7 0 0 1-6 6.9V22h-2v-3.1A7 7 0 0 1 5 12z"/></svg></div>', iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -12] });
    chargerFeatures.forEach((f) => {
      const [lon, lat] = f.geometry.coordinates;
      chargers.addLayer(L.marker([lat, lon], { icon: plugIcon, keyboard: true, title: f.properties.name })
        .bindPopup(chargerPopup(f.properties), { maxWidth: 300 }));
    });

    const markers = new Map();
    const candidates = L.layerGroup();
    const gaOnly = L.layerGroup();
    const top5 = L.layerGroup();
    const ordered = [...D.candidates].sort((a, b) => (EV.isSelected(a.candidate_id) - EV.isSelected(b.candidate_id)));
    ordered.forEach((c, i) => {
      const ll = [c.latitude, c.longitude];
      const m = L.marker(ll, { icon: pinIcon(c, i), riseOnHover: true, title: c.name, zIndexOffset: EV.isSelected(c.candidate_id) ? 1000 : 0 })
        .bindPopup(popupHtml(c), { maxWidth: 320 })
        .bindTooltip(`${escape(c.candidate_id)} · ${escape(c.name)}`, { direction: "right" });
      candidates.addLayer(m);
      markers.set(c.candidate_id, m);
      if (EV.isSelected(c.candidate_id)) {
        gaOnly.addLayer(L.marker(ll, { icon: pinIcon(c, 0), zIndexOffset: 1200 }).bindPopup(popupHtml(c), { maxWidth: 320 }));
      }
    });
    [...D.candidates].sort((a, b) => (b.fuzzy_suitability_score ?? -1) - (a.fuzzy_suitability_score ?? -1)).slice(0, 5)
      .forEach((c, i) => top5.addLayer(L.marker([c.latitude, c.longitude], {
        icon: L.divIcon({ className: "ev-icon", html: `<div class="star-badge" title="Top-5 fuzzy #${i + 1}">${i + 1}</div>`, iconSize: [26, 26], iconAnchor: [-6, 52] }),
        zIndexOffset: 1500, interactive: false,
      })));

    /* ───────── floating layer panel (also the legend) ───────── */
    const nSel = D.selected.length;
    const swatch = {
      line: (c, dash) => `<svg viewBox="0 0 18 18"><path d="M1 12c4-6 9 2 16-5" fill="none" stroke="${c}" stroke-width="3" ${dash ? 'stroke-dasharray="4 3"' : ""} stroke-linecap="round"/></svg>`,
      area: (c) => `<svg viewBox="0 0 18 18"><rect x="2" y="2" width="14" height="14" rx="3" fill="${c}" fill-opacity=".45" stroke="${c}" stroke-width="2"/></svg>`,
      pin: (c) => `<svg viewBox="0 0 34 44"><path d="${PIN}" fill="${c}" stroke="${COLORS.ink}" stroke-width="2.5"/></svg>`,
      dot: (c, t = "") => `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="7.5" fill="${c}"/><text x="9" y="13" text-anchor="middle" font-size="10" font-weight="800" fill="#fff">${t}</text></svg>`,
    };
    const GROUPS = [
      { title: "Candidates", rows: [
        { layer: candidates, on: true, name: "Candidate sites", count: D.candidates.length, sw: swatch.pin(COLORS.blue) },
        { layer: gaOnly, on: false, name: "GA-selected only", count: nSel, sw: swatch.pin(COLORS.lime) },
        { layer: top5, on: false, name: "Top-5 by fuzzy suitability", count: 5, sw: swatch.dot(COLORS.purple, "★") },
      ] },
      { title: "Campus (OpenStreetMap)", rows: [
        { layer: zones, on: true, name: "Campus zones", count: (D.geo.zones?.features || []).length, sw: swatch.area(COLORS.blue), zones: true },
        { layer: buildings, on: true, name: "Buildings", count: (D.geo.buildings?.features || []).length, sw: swatch.area(COLORS.amber) },
        { layer: roads, on: true, name: "Roads", count: (D.geo.roads?.features || []).length, sw: swatch.line("#ff9f43") },
        { layer: parking, on: true, name: "Parking lots", count: (D.geo.parking?.features || []).length, sw: swatch.area("#4fd1b5") },
        { layer: campus, on: true, name: "Campus boundary", sw: swatch.line("#6b6e80", true) },
      ] },
      { title: "Context", rows: [
        { layer: chargers, on: true, name: "Public chargers, Andhra Pradesh", count: chargerFeatures.length, sw: swatch.dot(COLORS.orange) },
        { layer: state, on: true, name: "Andhra Pradesh boundary", sw: swatch.line(COLORS.blue, true) },
        { layer: labels, on: true, name: "Place labels", sw: swatch.line(COLORS.ink) },
      ] },
    ];
    const groupsEl = document.getElementById("layer-groups");
    GROUPS.forEach((g) => {
      const wrap = document.createElement("div");
      wrap.className = "lgroup";
      wrap.innerHTML = `<p class="lgroup__title">${g.title}</p>`;
      g.rows.forEach((r) => {
        if (r.on) r.layer.addTo(map);
        const row = document.createElement("label");
        row.className = "lrow";
        row.innerHTML = `<input type="checkbox" ${r.on ? "checked" : ""}><span class="lrow__sw">${r.sw}</span>
          <span class="lrow__name">${r.name}</span>${r.count != null ? `<span class="lrow__count">${r.count}</span>` : ""}<span class="lrow__check"></span>`;
        row.querySelector("input").addEventListener("change", (e) => (e.target.checked ? r.layer.addTo(map) : map.removeLayer(r.layer)));
        r.input = row.querySelector("input");
        wrap.appendChild(row);
        if (r.zones) {
          const chips = document.createElement("div");
          chips.className = "zone-chips";
          const present = new Set((D.geo.zones?.features || []).map((f) => f.properties.zone_id));
          chips.innerHTML = Object.entries(EV.ZONES).filter(([k]) => present.has(k))
            .map(([, z]) => `<span class="zone-chip" style="--c:${z.color}"><i></i>${z.label}</span>`).join("");
          wrap.appendChild(chips);
        }
      });
      groupsEl.appendChild(wrap);
    });
    const candidateRow = GROUPS[0].rows[0];

    const seg = document.getElementById("basemaps");
    BASEMAPS.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "radio");
      btn.setAttribute("aria-checked", b.key === "satellite");
      btn.textContent = b.label;
      btn.addEventListener("click", () => {
        map.removeLayer(activeBase);
        activeBase = bases[b.key].addTo(map);
        activeBase.bringToBack();
        seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", x === btn));
      });
      seg.appendChild(btn);
    });

    const panel = document.getElementById("layer-panel");
    const toggle = document.getElementById("panel-toggle");
    const setPanel = (open) => { panel.classList.toggle("is-collapsed", !open); toggle.setAttribute("aria-expanded", open); };
    toggle.addEventListener("click", () => setPanel(panel.classList.contains("is-collapsed")));
    if (window.matchMedia("(max-width: 900px)").matches) setPanel(false);

    document.getElementById("road-key").innerHTML = ROAD_STYLE.map((s) => `<span style="--c:${s.color}"><i></i>${s.label}</span>`).join("");

    document.getElementById("zoom-in").addEventListener("click", () => map.zoomIn());
    document.getElementById("zoom-out").addEventListener("click", () => map.zoomOut());
    document.getElementById("reset-view").addEventListener("click", () => map.flyToBounds(campusBounds, { padding: [30, 30], duration: EV.reduceMotion ? 0 : 1.2 }));
    document.getElementById("state-view").addEventListener("click", () => map.flyToBounds(state.getBounds(), { padding: [20, 20], duration: EV.reduceMotion ? 0 : 1.4 }));

    new ResizeObserver(EV.debounce(() => map.invalidateSize(), 120)).observe(document.getElementById("leaflet"));

    EV.map = {
      leaflet: map,
      focusCandidate(id) {
        const m = markers.get(id);
        if (!m) return;
        if (!map.hasLayer(candidates)) { candidates.addTo(map); candidateRow.input.checked = true; }
        document.getElementById("map").scrollIntoView({ behavior: EV.reduceMotion ? "auto" : "smooth", block: "start" });
        setTimeout(() => {
          map.flyTo(m.getLatLng(), 18, { duration: EV.reduceMotion ? 0 : 1.1 });
          map.once("moveend", () => m.openPopup());
        }, EV.reduceMotion ? 0 : 450);
      },
    };
  };
})();
