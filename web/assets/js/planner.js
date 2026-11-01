/* Live planner: send a place and budget to the API, show progress, then open the result on this page. */
(function () {
  "use strict";
  const { escape, fmt } = EV;
  const API = window.EV_API || "";
  const INDEX_KEY = "ev-live-index";
  const RUNNING_KEY = "ev-live-running";
  const KEEP_SAVED = 3;   // full results kept in this browser (each is a few hundred KB)
  const KEEP_LISTED = 8;  // recent plans listed (older ones reload from the server while it still has them)
  const STAGES = [
    [0.0, "Area & OpenStreetMap"], [0.15, "Candidate sites"], [0.22, "ML demand"],
    [0.45, "Fuzzy scores"], [0.5, "GA plan"], [0.87, "Proof"],
  ];

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  const session = {
    get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } },
    del(k) { try { sessionStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  const readIndex = () => { try { return JSON.parse(store.get(INDEX_KEY)) || []; } catch (e) { return []; } };
  const liveHref = (id) => `index.html?live=${encodeURIComponent(id)}`;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* Save a finished plan: list it, and keep the newest few full results so they open offline. */
  function remember(entry, data) {
    const index = [entry, ...readIndex().filter((e) => e.id !== entry.id)].slice(0, KEEP_LISTED);
    index.slice(KEEP_SAVED).forEach((e) => store.del("ev-live:" + e.id));
    store.set(INDEX_KEY, JSON.stringify(index));
    const text = JSON.stringify(data);
    for (let i = Math.min(index.length, KEEP_SAVED) - 1; i >= 0; i--) {
      if (store.set("ev-live:" + entry.id, text)) return;
      if (i > 0) store.del("ev-live:" + index[i].id); // make room by dropping the oldest saved result
    }
  }

  async function api(path, opts = {}, timeout = 15000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeout);
    try {
      const res = await fetch(API + path, { ...opts, signal: ctl.signal });
      let body = null;
      try { body = await res.json(); } catch (e) { /* not JSON: the Space is still starting */ }
      return { ok: res.ok, status: res.status, body };
    } finally {
      clearTimeout(t);
    }
  }

  function setServer(state, text) {
    const el = $("plan-server");
    el.dataset.state = state;
    el.innerHTML = `<span class="dot"></span>${text}`;
  }

  async function checkServer() {
    if (!API) {
      setServer("off", "The live planner is not connected in this copy of the site. Run plans from the command line instead (see below).");
      $("plan-submit").disabled = true;
      return;
    }
    setServer("wait", "Checking the planner server…");
    try {
      const r = await api("/api/health", {}, 9000);
      if (r.ok && r.body && r.body.ok) {
        const busy = r.body.active_jobs;
        setServer("on", `Planner online${busy ? ` · ${busy} plan${busy > 1 ? "s" : ""} in progress` : ""} · areas up to ${fmt.num(r.body.max_area_km2)} km²`);
        return;
      }
    } catch (e) { /* asleep or offline */ }
    setServer("wait", "The free server is asleep. It wakes up when you press Plan it (about a minute).");
  }

  /* ───────── progress view ───────── */
  let timer = null;
  function showRun(startedAt) {
    $("plan-run").hidden = false;
    $("plan-error").hidden = true;
    $("plan-stages").innerHTML = STAGES.map(([, label]) => `<li>${label}</li>`).join("");
    clearInterval(timer);
    timer = setInterval(() => {
      const s = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      $("plan-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 500);
  }
  function progress(frac, msg) {
    $("plan-bar").style.width = `${Math.max(3, frac * 100).toFixed(1)}%`;
    $("plan-msg").textContent = msg;
    const items = $("plan-stages").children;
    STAGES.forEach(([start], i) => {
      const next = STAGES[i + 1] ? STAGES[i + 1][0] : 1.01;
      items[i].className = frac >= next ? "is-done" : frac >= start && frac > 0 ? "is-now" : "";
    });
  }
  function fail(text) {
    clearInterval(timer);
    session.del(RUNNING_KEY);
    $("plan-run").hidden = true;
    $("plan-error").hidden = false;
    $("plan-error").innerHTML = `<b>Could not plan this place.</b> ${escape(text)}`;
    lock(false);
  }
  function lock(on) {
    $("plan-form").querySelectorAll("input, select, button").forEach((el) => { el.disabled = on; });
    $("plan-submit").innerHTML = on ? "Planning…" : `Plan it <span aria-hidden="true">→</span>`;
  }

  /* POST the plan, retrying while the sleeping Space starts (its page is not our JSON). */
  async function submit(req) {
    const until = Date.now() + 150000;
    for (let attempt = 0; ; attempt++) {
      try {
        const r = await api("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) }, 20000);
        if (r.body && r.body.job) return r.body;
        if (r.body && r.body.detail) throw new Error(typeof r.body.detail === "string" ? r.body.detail : "Check the place name and numbers.");
      } catch (e) {
        if (!(e instanceof TypeError) && e.name !== "AbortError") throw e; // our own error message
      }
      if (Date.now() > until) throw new Error("The planner server did not wake up. Try again in a few minutes.");
      progress(0.01, attempt ? "Waking the free server… this happens after it has been idle" : "Contacting the planner…");
      await sleep(5000);
    }
  }

  async function follow(job, req, startedAt) {
    session.set(RUNNING_KEY, JSON.stringify({ job: job.job, req, startedAt }));
    showRun(startedAt);
    let misses = 0;
    for (;;) {
      let s;
      try {
        const r = await api(`/api/jobs/${job.job}`);
        if (r.status === 404) return fail((r.body && r.body.detail) || "The server restarted and lost this plan. Try again.");
        s = r.body;
        misses = 0;
      } catch (e) {
        if (++misses > 20) return fail("Lost contact with the planner server.");
        await sleep(3000);
        continue;
      }
      if (s.status === "queued") progress(0.01, s.position ? `Waiting in line · ${s.position} plan${s.position > 1 ? "s" : ""} ahead of yours` : "Starting…");
      else progress(s.progress, s.message);
      if (s.status === "error") return fail(s.error || "Unknown error.");
      if (s.status === "done") break;
      await sleep(2000);
    }
    progress(1, "Loading your plan…");
    const r = await api(`/api/jobs/${job.job}/result`, {}, 60000);
    if (!r.ok || !r.body) return fail("The plan finished but could not be downloaded. Try again.");
    const place = r.body.meta && r.body.meta.place;
    remember({ id: job.job, name: (place && place.short) || req.place, budget: req.budget, stations: req.stations, at: Date.now() }, r.body);
    session.del(RUNNING_KEY);
    location.href = liveHref(job.job);
  }

  function mine() {
    const list = readIndex();
    const el = $("plan-mine");
    if (!list.length) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<p class="planner__mine-title">Your recent plans</p><ul>${list.map((e) =>
      `<li><a href="${liveHref(e.id)}"${e.id === window.EV_LIVE ? ' aria-current="page"' : ""}>${escape(e.name)}<small>₹${fmt.num(e.budget)} L${e.stations ? ` · ${e.stations} stations` : ""}</small></a></li>`).join("")}</ul>`;

    // also list them in the place switcher
    const menu = document.querySelector("#place-switch .dropdown__menu");
    if (menu) {
      document.getElementById("place-switch").classList.remove("is-single");
      menu.insertAdjacentHTML("beforeend", `<li class="dropdown__sep" role="presentation">Your live plans</li>` + list.map((e) =>
        `<li role="option" aria-selected="${e.id === window.EV_LIVE}"><a href="${liveHref(e.id)}">${escape(e.name)}<small>₹${fmt.num(e.budget)} L · live</small></a></li>`).join(""));
    }
  }

  function liveNotes() {
    if (window.EV_LIVE && !window.EV_LIVE_FAILED) {
      const status = $("hero-status");
      status.insertAdjacentHTML("afterbegin", `<span class="live-chip">Live plan · fast mode</span><span class="status__sep">/</span>`);
    }
    if (window.EV_LIVE_FAILED) {
      const entry = readIndex().find((e) => e.id === window.EV_LIVE);
      if (entry) {
        $("plan-place").value = entry.name;
        $("plan-budget").value = entry.budget;
        $("plan-stations").value = entry.stations || "";
      }
      $("plan-error").hidden = false;
      $("plan-error").innerHTML = `<b>That live plan is not available any more.</b> ${escape(window.EV_LIVE_FAILED)} Showing ${escape(EV.place.short)} instead. Press Plan it to run it again.`;
      setTimeout(() => $("plan").scrollIntoView(), 300);
    }
  }

  function init() {
    if (!$("plan-form")) return;
    const stations = $("plan-stations");
    for (let k = 1; k <= 8; k++) stations.insertAdjacentHTML("beforeend", `<option value="${k}">${k} station${k > 1 ? "s" : ""}</option>`);
    mine();
    liveNotes();
    checkServer();

    $("plan-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const place = $("plan-place").value.trim().replace(/\s+/g, " ");
      const budget = Number($("plan-budget").value);
      const spacing = Number($("plan-spacing").value || 300);
      const k = $("plan-stations").value;
      if (place.length < 3) return fail("Type the name of a campus or area (at least 3 letters).");
      if (!(budget >= 10 && budget <= 5000)) return fail("Budget must be between ₹10 L and ₹5,000 L.");
      if (!(spacing >= 100 && spacing <= 1000)) return fail("Spacing must be between 100 m and 1,000 m.");
      const req = { place, budget, spacing, stations: k ? Number(k) : null };
      lock(true);
      const startedAt = Date.now();
      showRun(startedAt);
      progress(0.01, "Contacting the planner…");
      try {
        const job = await submit(req);
        setServer("on", "Planner online");
        await follow(job, req, startedAt);
      } catch (err) {
        fail(err.message || String(err));
      }
    });

    // resume a plan that was running when the page was reloaded
    try {
      const running = JSON.parse(session.get(RUNNING_KEY));
      if (running && API) {
        lock(true);
        $("plan-place").value = running.req.place;
        $("plan-budget").value = running.req.budget;
        follow({ job: running.job }, running.req, running.startedAt);
      }
    } catch (e) { session.del(RUNNING_KEY); }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
