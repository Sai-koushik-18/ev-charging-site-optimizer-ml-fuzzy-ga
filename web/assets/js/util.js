/* Shared helpers: data access, formatting, hand-drawn sketches, reveals, counters, tooltip. */
(function () {
  "use strict";

  const D = window.DASHBOARD_DATA;
  const EV = (window.EV = { D });
  EV.place = Object.assign({ slug: "vit-ap", name: "VIT-AP University", short: "VIT-AP", chargers_scope: "in Andhra Pradesh" },
    (D && D.meta && D.meta.place) || {});

  EV.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ───────── data helpers ───────── */
  const selectedRank = new Map((D.selected || []).map((s) => [s.candidate_id, s.ga_rank]));
  EV.selectedRank = selectedRank;
  EV.isSelected = (id) => selectedRank.has(id);
  EV.byId = new Map(D.candidates.map((c) => [c.candidate_id, c]));

  EV.ZONES = {
    academic: { label: "Academic", color: "#3d73ff" },
    residential: { label: "Residences", color: "#e25c9c" },
    admin: { label: "Administration", color: "#8b5cf6" },
    hostel_boys: { label: "Men’s hostels", color: "#ff7a2f" },
    hostel_girls: { label: "Ladies’ hostels", color: "#e25c9c" },
    commercial: { label: "Commercial", color: "#f2b33d" },
    sports: { label: "Sports", color: "#2e9e6b" },
    gate: { label: "Entry points", color: "#00a3a3" },
    campus_general: { label: "Rest of campus", color: "#9a9486" },
  };
  EV.zone = (id) => EV.ZONES[id] || { label: id || "—", color: "#9a9486" };

  EV.COLORS = {
    ink: "#15172b", paper: "#f5efe3", card: "#fbf8f1", night: "#10132a", night2: "#191d3c",
    onNight: "#efedf8", onNight2: "#b9bcd6",
    blue: "#3d73ff", blueLight: "#86a8ff", lime: "#b6f05a", limeInk: "#44700c",
    orange: "#ff7a2f", orangeInk: "#b8490b", purple: "#8b5cf6", purpleLight: "#bba2ff",
    grey: "#9a9486", amber: "#f2b33d",
  };

  /* ───────── formatting (matches the original dashboard) ───────── */
  const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  EV.fmt = {
    num: (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : nf(d).format(v)),
    lakh: (v, d = 2) => (v == null ? "—" : `₹${nf(d).format(v)} L`),
    score: (v) => (v == null ? "—" : Number(v).toFixed(3)),
    coord: (v) => (v == null ? "—" : Number(v).toFixed(6)),
    pct: (v, d = 1) => `${nf(d).format(v)}%`,
  };
  EV.escape = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ───────── hand-drawn sketches (Rough.js) ───────── */
  const SVGNS = "http://www.w3.org/2000/svg";
  EV.svg = (tag, attrs = {}) => {
    const el = document.createElementNS(SVGNS, tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  };
  EV.rough = (svgEl) => window.rough.svg(svgEl);

  /** Draw a sketchy border behind/around an element; redraws when it resizes. */
  EV.sketchBorder = (el, opts = {}) => {
    const holder = document.createElement("div");
    holder.className = "sketch";
    holder.setAttribute("aria-hidden", "true");
    const svgEl = EV.svg("svg");
    holder.appendChild(svgEl);
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    el.appendChild(holder);
    const draw = () => {
      const { width, height } = el.getBoundingClientRect();
      if (!width || !height) return;
      svgEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svgEl.replaceChildren(
        EV.rough(svgEl).rectangle(2, 2, width - 4, height - 4, {
          stroke: opts.stroke || EV.COLORS.ink, strokeWidth: opts.width || 1.4,
          roughness: opts.roughness ?? 1.2, bowing: 1.2, seed: opts.seed || 7,
        })
      );
    };
    draw();
    new ResizeObserver(draw).observe(el);
  };

  /** Hand-drawn underline beneath inline text (used by .hand-u and the hero scribble). */
  EV.underline = (el, color, seed = 3) => {
    let svgEl = el.querySelector("svg");
    if (!svgEl) { svgEl = EV.svg("svg"); el.appendChild(svgEl); }
    svgEl.setAttribute("aria-hidden", "true");
    svgEl.setAttribute("viewBox", "0 0 200 24");
    svgEl.setAttribute("preserveAspectRatio", "none");
    svgEl.classList.add("draw");
    const rc = EV.rough(svgEl);
    svgEl.replaceChildren(
      rc.curve([[2, 16], [50, 9], [110, 15], [198, 7]], { stroke: color, strokeWidth: 3.2, roughness: 1.4, seed }),
      rc.curve([[14, 20], [80, 14], [150, 19], [192, 13]], { stroke: color, strokeWidth: 2, roughness: 1.6, seed: seed + 5 })
    );
    svgEl.querySelectorAll("path").forEach((p) => p.style.setProperty("--len", Math.ceil(p.getTotalLength?.() || 400)));
  };

  /** Hand-drawn arrow from a to b in an SVG's own coordinate space. */
  EV.arrow = (svgEl, a, b, color, seed = 11, bend = 0.25) => {
    const rc = EV.rough(svgEl);
    const mx = (a[0] + b[0]) / 2 + (b[1] - a[1]) * bend;
    const my = (a[1] + b[1]) / 2 - (b[0] - a[0]) * bend;
    const ang = Math.atan2(b[1] - my, b[0] - mx);
    const h = 12;
    const o = { stroke: color, strokeWidth: 2.4, roughness: 1.3, seed };
    svgEl.append(
      rc.curve([a, [mx, my], b], o),
      rc.linearPath([[b[0] - h * Math.cos(ang - 0.45), b[1] - h * Math.sin(ang - 0.45)], b,
        [b[0] - h * Math.cos(ang + 0.45), b[1] - h * Math.sin(ang + 0.45)]], o)
    );
    svgEl.classList.add("draw");
    svgEl.querySelectorAll("path").forEach((p) => p.style.setProperty("--len", Math.ceil(p.getTotalLength?.() || 300)));
  };

  /** Small hand-drawn icons from simple path data (viewBox 0 0 48 48). */
  const ICONS = {
    map: "M6 12 18 7l12 5 12-5v29l-12 5-12-5-12 5z M18 7v29 M30 12v29",
    brain: "M24 40V10 M24 10c-6-6-16-2-15 6-6 2-6 11 0 13-2 7 7 12 15 8 M24 10c6-6 16-2 15 6 6 2 6 11 0 13 2 7-7 12-15 8",
    sliders: "M8 14h32 M8 24h32 M8 34h32 M16 10v8 M30 20v8 M20 30v8",
    dna: "M14 6c0 12 20 12 20 24s-20 12-20 12 M34 6c0 12-20 12-20 24s20 12 20 12 M16 14h16 M16 34h16 M19 24h10",
    check: "M24 5 41 11v12c0 10-7 17-17 20C14 40 7 33 7 23V11z M16 24l6 6 11-12",
    bolt: "M27 4 10 27h12l-3 17 19-25H26z",
    pin: "M24 44S10 29 10 19a14 14 0 1 1 28 0c0 10-14 25-14 25z M24 13v11 M19 19h10",
    coin: "M24 8a16 16 0 1 0 0 32 16 16 0 1 0 0-32 M24 15v18 M19 19c0-3 10-3 10 0s-10 3-10 7 10 4 10 0",
    plug: "M18 6v10 M30 6v10 M13 16h22v8c0 7-5 11-11 11s-11-4-11-11z M24 35v8",
  };
  EV.icon = (name, color, seed = 2) => {
    const s = EV.svg("svg", { viewBox: "0 0 48 48", "aria-hidden": "true" });
    const rc = EV.rough(s);
    ICONS[name].split(" M").forEach((seg, i) => {
      s.appendChild(rc.path(i ? "M" + seg : seg, { stroke: color, strokeWidth: 2.4, roughness: 0.9, seed: seed + i }));
    });
    return s;
  };

  /* ───────── reveal on scroll ───────── */
  const revealCallbacks = new WeakMap();
  EV.onReveal = (el, fn) => revealCallbacks.set(el, fn);
  const io = new IntersectionObserver(
    (entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("is-in");
      const fn = revealCallbacks.get(e.target);
      if (fn) { fn(); revealCallbacks.delete(e.target); }
      io.unobserve(e.target);
    }),
    { threshold: 0.15, rootMargin: "0px 0px -6% 0px" }
  );
  EV.observe = (el) => {
    if (EV.reduceMotion) { el.classList.add("is-in"); const fn = revealCallbacks.get(el); if (fn) fn(); return; }
    io.observe(el);
  };

  /* ───────── animated counters ───────── */
  EV.countUp = (el, value, format, duration = 1400) => {
    if (EV.reduceMotion) { el.textContent = format(value); return; }
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 4);
      el.textContent = format(t < 1 ? value * eased : value);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  /* ───────── tooltip ───────── */
  const tip = document.getElementById("tooltip");
  EV.tooltip = {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.hidden = false;
      const pad = 12;
      const half = tip.offsetWidth / 2;
      tip.style.left = `${Math.min(window.innerWidth - half - pad, Math.max(half + pad, x))}px`;
      tip.style.top = `${Math.max(tip.offsetHeight + 20, y)}px`;
      tip.classList.add("is-on");
    },
    hide() { tip.classList.remove("is-on"); },
  };
  EV.bindTooltip = (el, html) => {
    el.addEventListener("mousemove", (e) => EV.tooltip.show(typeof html === "function" ? html() : html, e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => EV.tooltip.hide());
    el.addEventListener("focus", () => { const r = el.getBoundingClientRect(); EV.tooltip.show(typeof html === "function" ? html() : html, r.left + r.width / 2, r.top); });
    el.addEventListener("blur", () => EV.tooltip.hide());
  };

  EV.debounce = (fn, ms = 150) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
})();
