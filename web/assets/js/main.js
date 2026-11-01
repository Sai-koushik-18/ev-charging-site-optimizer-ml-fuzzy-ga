/* Page orchestration: hero, method steps, provenance, nav, reveals. */
(function () {
  "use strict";
  const { D, fmt, COLORS, escape } = EV;

  const sel = D.candidates.filter((c) => EV.isSelected(c.candidate_id));
  const totalCapex = sel.reduce((s, c) => s + c.total_capex_inr_lakhs, 0);
  const budget = D.ga.budget_lakhs;
  const summary = D.evaluation.summary;
  const optimum = summary.ga_matches_optimum === true;
  const nZones = new Set(D.candidates.map((c) => c.zone)).size;
  const exported = new Date(D.meta.generated_at);
  const dateText = exported.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const place = EV.place;

  function hero() {
    document.title = `EV Charging Location Optimizer — ${place.short}`;
    document.getElementById("hero-eyebrow").textContent = `${place.name} · EV charging infrastructure study`;
    document.getElementById("brand-place").textContent = ` · ${place.short}`;
    document.getElementById("hero-lede").innerHTML =
      `Where should ${escape(place.short)} build its first EV chargers? We turn real OpenStreetMap data into <strong>${D.candidates.length} candidate sites</strong>, predict demand with a GA-tuned Random Forest, score suitability with <strong>${D.fuzzy.n_rules} fuzzy rules</strong>, and let a genetic algorithm choose <strong>${EV.count(D.ga.n_stations, "station")}</strong> within a <strong>${fmt.lakh(budget, 0)}</strong> budget.`;

    document.getElementById("hero-status").innerHTML =
      `<span class="dot"></span><span>Pipeline complete</span><span class="status__sep">/</span>
       <span>${optimum ? "GA plan = verified optimum" : `GA plan beats ${fmt.pct((summary.ga_beats_share_of_valid_plans || 0) * 100, 0)} of valid plans`}</span>
       <span class="status__sep">/</span><span>Data from ${escape(dateText)}</span>`;
    document.getElementById("nav-status").innerHTML = `<span class="dot"></span><span>${optimum ? "Optimum verified" : "Pipeline complete"}</span>`;

    const KPIS = [
      { label: "Total candidates", value: D.candidates.length, f: (v) => fmt.num(v), note: "from real OSM features", icon: "pin", color: COLORS.blueLight },
      { label: "Stations selected", value: sel.length, f: (v) => fmt.num(v), note: "picked by the GA", icon: "bolt", color: COLORS.lime, lime: true },
      { label: "Total CAPEX", value: totalCapex, f: (v) => fmt.lakh(v, 1), note: `of a ${fmt.lakh(budget, 0)} budget`, icon: "coin", color: COLORS.orange },
      { label: "Remaining budget", value: budget - totalCapex, f: (v) => fmt.lakh(v, 1), note: "still unspent", icon: "check", color: COLORS.purpleLight },
    ];
    const wrap = document.getElementById("kpis");
    KPIS.forEach((k, i) => {
      const card = document.createElement("article");
      card.className = `kpi reveal ${k.lime ? "kpi--lime" : ""}`;
      card.style.setProperty("--d", `${0.15 + i * 0.08}s`);
      card.innerHTML = `<p class="kpi__label">${k.label}</p><p class="kpi__value num" data-final="${escape(k.f(k.value))}">${k.f(0)}</p><p class="kpi__note">${k.note}</p>`;
      const icon = EV.icon(k.icon, k.color, 10 + i);
      icon.classList.add("kpi__icon");
      card.prepend(icon);
      wrap.appendChild(card);
      EV.sketchBorder(card, { stroke: "rgba(239,237,248,.42)", seed: 50 + i });
      EV.onReveal(card, () => EV.countUp(card.querySelector(".kpi__value"), k.value, k.f));
    });

    EV.underline(document.getElementById("hero-scribble"), COLORS.lime, 4);
    const arrow = document.getElementById("hero-arrow");
    arrow.setAttribute("viewBox", "0 0 230 96");
    EV.arrow(arrow, [214, 6], [8, 88], COLORS.orange, 13, 0.3);

    const contours = document.getElementById("hero-contours");
    const rc = EV.rough(contours);
    for (let i = 0; i < 9; i++) {
      const y = 70 + i * 72;
      const pts = Array.from({ length: 7 }, (_, j) => [j * 200, y + Math.sin(j * 1.3 + i) * 34 + (j % 2 ? 18 : -10)]);
      contours.appendChild(rc.curve(pts, { stroke: i % 3 ? COLORS.blueLight : COLORS.purpleLight, strokeWidth: 1, roughness: 1.6, seed: 60 + i }));
    }
    [["doodle-plug", "plug", COLORS.lime], ["doodle-bolt", "bolt", COLORS.orange], ["doodle-pin", "pin", COLORS.blueLight]].forEach(([id, name, c], i) => {
      const el = document.getElementById(id);
      const icon = EV.icon(name, c, 70 + i);
      icon.id = id;
      icon.setAttribute("class", el.getAttribute("class"));
      el.replaceWith(icon);
    });
  }

  function steps() {
    const ml = D.ml.ga_tuned_rf;
    const STEPS = [
      { title: "Map", sub: "OpenStreetMap", text: `Campus boundary, buildings, roads and parking become ${D.candidates.length} candidate sites in ${nZones} zones.`, stat: `${D.candidates.length} sites`, icon: "map", color: COLORS.blue },
      { title: "Predict", sub: "GA-tuned Random Forest", text: "Learns hourly charging demand from time, weather and location.", stat: `test RMSE ${fmt.score(ml.test_rmse)}`, icon: "brain", color: COLORS.purple },
      { title: "Score", sub: "Fuzzy logic", text: `${D.fuzzy.inputs.length} inputs and ${D.fuzzy.n_rules} expert rules give every site a score from 0 to 1.`, stat: `${D.fuzzy.n_rules} rules`, icon: "sliders", color: COLORS.purple },
      { title: "Optimise", sub: "Genetic algorithm", text: `Evolves plans of ${EV.count(D.ga.n_stations, "station")} under hard budget and spacing rules.`, stat: `≤ ${fmt.lakh(budget, 0)} · ≥ ${D.ga.min_spacing_m} m`, icon: "dna", color: COLORS.limeInk },
      { title: "Prove", sub: "Exhaustive search", text: "Scores every possible plan to check the GA’s answer.", stat: `${fmt.num(summary.n_combinations)} plans`, icon: "check", color: COLORS.orange },
    ];
    const ol = document.getElementById("steps");
    STEPS.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "step reveal";
      li.style.setProperty("--d", `${i * 0.09}s`);
      li.style.setProperty("--step-color", s.color);
      li.innerHTML = `<span class="step__num">0${i + 1}</span><h3>${s.title}</h3><p><b>${s.sub}.</b> ${s.text}</p><span class="step__stat">${escape(s.stat)}</span>`;
      const icon = EV.icon(s.icon, s.color, 80 + i);
      icon.classList.add("step__icon");
      li.querySelector("h3").before(icon);
      if (i < STEPS.length - 1) {
        const a = EV.svg("svg", { class: "step__arrow", viewBox: "0 0 40 30", "aria-hidden": "true" });
        EV.arrow(a, [2, 20], [36, 12], COLORS.ink, 90 + i, 0.35);
        li.appendChild(a);
      }
      ol.appendChild(li);
      EV.sketchBorder(li, { seed: 100 + i, stroke: "rgba(21,23,43,.5)" });
    });
  }

  function verdict() {
    const k = D.ga.n_stations;
    document.getElementById("opt-combos").textContent = fmt.num(summary.n_combinations);
    document.getElementById("opt-lede").textContent =
      `Every method below picks ${EV.count(k, "station")} under the same budget and spacing rules and is scored with the same fitness function. With ${summary.n_sites} sites there are only ${fmt.num(summary.n_combinations)} possible plans, so we scored all of them to find the true best.`;
    const el = document.getElementById("verdict");
    el.innerHTML = `<div class="stamp" aria-hidden="true"><svg></svg><div><b>${optimum ? "TRUE<br>OPTIMUM" : "NEAR<br>OPTIMUM"}</b><small>${optimum ? "verified ✓" : "checked"}</small></div></div>
      <div><h3>${optimum ? "The genetic algorithm found the single best plan." : "The genetic algorithm beats most valid plans."}</h3>
      <p>There are ${fmt.num(summary.n_combinations)} ways to choose ${k} of ${summary.n_sites} sites. Only ${fmt.num(summary.n_valid_plans)} respect the ${fmt.lakh(budget, 0)} budget and ${D.ga.min_spacing_m} m spacing. Scoring every one of them ${optimum ? "confirms the GA’s plan ranks first" : `puts the GA ahead of ${fmt.pct(summary.ga_beats_share_of_valid_plans * 100, 0)} of them`}.</p>
      <div class="funnel"><span class="funnel__step"><b>${fmt.num(summary.n_combinations)}</b>possible plans</span><span class="funnel__arrow">→</span>
        <span class="funnel__step"><b>${fmt.num(summary.n_valid_plans)}</b>follow the rules</span><span class="funnel__arrow">→</span>
        <span class="funnel__step funnel__step--win"><b>1</b>${optimum ? "best = the GA’s pick" : "best plan"}</span></div></div>`;
    const stampSvg = el.querySelector(".stamp svg");
    stampSvg.setAttribute("viewBox", "0 0 160 160");
    const rc = EV.rough(stampSvg);
    stampSvg.append(
      rc.circle(80, 80, 150, { stroke: COLORS.lime, strokeWidth: 3, roughness: 1.5, seed: 120 }),
      rc.circle(80, 80, 128, { stroke: COLORS.lime, strokeWidth: 1.5, roughness: 1.8, seed: 121 })
    );
  }

  function provenance() {
    const g = D.geo;
    const n = (x) => fmt.num((x && x.features && x.features.length) || 0);
    const ITEMS = [
      ["REAL", "Boundary & buildings", `OpenStreetMap: the ${escape(place.short)} outline and ${n(g.buildings)} mapped buildings.`],
      ["REAL", "Campus roads & parking", `OpenStreetMap: ${n(g.roads)} road segments and ${n(g.parking)} parking lots on campus.`],
      ["REAL", "Hourly weather, 2024", "Open-Meteo archive: temperature, humidity and rain feed the demand model."],
      ["REAL", "Existing public chargers", `OpenChargeMap: ${n(g.chargers)} chargers ${escape(place.chargers_scope)}.`],
      ["DERIVED", "Zones & candidate sites", `${nZones} zones and ${D.candidates.length} sites built from OSM geometry (parking lots, roadside spots, entry points).`],
      ...(D.ml && D.ml.ev_adoption ? [["REAL", "EV adoption", (() => {
        const a = D.ml.ev_adoption;
        return `Vahan (MoRTH): ${fmt.pct(a.ev_share * 100, 2)} of new vehicles registered in ${a.state} in ${a.year} were electric (${fmt.num(a.ev_registrations)} of ${fmt.num(a.all_registrations)}). Demand is scaled to it.`;
      })()]] : []),
      ["SYNTHETIC", "Charging demand history", `Statistical model of hourly demand${D.ml && D.ml.ev_adoption ? `, sized by the real EV share` : ""}; ${place.short} has no public charging records.`],
      ["SYNTHETIC", "Land, construction & grid costs", `Rates from config/settings.yaml. Grid: ${place.grid_note || "the OSM-mapped campus power building"}.`],
    ];
    const el = document.getElementById("provenance");
    el.innerHTML = ITEMS.map(([tag, title, text], i) =>
      `<article class="prov reveal" style="--d:${(i % 3) * 0.08}s"><span class="prov__tag prov__tag--${tag}">${tag}</span><h3>${escape(title)}</h3><p>${escape(text)}</p></article>`).join("");
  }

  function chargerCaption() {
    const feats = (D.geo.chargers && D.geo.chargers.features) || [];
    if (!feats.length) return;
    const d = feats.map((f) => f.properties.distance_to_campus_km);
    const nearest = feats.reduce((a, b) => (a.properties.distance_to_campus_km <= b.properties.distance_to_campus_km ? a : b)).properties;
    const near3 = D.candidates.filter((c) => (c.existing_chargers_3km || 0) > 0).length;
    const bands = place.region ? [50, 100, 300] : [1, 3, 5];
    document.getElementById("charger-caption").innerHTML =
      `<strong>${feats.length}</strong> public chargers ${escape(place.chargers_scope)} (OpenChargeMap): ${bands.map((km) => `${d.filter((x) => x <= km).length} within ${km} km`).join(", ")} of the area. The nearest is <strong>${fmt.num(nearest.distance_to_campus_km, 1)} km</strong> away (${escape(nearest.name)}, ${escape(nearest.town)}). ${near3 ? `${near3} candidate sites have a charger within 3 km.` : "None are within 3 km of any candidate site."}`;
  }

  function underlines() {
    const map = { "hand-u--lime": COLORS.lime, "hand-u--orange": COLORS.orange, "hand-u--purple": COLORS.purple };
    document.querySelectorAll(".hand-u").forEach((el, i) => {
      const cls = [...el.classList].find((c) => map[c]);
      EV.underline(el, cls ? map[cls] : COLORS.orange, 140 + i);
    });
  }

  function nav() {
    const navEl = document.getElementById("nav");
    const dark = [...document.querySelectorAll(".hero, .section--ink, .footer")];
    const update = () => {
      navEl.classList.toggle("is-solid", window.scrollY > 40);
      const y = navEl.offsetHeight / 2;
      navEl.classList.toggle("on-night", dark.some((s) => { const r = s.getBoundingClientRect(); return r.top <= y && r.bottom >= y; }));
    };
    update();
    window.addEventListener("scroll", update, { passive: true });

    const links = new Map([...navEl.querySelectorAll(".nav__links a")].map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.classList.remove("is-active"));
      const id = e.target.id === "suitability" ? "ranking" : ["costs"].includes(e.target.id) ? "optimisation" : e.target.id;
      links.get(id)?.classList.add("is-active");
    }), { rootMargin: "-45% 0px -50% 0px" });
    document.querySelectorAll("main section[id]").forEach((s) => io.observe(s));
  }

  function placeSwitch() {
    const list = window.EV_PLACES || [];
    const dd = document.getElementById("place-switch");
    document.getElementById("place-current").textContent = place.short;
    if (list.length < 2) { dd.classList.add("is-single"); }
    const btn = dd.querySelector(".dropdown__btn");
    const menu = dd.querySelector(".dropdown__menu");
    menu.innerHTML = list.map((p) => {
      const href = p.slug === "vit-ap" ? "index.html" : `index.html?place=${encodeURIComponent(p.slug)}`;
      return `<li role="option" aria-selected="${p.slug === place.slug}"><a href="${href}">${escape(p.name)}${p.budget ? `<small>₹${fmt.num(p.budget)} L budget</small>` : ""}</a></li>`;
    }).join("");
    const setOpen = (o) => { dd.classList.toggle("is-open", o); btn.setAttribute("aria-expanded", o); };
    btn.addEventListener("click", () => setOpen(!dd.classList.contains("is-open")));
    document.addEventListener("click", (e) => { if (!dd.contains(e.target)) setOpen(false); });
    dd.addEventListener("keydown", (e) => { if (e.key === "Escape") { setOpen(false); btn.focus(); } });
  }

  function footer() {
    document.getElementById("footer-meta").textContent =
      `Built from the pipeline outputs exported ${dateText} · ${D.candidates.length} candidates · ${D.evaluation.methods.length} methods compared · numbers are read from data/ and outputs/, never typed in.`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!window.DASHBOARD_DATA) {
      document.body.innerHTML = '<p class="noscript">Missing web/data/dashboard-data.js — run <code>python run_pipeline.py web</code>.</p>';
      return;
    }
    hero();
    placeSwitch();
    steps();
    verdict();
    provenance();
    chargerCaption();
    footer();
    EV.initMap();
    EV.initTable();
    EV.initCharts();
    underlines();
    nav();
    document.querySelectorAll(".reveal").forEach((el) => EV.observe(el));
  });
})();
