/* Candidate ranking table: sortable, searchable, zone filter, GA-only toggle, row → map. */
(function () {
  "use strict";
  const { D, fmt, zone, escape } = EV;

  const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 10 17l9-10"/></svg>';

  // Same columns, order and number formats as the original dashboard; extras sit behind "More columns".
  const COLUMNS = [
    { key: "rank", label: "Rank", num: true, render: (c) => `<span class="rank ${c.rank <= 3 ? `rank--${c.rank}` : ""}">${c.rank}</span>` },
    { key: "candidate_id", label: "Candidate ID", render: (c) => `<span class="cell-id">${escape(c.candidate_id)}</span>` },
    { key: "name", label: "Name", render: (c) => `<span class="cell-name">${escape(c.name)}</span>` },
    { key: "zone", label: "Zone", sortValue: (c) => zone(c.zone).label, render: (c) => { const z = zone(c.zone); return `<span class="cell-zone" style="--c:${z.color}"><i></i>${escape(z.label)}</span>`; } },
    { key: "latitude", label: "Latitude", num: true, render: (c) => fmt.coord(c.latitude) },
    { key: "longitude", label: "Longitude", num: true, render: (c) => fmt.coord(c.longitude) },
    { key: "predicted_annual_demand_kwh", label: "Annual demand (kWh)", num: true, render: (c) => fmt.num(c.predicted_annual_demand_kwh) },
    { key: "fuzzy_suitability_score", label: "Fuzzy suitability", num: true,
      render: (c) => `<span class="score"><i><b style="width:${(c.fuzzy_suitability_score || 0) * 100}%"></b></i>${fmt.score(c.fuzzy_suitability_score)}</span>` },
    { key: "total_capex_inr_lakhs", label: "Total CAPEX (₹L)", num: true, render: (c) => `<b>${fmt.lakh(c.total_capex_inr_lakhs)}</b>` },
    { key: "land_cost_inr_lakhs", label: "Land (₹L)", num: true, render: (c) => fmt.lakh(c.land_cost_inr_lakhs) },
    { key: "grid_connection_cost_inr_lakhs", label: "Grid (₹L)", num: true, render: (c) => fmt.lakh(c.grid_connection_cost_inr_lakhs) },
    { key: "construction_cost_inr_lakhs", label: "Construction (₹L)", num: true, render: (c) => fmt.lakh(c.construction_cost_inr_lakhs) },
    { key: "ga_selected", label: "GA selected", sortValue: (c) => (c.ga_selected ? 1 : 0),
      render: (c) => `<span class="check ${c.ga_selected ? "is-on" : ""}" role="img" aria-label="${c.ga_selected ? "Selected by the GA" : "Not selected"}">${c.ga_selected ? CHECK : ""}</span>` },
    { key: "demand_prediction_source", label: "Demand source", extra: true, render: (c) => escape(c.demand_prediction_source) },
    { key: "demand_index", label: "Demand index", num: true, extra: true, render: (c) => fmt.num(c.demand_index, 1) },
    { key: "traffic_accessibility_score", label: "Traffic access", num: true, extra: true, render: (c) => fmt.score(c.traffic_accessibility_score) },
    { key: "poi_score", label: "POI score", num: true, extra: true, render: (c) => fmt.score(c.poi_score) },
    { key: "existing_chargers_3km", label: "Chargers ≤ 3 km", num: true, extra: true, render: (c) => fmt.num(c.existing_chargers_3km) },
    { key: "grid_dist_m", label: "Grid distance (m)", num: true, extra: true, render: (c) => fmt.num(c.grid_dist_m) },
    { key: "land_cost_tier", label: "Land tier", extra: true, render: (c) => escape(c.land_cost_tier) },
    { key: "site_source", label: "Site type", extra: true, render: (c) => escape(String(c.site_source || "").replaceAll("_", " ")) },
  ];

  EV.initTable = function () {
    // Rank by predicted annual demand, as in the original dashboard
    const rows = [...D.candidates]
      .sort((a, b) => b.predicted_annual_demand_kwh - a.predicted_annual_demand_kwh)
      .map((c, i) => ({ ...c, rank: i + 1, ga_selected: EV.isSelected(c.candidate_id) }));

    const table = document.getElementById("rank-table");
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    const state = { sortKey: "rank", dir: 1, query: "", zone: "", onlySel: false };

    thead.innerHTML = `<tr>${COLUMNS.map((col) =>
      `<th scope="col" data-key="${col.key}" class="${col.num ? "num-col" : ""} ${col.extra ? "extra" : ""}" tabindex="0"
        aria-sort="${col.key === "rank" ? "ascending" : "none"}">${col.label}<span class="sort" aria-hidden="true">▾</span></th>`).join("")}</tr>`;

    const value = (c, col) => (col.sortValue ? col.sortValue(c) : c[col.key]);
    const render = () => {
      const q = state.query.trim().toLowerCase();
      const col = COLUMNS.find((x) => x.key === state.sortKey);
      const shown = rows
        .filter((c) => !state.onlySel || c.ga_selected)
        .filter((c) => !state.zone || c.zone === state.zone)
        .filter((c) => !q || [c.name, c.candidate_id, zone(c.zone).label].some((s) => String(s).toLowerCase().includes(q)))
        .sort((a, b) => {
          const va = value(a, col), vb = value(b, col);
          if (va == null) return 1;
          if (vb == null) return -1;
          return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * state.dir;
        });
      tbody.innerHTML = shown.length
        ? shown.map((c, i) => `<tr data-id="${escape(c.candidate_id)}" class="${c.ga_selected ? "is-selected" : ""}" tabindex="0"
            style="animation-delay:${EV.reduceMotion ? 0 : Math.min(i, 14) * 0.025}s" aria-label="${escape(c.name)}: show on map">
            ${COLUMNS.map((x) => `<td class="${x.num ? "num-col" : ""} ${x.extra ? "extra" : ""}">${x.render(c)}</td>`).join("")}</tr>`).join("")
        : `<tr class="table-empty"><td colspan="${COLUMNS.length}">no site matches — try another search</td></tr>`;
      document.getElementById("table-count").textContent = `showing ${shown.length} of ${rows.length}`;
      thead.querySelectorAll("th").forEach((th) => th.setAttribute("aria-sort",
        th.dataset.key === state.sortKey ? (state.dir === 1 ? "ascending" : "descending") : "none"));
    };

    const sortBy = (key) => {
      if (state.sortKey === key) state.dir *= -1;
      else { state.sortKey = key; state.dir = COLUMNS.find((c) => c.key === key).num && key !== "rank" ? -1 : 1; }
      render();
    };
    thead.addEventListener("click", (e) => { const th = e.target.closest("th"); if (th) sortBy(th.dataset.key); });
    thead.addEventListener("keydown", (e) => { const th = e.target.closest("th"); if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(th.dataset.key); } });

    const open = (tr) => tr && tr.dataset.id && EV.map && EV.map.focusCandidate(tr.dataset.id);
    tbody.addEventListener("click", (e) => open(e.target.closest("tr")));
    tbody.addEventListener("keydown", (e) => { if (e.key === "Enter") open(e.target.closest("tr")); });

    document.getElementById("table-search").addEventListener("input", (e) => { state.query = e.target.value; render(); });
    document.getElementById("only-selected").addEventListener("change", (e) => { state.onlySel = e.target.checked; render(); });
    document.getElementById("more-columns").addEventListener("change", (e) => table.classList.toggle("show-extra", e.target.checked));

    // zone dropdown
    const dd = document.getElementById("zone-filter");
    const btn = dd.querySelector(".dropdown__btn");
    const menu = dd.querySelector(".dropdown__menu");
    const zonesPresent = [...new Set(rows.map((c) => c.zone))];
    const options = [["", "All zones", "transparent"], ...zonesPresent.map((z) => [z, zone(z).label, zone(z).color])];
    menu.innerHTML = options.map(([v, label, c]) =>
      `<li role="option" data-value="${v}" aria-selected="${v === ""}" tabindex="-1" style="--c:${c}"><i></i>${escape(label)}</li>`).join("");
    const setOpen = (o) => { dd.classList.toggle("is-open", o); btn.setAttribute("aria-expanded", o); };
    btn.addEventListener("click", () => setOpen(!dd.classList.contains("is-open")));
    menu.addEventListener("click", (e) => {
      const li = e.target.closest("li");
      if (!li) return;
      state.zone = li.dataset.value;
      btn.querySelector("span").textContent = li.textContent;
      menu.querySelectorAll("li").forEach((x) => x.setAttribute("aria-selected", x === li));
      setOpen(false);
      render();
    });
    document.addEventListener("click", (e) => { if (!dd.contains(e.target)) setOpen(false); });
    dd.addEventListener("keydown", (e) => { if (e.key === "Escape") { setOpen(false); btn.focus(); } });

    render();
  };
})();
