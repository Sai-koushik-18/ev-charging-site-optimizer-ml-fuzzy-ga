/* Hand-drawn charts: fuzzy suitability, method comparison, convergence, capacity, budget, costs, ML error. */
(function () {
  "use strict";
  const { D, fmt, zone, COLORS, escape } = EV;

  /**
   * Horizontal sketch bar chart. On reveal the bars spring in one after another,
   * their hatching sketches itself line by line and the values count up.
   * Returns { play, reorder } so charts can replay and re-sort with motion.
   */
  function barChart(el, items, { max, style, format = fmt.score, onPlay }) {
    el.replaceChildren();
    let played = false;
    items.forEach((it, i) => {
      const row = document.createElement("div");
      row.className = `bar ${it.ga ? "bar--ga" : ""}`;
      row.tabIndex = 0;
      row.innerHTML = `<div class="bar__label">${it.labelHtml}</div><div class="bar__track"><svg aria-hidden="true"></svg></div>
        <div class="bar__value">${escape(EV.reduceMotion ? it.valueText : format(0))}</div>`;
      if (it.tip) EV.bindTooltip(row, it.tip);
      el.appendChild(row);
      Object.assign(it, { row, seed: i + 3, svgEl: row.querySelector("svg"), valueEl: row.querySelector(".bar__value") });
    });

    const draw = () => items.forEach((it) => {
      const box = it.svgEl.parentElement;
      const w = box.clientWidth, h = box.clientHeight;
      if (!w) return;
      it.svgEl.setAttribute("viewBox", `0 0 ${w} ${h}`);
      const g = EV.svg("g", { class: "bar__fill" });
      g.appendChild(EV.rough(it.svgEl).rectangle(1, 3, Math.max(3, (w - 2) * (it.value / max)), h - 6, {
        roughness: 1.15, bowing: 0.8, seed: it.seed, hachureGap: 5, hachureAngle: -41, fillWeight: 1.6, ...style(it),
      }));
      if (!played && !EV.reduceMotion) g.style.opacity = "0";
      it.svgEl.replaceChildren(g);
    });

    function play() {
      played = true;
      items.forEach((it, i) => {
        const g = it.svgEl.querySelector(".bar__fill");
        if (!g) return;
        g.style.opacity = "";
        if (EV.reduceMotion) { it.valueEl.textContent = it.valueText; return; }
        const delay = i * 60;
        g.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
          { duration: 1050, delay, easing: "cubic-bezier(.34,1.32,.64,1)", fill: "backwards" });
        g.querySelectorAll("path").forEach((p, j) => {
          const len = Math.ceil(p.getTotalLength ? p.getTotalLength() : 0);
          if (!len) return;
          p.animate([{ strokeDasharray: `${len}`, strokeDashoffset: `${len}` }, { strokeDasharray: `${len}`, strokeDashoffset: "0" }],
            { duration: 1150, delay: delay + 160 + j * 150, easing: "cubic-bezier(.45,0,.2,1)", fill: "backwards" });
        });
        it.valueEl.textContent = format(0);
        setTimeout(() => EV.countUp(it.valueEl, it.value, format, 950), delay);
      });
      if (onPlay) onPlay(items.length * 60 + 1050);
    }

    /** Re-sort rows; each row glides from its old position to its new one (FLIP). */
    function reorder(compare) {
      const first = new Map(items.map((it) => [it, it.row.getBoundingClientRect().top]));
      items.sort(compare);
      items.forEach((it) => el.appendChild(it.row));
      if (EV.reduceMotion) return;
      items.forEach((it, i) => {
        const dy = first.get(it) - it.row.getBoundingClientRect().top;
        if (dy) it.row.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }],
          { duration: 650, delay: i * 14, easing: "cubic-bezier(.22,1,.36,1)", fill: "backwards" });
      });
    }

    draw();
    new ResizeObserver(EV.debounce(draw, 160)).observe(el);
    EV.onReveal(el.closest(".chart-card"), play);
    return { play, reorder };
  }

  /** Replay button (and optional extra controls) in a chart card's header row. */
  function chartTools(chart, el, extraHtml = "") {
    const card = el.closest(".chart-card");
    const tools = document.createElement("div");
    tools.className = "chart-tools";
    tools.innerHTML = `${extraHtml}<button type="button" class="replay" aria-label="Replay the chart animation"><span aria-hidden="true">↻</span> replay</button>`;
    tools.querySelector(".replay").addEventListener("click", () => chart.play());
    (card.querySelector(".chart-legend, h3") || card).appendChild(tools);
    return tools;
  }

  const zoneDot = (id) => `<i style="--c:${zone(id).color}"></i>`;

  /* ───────── fuzzy suitability ───────── */
  function fuzzyChart() {
    const k = D.ga.n_stations;
    const items = [...D.candidates]
      .sort((a, b) => (b.fuzzy_suitability_score ?? -1) - (a.fuzzy_suitability_score ?? -1))
      .map((c) => {
        const ga = EV.isSelected(c.candidate_id);
        return {
          c, ga, value: c.fuzzy_suitability_score || 0, valueText: fmt.score(c.fuzzy_suitability_score),
          labelHtml: `${zoneDot(c.zone)}<span>${escape(c.name)}</span>${ga ? `<em class="bar__ga">GA #${EV.selectedRank.get(c.candidate_id)}</em>` : ""}`,
          tip: `<b>${escape(c.name)}</b><br>${escape(c.candidate_id)} · ${escape(zone(c.zone).label)}<br>Suitability <b>${fmt.score(c.fuzzy_suitability_score)}</b><br>Demand ${fmt.num(c.predicted_annual_demand_kwh)} kWh/yr${ga ? "<br>✓ chosen by the GA" : ""}`,
        };
      });
    const fuzzyEl = document.getElementById("fuzzy-chart");
    const chart = barChart(fuzzyEl, items, {
      max: 1,
      style: (it) => it.ga
        ? { fill: COLORS.lime, fillStyle: "solid", stroke: COLORS.ink, strokeWidth: 1.6 }
        : { fill: COLORS.purple, fillStyle: "hachure", stroke: COLORS.purple, strokeWidth: 1.2 },
    });

    const sw = (fill, style, stroke) => {
      const s = EV.svg("svg", { viewBox: "0 0 26 14" });
      s.appendChild(EV.rough(s).rectangle(1, 1, 24, 12, { fill, fillStyle: style, stroke, hachureGap: 3, roughness: 1, seed: 4 }));
      return s.outerHTML;
    };
    document.getElementById("fuzzy-legend").innerHTML =
      `<span>${sw(COLORS.lime, "solid", COLORS.ink)}Chosen by the GA</span><span>${sw(COLORS.purple, "hachure", COLORS.purple)}Other candidates</span>
       <span>${Object.values(EV.ZONES).slice(0, 3).map((z) => `<i style="display:inline-block;width:9px;height:9px;border-radius:3px;background:${z.color}"></i>`).join("")} dot = campus zone</span>`;

    const SORTS = {
      score: (a, b) => b.value - a.value,
      zone: (a, b) => zone(a.c.zone).label.localeCompare(zone(b.c.zone).label) || b.value - a.value,
      ga: (a, b) => (EV.selectedRank.get(a.c.candidate_id) ?? 99) - (EV.selectedRank.get(b.c.candidate_id) ?? 99) || b.value - a.value,
    };
    const tools = chartTools(chart, fuzzyEl,
      `<div class="seg seg--inline" role="radiogroup" aria-label="Sort bars">
        <button type="button" role="radio" aria-checked="true" data-sort="score">By score</button>
        <button type="button" role="radio" aria-checked="false" data-sort="zone">By zone</button>
        <button type="button" role="radio" aria-checked="false" data-sort="ga">GA picks first</button></div>`);
    tools.querySelectorAll("[data-sort]").forEach((btn) => btn.addEventListener("click", () => {
      tools.querySelectorAll("[data-sort]").forEach((b) => b.setAttribute("aria-checked", b === btn));
      chart.reorder(SORTS[btn.dataset.sort]);
    }));

    const top = items[0];
    const minSel = Math.min(...items.filter((i) => i.ga).map((i) => i.value));
    const skipped = items.filter((i) => !i.ga && i.value > minSel).length;
    const ann = document.getElementById("fuzzy-annotation");
    ann.innerHTML = `<p>Top score: ${escape(top.c.name)} (${top.valueText})${top.ga ? " — and the GA picked it." : "."}</p>` +
      (skipped ? `<p>The GA skipped ${skipped} higher-scoring site${skipped > 1 ? "s" : ""} to spread its ${k} stations across campus.</p>` : "");
    const arrow = EV.svg("svg", { viewBox: "0 0 120 40" });
    ann.prepend(arrow);
    EV.arrow(arrow, [112, 30], [6, 14], COLORS.orangeInk, 21, 0.25);

    const names = { predicted_demand: "predicted demand", traffic_accessibility: "traffic access", grid_connection_cost: "grid cost",
      land_cost_tier: "land cost", poi_attractiveness: "nearby places", competition_index: "existing chargers" };
    document.getElementById("fuzzy-lede").innerHTML =
      `${D.fuzzy.n_rules} IF–THEN rules combine ${D.fuzzy.inputs.length} inputs — ${D.fuzzy.inputs.map((x) => names[x] || x).join(", ")} — into one score per site. Lime bars are the sites the genetic algorithm chose.`;
  }

  /* ───────── method comparison ───────── */
  const METHOD_LABEL = (m) => m.startsWith("GA") ? "Genetic algorithm"
    : m.startsWith("Exhaustive") ? "Exhaustive search"
    : m.startsWith("Top fuzzy") ? "Top fuzzy score"
    : m.startsWith("Highest ML") ? "Highest ML demand"
    : m.startsWith("Cheapest") ? "Cheapest sites"
    : m.startsWith("Random") ? "Random placement" : m;

  function methodChart() {
    const rows = D.evaluation.methods.filter((m) => m.fitness != null);
    const order = (m) => (m.method.startsWith("GA") ? 0 : m.method.startsWith("Exhaustive") ? 1 : 2);
    rows.sort((a, b) => b.fitness - a.fitness || order(a) - order(b));
    const max = Math.max(...rows.map((r) => r.fitness));
    const items = rows.map((r) => {
      const isGa = r.method.startsWith("GA");
      return {
        ga: isGa, value: r.fitness, valueText: fmt.score(r.fitness), kind: isGa ? "ga" : r.method.startsWith("Exhaustive") ? "opt" : "base",
        labelHtml: `<span>${escape(METHOD_LABEL(r.method))}</span>${isGa ? '<em class="bar__ga">this project</em>' : ""}`,
        tip: `<b>${escape(r.method)}</b><br>Fitness <b>${fmt.score(r.fitness)}</b><br>Demand covered ${fmt.score(r.demand_coverage)} · campus covered ${fmt.pct(r.spatial_coverage * 100, 0)}<br>Cost ${fmt.lakh(r.total_cost_lakhs, 1)} · closest pair ${fmt.num(r.min_spacing_m)} m${r.sites ? `<br>${escape(r.sites)}` : ""}${r.note ? `<br><i>${escape(r.note)}</i>` : ""}`,
      };
    });
    const methodEl = document.getElementById("method-chart");
    const gaRow = rows.find((r) => r.method.startsWith("GA"));
    const exRow = rows.find((r) => r.method.startsWith("Exhaustive"));
    const note = document.createElement("p");
    note.className = "method-note";
    if (gaRow && exRow) {
      const same = Math.abs(gaRow.fitness - exRow.fitness) < 1e-9;
      note.textContent = same
        ? `GA ${fmt.score(gaRow.fitness)} = exhaustive search ${fmt.score(exRow.fitness)} → the GA found the true optimum ✓`
        : `GA ${fmt.score(gaRow.fitness)} vs exhaustive search ${fmt.score(exRow.fitness)}`;
      methodEl.after(note);
    }
    const methodChartCtl = barChart(methodEl, items, {
      onPlay: (ms) => {
        note.classList.remove("is-on");
        setTimeout(() => note.classList.add("is-on"), EV.reduceMotion ? 0 : ms);
      },
      max: max * 1.04,
      style: (it) => it.kind === "ga" ? { fill: COLORS.lime, fillStyle: "solid", stroke: COLORS.lime, strokeWidth: 1.6 }
        : it.kind === "opt" ? { fill: COLORS.blueLight, fillStyle: "cross-hatch", stroke: COLORS.blueLight, strokeWidth: 1.4 }
        : { fill: COLORS.onNight2, fillStyle: "hachure", stroke: COLORS.onNight2, strokeWidth: 1.1 },
    });
    chartTools(methodChartCtl, methodEl);
    const w = D.ga.weights;
    const pc = (x) => Math.round(x * 100);
    document.getElementById("fitness-formula").textContent =
      `Fitness = ${pc(w.demand_coverage)}% demand covered + ${pc(w.spatial_coverage)}% sites within ${D.ga.coverage_radius_m} m of a station + ${pc(w.cost_efficiency)}% cost efficiency − ${pc(w.redundancy_penalty)}% clustering penalty. A plan that breaks the ₹${fmt.num(D.ga.budget_lakhs)} L budget or ${D.ga.min_spacing_m} m spacing scores 0.`;
  }

  /* ───────── convergence ───────── */
  function convergence() {
    const svgEl = document.getElementById("convergence");
    const h = D.ga.fitness_history;
    const draw = () => {
      const W = svgEl.clientWidth || 400, H = 150, padL = 8, padR = 8, padT = 14, padB = 24;
      const lo = Math.min(...h), hi = Math.max(...h);
      const span = hi - lo || 1;
      const pts = h.map((v, i) => [padL + (i / Math.max(1, h.length - 1)) * (W - padL - padR), padT + (1 - (v - lo) / span) * (H - padT - padB)]);
      svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const rc = EV.rough(svgEl);
      svgEl.replaceChildren(
        rc.line(padL, H - padB, W - padR, H - padB, { stroke: COLORS.onNight2, strokeWidth: 1, roughness: 0.8, seed: 2 }),
        rc.linearPath(pts, { stroke: COLORS.lime, strokeWidth: 2.6, roughness: 0.9, seed: 5 }),
        ...[["gen 0", padL, "start"], [`gen ${h.length - 1}`, W - padR, "end"]].map(([t, x, a]) => {
          const tx = EV.svg("text", { x, y: H - 6, "text-anchor": a, fill: COLORS.onNight2, "font-size": 12, "font-weight": 700 });
          tx.textContent = t;
          return tx;
        })
      );
      const end = pts[pts.length - 1];
      svgEl.appendChild(EV.svg("circle", { cx: end[0], cy: end[1], r: 5, fill: COLORS.lime }));
      svgEl.classList.add("draw");
    };
    draw();
    new ResizeObserver(EV.debounce(draw, 160)).observe(svgEl);
    const best = h[h.length - 1];
    const firstBest = h.findIndex((v) => Math.abs(v - best) < 1e-12);
    document.getElementById("convergence-note").textContent =
      `Best run of 10: fitness ${fmt.score(h[0])} at the start, ${fmt.score(best)} by generation ${firstBest}. The run stops once the best plan stops improving (${h.length} generations here).`;
  }

  /* ───────── capacity ───────── */
  function capacity() {
    document.getElementById("capacity").innerHTML = D.ga.capacity.map((r) => {
      const chosen = r.n_stations === D.ga.n_stations;
      return `<li class="${r.feasible ? "ok" : "no"} ${chosen ? "chosen" : ""}" title="${r.feasible ? "A valid plan exists" : "No plan meets the budget and spacing rules"}">
        <b>${r.n_stations}</b><span>${r.feasible ? fmt.lakh(r.total_cost_lakhs, 1) : "no valid plan"}</span><span>${chosen ? "chosen" : r.feasible ? "fits" : "✕"}</span></li>`;
    }).join("");
  }

  /* ───────── budget + cost cards ───────── */
  function costs() {
    const sel = D.candidates.filter((c) => EV.isSelected(c.candidate_id))
      .sort((a, b) => EV.selectedRank.get(a.candidate_id) - EV.selectedRank.get(b.candidate_id));
    const total = sel.reduce((s, c) => s + c.total_capex_inr_lakhs, 0);
    const budget = D.ga.budget_lakhs;
    const maxFit = Math.max(...D.ga.capacity.filter((r) => r.feasible).map((r) => r.n_stations));

    const el = document.getElementById("budget");
    el.innerHTML = `<div class="budget__head"><h3>${fmt.lakh(total, 1)} of a ${fmt.lakh(budget, 0)} budget</h3>
      <p class="budget__note">${fmt.lakh(budget - total, 1)} left · up to ${maxFit} stations would fit</p></div>
      <div class="budget__bar"><svg aria-hidden="true"></svg></div>
      <div class="budget__marks"><span>₹0</span><span>${sel.length} stations · ${fmt.lakh(total, 1)}</span><span>${fmt.lakh(budget, 0)}</span></div>`;
    const svgEl = el.querySelector("svg");
    const draw = () => {
      const W = svgEl.parentElement.clientWidth, H = 54;
      svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const rc = EV.rough(svgEl);
      const g = EV.svg("g", { class: "budget__fill" });
      g.appendChild(rc.rectangle(3, 6, (W - 6) * (total / budget), H - 12, { fill: COLORS.lime, fillStyle: "solid", stroke: COLORS.ink, strokeWidth: 1.6, roughness: 1.2, seed: 8 }));
      svgEl.replaceChildren(g, rc.rectangle(3, 6, W - 6, H - 12, { stroke: COLORS.ink, strokeWidth: 2, roughness: 1.3, seed: 9 }));
    };
    draw();
    new ResizeObserver(EV.debounce(draw, 160)).observe(el);
    EV.observe(el.closest(".reveal") || el);

    const PARTS = [["land_cost_inr_lakhs", "Land", COLORS.amber], ["grid_connection_cost_inr_lakhs", "Grid connection", COLORS.blue],
      ["construction_cost_inr_lakhs", "Construction", COLORS.orange]];
    const cards = document.getElementById("cost-cards");
    cards.innerHTML = sel.map((c, i) => {
      const z = zone(c.zone);
      return `<article class="cost-card reveal" style="--d:${i * 0.08}s">
        <span class="cost-card__rank" aria-label="GA rank ${EV.selectedRank.get(c.candidate_id)}">#${EV.selectedRank.get(c.candidate_id)}</span>
        <h3>${escape(c.name)}</h3><span class="cost-card__zone" style="--c:${z.color}"><i></i>${escape(z.label)} · ${escape(c.candidate_id)}</span>
        <p class="cost-card__total">${fmt.lakh(c.total_capex_inr_lakhs)}</p>
        <div class="cost-split" aria-hidden="true">${PARTS.map(([k, , col], j) => `<i style="width:${(c[k] / c.total_capex_inr_lakhs) * 100}%;background:${col};--d:${0.2 + j * 0.12}s"></i>`).join("")}</div>
        <dl>${PARTS.map(([k, label, col]) => `<dt><b style="--c:${col}"></b>${label}</dt><dd>${fmt.lakh(c[k])}</dd>`).join("")}
          <dt><b style="--c:transparent"></b>Total CAPEX</dt><dd>${fmt.lakh(c.total_capex_inr_lakhs)}</dd></dl></article>`;
    }).join("");
    cards.querySelectorAll(".cost-card").forEach((card, i) => { EV.sketchBorder(card, { seed: 30 + i, stroke: "rgba(21,23,43,.55)" }); EV.observe(card); });
  }

  /* ───────── minimum budget ───────── */
  function minBudget() {
    const el = document.getElementById("min-budget");
    const rows = D.min_budget || [];
    if (!rows.length) { el.remove(); return; }
    const budget = D.ga.budget_lakhs, k = D.ga.n_stations;
    const possible = rows.filter((r) => r.possible);
    const impossible = rows.find((r) => !r.possible);
    const label = (r) => (r.n_stations === k ? "chosen" : r.min_cost_lakhs <= budget ? "fits budget" : "over budget");
    el.innerHTML = `<div class="minbudget__head"><h3>Minimum budget</h3>
      <p>The cheapest plan that keeps stations at least ${D.ga.min_spacing_m} m apart, for each number of stations. Nothing can be built below <b>${fmt.lakh(possible[0].min_cost_lakhs)}</b>.</p></div>
      <ol class="minbudget__list">${possible.map((r) => `<li class="${r.min_cost_lakhs <= budget ? "fits" : "over"} ${r.n_stations === k ? "chosen" : ""}" tabindex="0">
        <b>${r.n_stations}</b><span>${fmt.lakh(r.min_cost_lakhs)}</span><em>${label(r)}</em></li>`).join("")}
        ${impossible ? `<li class="impossible"><b>${impossible.n_stations}+</b><span>not possible</span><em>sites too close</em></li>` : ""}</ol>`;
    el.querySelectorAll("li[tabindex]").forEach((li, i) => {
      const r = possible[i];
      EV.bindTooltip(li, `<b>${r.n_stations} station${r.n_stations > 1 ? "s" : ""}: ${fmt.lakh(r.min_cost_lakhs)}</b><br>${escape(r.sites)}`);
    });
  }

  /* ───────── ML accuracy ───────── */
  function ml() {
    const m = D.ml;
    const base = m.baseline_rmse, tuned = m.ga_tuned_rf.test_rmse;
    const def = m.default_rf ? m.default_rf.test_rmse : null;
    const items = [
      { key: "base", value: base, label: "Simple average", sub: "site × hour × weekend" },
      def != null && { key: "def", value: def, label: "Default Random Forest", sub: `R² ${fmt.score(m.default_rf.test_r2)}` },
      { key: "ga", value: tuned, label: "GA-tuned Random Forest", sub: `R² ${fmt.score(m.ga_tuned_rf.test_r2)}`, ga: true },
    ].filter(Boolean).map((it) => ({
      ...it, valueText: fmt.score(it.value),
      labelHtml: `<span>${it.label}<br><small class="muted">${it.sub}</small></span>`,
      tip: `<b>${it.label}</b><br>Test RMSE <b>${fmt.score(it.value)}</b> kWh/h`,
    }));
    const mlEl = document.getElementById("ml-chart");
    const mlChart = barChart(mlEl, items, {
      max: Math.max(base, def ?? 0, tuned) * 1.08,
      style: (it) => it.key === "ga" ? { fill: COLORS.purple, fillStyle: "solid", stroke: COLORS.ink, strokeWidth: 1.6 }
        : it.key === "def" ? { fill: COLORS.blue, fillStyle: "hachure", stroke: COLORS.blue, strokeWidth: 1.2 }
        : { fill: COLORS.grey, fillStyle: "hachure", stroke: COLORS.grey, strokeWidth: 1.2 },
    });
    chartTools(mlChart, mlEl);

    const vsBase = ((base - tuned) / base) * 100;
    const vsDef = def != null ? ((def - tuned) / def) * 100 : null;
    const big = document.getElementById("ml-improve");
    big.innerHTML = `<div class="improve__big"><span id="ml-pct">0.0%</span></div>
      <p>lower test error than a simple average for the GA-tuned forest.</p>
      <div class="improve__row">${vsDef != null ? `<span class="improve__chip">vs default forest <b>−${fmt.pct(vsDef)}</b></span>` : ""}
      <span class="improve__chip">test R² <b>${fmt.score(m.ga_tuned_rf.test_r2)}</b></span></div>`;
    EV.onReveal(big, () => EV.countUp(document.getElementById("ml-pct"), vsBase, (v) => fmt.pct(v)));
    EV.observe(big);

    const p = m.ga_tuned_params || {};
    document.getElementById("ml-info").innerHTML = `<h3>How it was tested</h3><dl>
      <dt>Training data</dt><dd>${escape(m.training_data)}</dd>
      <dt>Split</dt><dd>${escape(m.split)}</dd>
      ${m.settings ? `<dt>Settings</dt><dd>${escape(m.settings)}</dd>` : ""}
      <dt>Train / test RMSE</dt><dd>${fmt.score(m.ga_tuned_rf.train_rmse)} / ${fmt.score(m.ga_tuned_rf.test_rmse)} kWh/h</dd>
      <dt>GA-chosen settings</dt><dd><span class="params">${Object.entries(p).map(([k, v]) => `<span>${escape(k.replaceAll("_", " "))}: ${escape(v ?? "none")}</span>`).join("")}</span></dd></dl>`;

    document.getElementById("ml-lede").textContent =
      "A Random Forest predicts hourly charging demand from time, weather and location. A small genetic algorithm searched its settings. Every model is scored on the last 20% of 2024, which it never saw during training.";

    const cav = document.getElementById("ml-caveat");
    cav.innerHTML = `<p><strong>Synthetic demand, real method.</strong> These errors come from the project’s synthetic demand history (${escape(m.training_data)}).
      They show the modelling method works on that data; they are <strong>not</strong> real-world validated accuracy, because ${escape(EV.place.short)} has no public charging records.</p>`;
    cav.prepend(EV.icon("check", COLORS.orangeInk, 40));
    cav.firstChild.classList.add("caveat__icon");
  }

  EV.initCharts = function () {
    fuzzyChart();
    methodChart();
    convergence();
    capacity();
    costs();
    minBudget();
    ml();
  };
})();
