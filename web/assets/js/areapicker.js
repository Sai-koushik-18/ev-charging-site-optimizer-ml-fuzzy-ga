/* Area picker for the live planner: search a place, fly there, draw (or adjust) the boundary on the map. */
(function () {
  "use strict";
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const NOMINATIM = "https://nominatim.openstreetmap.org/search";
  const INDIA = L.latLngBounds([5.5, 67.5], [37.5, 98.0]);
  const LINE = { color: "#b6f05a", weight: 3, opacity: 1 };
  const MAX_OUTLINE_POINTS = 40; // few enough corners to edit by hand

  /* Spherical polygon area in km² (ring of [lat, lng]). */
  function areaKm2(latlngs) {
    if (latlngs.length < 3) return 0;
    const R = 6371.0088, rad = Math.PI / 180;
    let s = 0;
    for (let i = 0; i < latlngs.length; i++) {
      const a = latlngs[i], b = latlngs[(i + 1) % latlngs.length];
      s += (b.lng - a.lng) * rad * (2 + Math.sin(a.lat * rad) + Math.sin(b.lat * rad));
    }
    return Math.abs((s * R * R) / 2);
  }

  /* Largest outer ring of a GeoJSON Polygon/MultiPolygon, as LatLngs, thinned to an editable number of points. */
  function outlineRing(map, geojson) {
    const polys = geojson.type === "Polygon" ? [geojson.coordinates] : geojson.type === "MultiPolygon" ? geojson.coordinates : [];
    if (!polys.length) return null;
    const rings = polys.map((p) => p[0].map(([lng, lat]) => L.latLng(lat, lng)));
    let ring = rings.reduce((best, r) => (areaKm2(r) > areaKm2(best) ? r : best));
    if (ring.length > 1 && ring[0].equals(ring[ring.length - 1])) ring = ring.slice(0, -1);
    const z = map.getZoom();
    let tol = 0.5;
    while (ring.length > MAX_OUTLINE_POINTS && tol < 64) {
      const pts = L.LineUtil.simplify(ring.map((ll) => map.project(ll, z)), tol);
      if (pts.length <= MAX_OUTLINE_POINTS) return pts.map((p) => map.unproject(p, z));
      tol *= 2;
    }
    return ring;
  }

  EV.areaPicker = function (root, { onChange, onPlace }) {
    const el = root.querySelector(".picker__map");
    const map = L.map(el, { zoomControl: false, minZoom: 4, maxBounds: INDIA.pad(0.1), maxBoundsViscosity: 1, doubleClickZoom: true })
      .fitBounds(INDIA);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.tileLayer(ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 20, maxNativeZoom: 19, noWrap: true,
      attribution: "Tiles © Esri, Maxar · Search © OpenStreetMap" }).addTo(map);
    L.tileLayer(ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 20, maxNativeZoom: 19, noWrap: true }).addTo(map);

    const $ = (sel) => root.querySelector(sel);
    const hint = $(".picker__hint");
    let points = [];          // LatLngs of the boundary
    let closed = false;       // the boundary is a finished polygon
    let drawing = false;
    let suggestion = null;    // outline from the search result
    let shape = null, guide = null;
    const handles = L.layerGroup().addTo(map);

    const vertexIcon = (first) => L.divIcon({ className: `picker__vertex${first ? " is-first" : ""}`, iconSize: [14, 14] });
    const midIcon = L.divIcon({ className: "picker__mid", iconSize: [10, 10] });

    function render() {
      if (shape) shape.remove();
      handles.clearLayers();
      shape = null;
      if (points.length) {
        shape = closed
          ? L.polygon(points, { ...LINE, fillColor: "#b6f05a", fillOpacity: 0.18 }).addTo(map)
          : L.polyline(points, { ...LINE, dashArray: drawing ? null : "6 6" }).addTo(map);
      }
      points.forEach((ll, i) => {
        const m = L.marker(ll, { icon: vertexIcon(i === 0 && drawing), draggable: closed, keyboard: false, title: closed ? "Drag to move · double-click to delete" : "" }).addTo(handles);
        m.on("click", (e) => {
          if (drawing && i === 0 && points.length >= 3) { L.DomEvent.stopPropagation(e); finish(); }
        });
        m.on("drag", (e) => { points[i] = e.target.getLatLng(); shape.setLatLngs(points); });
        m.on("dragend", () => { render(); changed(); });
        m.on("dblclick", (e) => {
          L.DomEvent.stopPropagation(e);
          if (closed && points.length > 3) { points.splice(i, 1); render(); changed(); }
        });
      });
      if (closed) {
        points.forEach((a, i) => {
          const b = points[(i + 1) % points.length];
          const mid = L.marker(L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2), { icon: midIcon, draggable: true, keyboard: false, title: "Drag to add a corner" }).addTo(handles);
          mid.on("dragstart", () => points.splice(i + 1, 0, mid.getLatLng()));
          mid.on("drag", (e) => { points[i + 1] = e.target.getLatLng(); shape.setLatLngs(points); });
          mid.on("dragend", () => { render(); changed(); });
        });
      }
      root.classList.toggle("is-drawing", drawing);
      $(".js-undo").disabled = !points.length;
      $(".js-clear").disabled = !points.length;
      $(".js-draw").classList.toggle("is-on", drawing);
      $(".js-draw").textContent = drawing ? (points.length >= 3 ? "Finish shape" : "Drawing…") : closed ? "Redraw" : "Draw boundary";
      hint.textContent = drawing
        ? points.length < 3 ? "Click the map to add corners around the area." : "Keep clicking, or click the first corner (or Finish shape) to close it."
        : closed ? "Drag corners to adjust · drag a small dot to add a corner · double-click a corner to delete it."
          : suggestion ? "Use the outline from the search, or draw your own boundary." : "Search a place, then draw its boundary.";
    }

    function changed() {
      onChange({ closed, points: points.length, km2: closed ? areaKm2(points) : 0 });
    }

    function start() {
      points = []; closed = false; drawing = true;
      map.doubleClickZoom.disable();
      render(); changed();
    }
    function finish() {
      if (points.length < 3) return;
      drawing = false; closed = true;
      map.doubleClickZoom.enable();
      if (guide) { guide.remove(); guide = null; }
      render(); changed();
    }
    function clear() {
      points = []; closed = false; drawing = false;
      map.doubleClickZoom.enable();
      if (guide) { guide.remove(); guide = null; }
      render(); changed();
    }

    map.on("click", (e) => {
      if (!drawing) return;
      points.push(e.latlng);
      render(); changed();
    });
    map.on("mousemove", (e) => {
      if (!drawing || !points.length) return;
      const line = [points[points.length - 1], e.latlng].concat(points.length >= 2 ? [points[0]] : []);
      if (guide) guide.setLatLngs(line);
      else guide = L.polyline(line, { color: "#b6f05a", weight: 2, dashArray: "4 6", interactive: false }).addTo(map);
    });

    $(".js-draw").addEventListener("click", () => (drawing ? (points.length >= 3 ? finish() : null) : start()));
    $(".js-undo").addEventListener("click", () => {
      if (closed) { closed = false; drawing = true; map.doubleClickZoom.disable(); }
      points.pop();
      if (!points.length) { drawing = false; map.doubleClickZoom.enable(); }
      render(); changed();
    });
    $(".js-clear").addEventListener("click", clear);
    $(".js-outline").addEventListener("click", () => {
      if (!suggestion) return;
      const ring = outlineRing(map, suggestion.geojson);
      if (!ring) return;
      if (suggestion.layer) { suggestion.layer.remove(); suggestion.layer = null; }
      $(".js-outline").hidden = true;
      points = ring; closed = true; drawing = false;
      map.doubleClickZoom.enable();
      map.fitBounds(L.latLngBounds(ring), { padding: [30, 30] });
      render(); changed();
    });

    /* ───────── search (OpenStreetMap Nominatim, India only, on submit — no autocomplete) ───────── */
    const results = $(".picker__results");
    let lastSearch = 0;
    async function search(q) {
      const wait = 1100 - (Date.now() - lastSearch); // Nominatim allows one request per second
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      lastSearch = Date.now();
      results.hidden = false;
      results.innerHTML = `<li class="is-note">Searching…</li>`;
      try {
        const url = `${NOMINATIM}?format=jsonv2&polygon_geojson=1&polygon_threshold=0.00002&limit=6&countrycodes=in&q=${encodeURIComponent(q)}`;
        const res = await fetch(url, { headers: { "Accept-Language": "en" } });
        const hits = await res.json();
        if (!hits.length) { results.innerHTML = `<li class="is-note">No match in India. Try a fuller name, or pan the map and draw the area yourself.</li>`; return; }
        results.innerHTML = hits.map((h, i) => `<li><button type="button" data-i="${i}"><b>${EV.escape(h.name || h.display_name.split(",")[0])}</b><small>${EV.escape(h.display_name)}</small></button></li>`).join("");
        results.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => choose(hits[+b.dataset.i])));
      } catch (err) {
        results.innerHTML = `<li class="is-note">Search is unavailable right now. Pan and zoom the map, then draw the area.</li>`;
      }
    }
    function choose(h) {
      results.hidden = true;
      const [s, n, w, e] = h.boundingbox.map(Number);
      map.flyToBounds([[s, w], [n, e]], { padding: [40, 40], maxZoom: 17, duration: 1.2 });
      if (suggestion && suggestion.layer) suggestion.layer.remove();
      const hasOutline = h.geojson && /Polygon/.test(h.geojson.type);
      suggestion = hasOutline ? { geojson: h.geojson, layer: L.geoJSON(h.geojson, { style: { color: "#ffffff", weight: 2, dashArray: "6 6", fill: false }, interactive: false }).addTo(map) } : null;
      $(".js-outline").hidden = !hasOutline;
      if (!closed && !drawing) render();
      onPlace(h.name || h.display_name.split(",")[0]);
    }
    $(".picker__search").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $(".picker__search input").value.trim();
      if (q.length >= 3) search(q);
    });
    document.addEventListener("click", (e) => { if (!root.querySelector(".picker__search").contains(e.target)) results.hidden = true; });

    // the map sits in a section that may be laid out after Leaflet starts
    new IntersectionObserver((entries) => entries.forEach((en) => en.isIntersecting && map.invalidateSize())).observe(el);

    render();
    return {
      map,
      geometry() {
        if (!closed) return null;
        const ring = points.map((ll) => [+ll.lng.toFixed(6), +ll.lat.toFixed(6)]);
        return { type: "Polygon", coordinates: [ring.concat([ring[0]])] };
      },
      setGeometry(geojson) {
        const ring = geojson && outlineRing(map, geojson);
        if (!ring) return;
        points = ring; closed = true; drawing = false;
        map.fitBounds(L.latLngBounds(ring), { padding: [30, 30] });
        render(); changed();
      },
      setEnabled(on) { root.classList.toggle("is-locked", !on); },
      closeIfOpen() { if (drawing && points.length >= 3) finish(); },
    };
  };
})();
