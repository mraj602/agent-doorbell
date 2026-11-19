// Runs in <head> before the stylesheet so the page never paints in the wrong theme.
// A saved choice wins; otherwise the system preference. No network, no cookies.
(function () {
  "use strict";
  var d = document.documentElement, t = null;
  try { t = localStorage.getItem("doorbell-theme"); } catch (e) { /* storage blocked */ }
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  d.setAttribute("data-theme", t);
  d.className += " js";
})();
