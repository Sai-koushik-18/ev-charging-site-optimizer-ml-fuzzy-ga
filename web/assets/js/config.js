/* Address of the live planner API (the free Hugging Face Space). Empty = the planner form is shown as offline.
   For local testing open the page with ?api=http://127.0.0.1:7860 once; it is remembered for the tab. */
window.EV_API = "";

(function () {
  try {
    var q = new URLSearchParams(location.search).get("api");
    if (q) sessionStorage.setItem("ev-api", q);
    window.EV_API = (sessionStorage.getItem("ev-api") || window.EV_API).replace(/\/+$/, "");
  } catch (e) { /* storage blocked: keep the default */ }
})();
