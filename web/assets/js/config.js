/* Address of the live planner API once it is hosted. Empty = use the local server when the page is opened
   from this computer (file:// or localhost), otherwise the planner is shown as offline.
   ?api=<url> overrides it for the tab. */
window.EV_API = "https://ev-charging-planner-api.vercel.app";
window.EV_LOCAL_API = "http://127.0.0.1:7860";

(function () {
  try {
    var q = new URLSearchParams(location.search).get("api");
    if (q) sessionStorage.setItem("ev-api", q);
    window.EV_API = (sessionStorage.getItem("ev-api") || window.EV_API).replace(/\/+$/, "");
  } catch (e) { /* storage blocked: keep the default */ }
  if (!window.EV_API && /^(file:|https?:\/\/(localhost|127\.0\.0\.1)[:/])/.test(location.href)) window.EV_API = window.EV_LOCAL_API;
})();
