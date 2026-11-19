// Copy buttons and the theme toggle. Plain text only; no network.
(function () {
  "use strict";
  var live = document.getElementById("copy-status");
  var say = function (msg) {
    if (!live) return;
    live.textContent = "";
    setTimeout(function () { live.textContent = msg; }, 50);
  };

  var buttons = document.querySelectorAll("button.copy[data-copy]");
  Array.prototype.forEach.call(buttons, function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      if (!src) return;
      var text = src.textContent;
      var done = function (ok) {
        btn.textContent = ok ? "Copied" : "Press Ctrl+C";
        btn.classList.toggle("ok", ok);
        say(ok ? "Copied to clipboard" : "Text selected. Press Ctrl+C to copy.");
        setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("ok"); }, 2000);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      } else {
        var r = document.createRange();
        r.selectNodeContents(src);
        var s = window.getSelection();
        s.removeAllRanges();
        s.addRange(r);
        done(false);
      }
    });
  });

  // Theme toggle: theme.js already set data-theme before first paint.
  var root = document.documentElement;
  var toggle = document.getElementById("theme-toggle");
  var KEY = "doorbell-theme";
  var stored = function () {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  };
  var apply = function (t) {
    root.setAttribute("data-theme", t);
    if (toggle) toggle.setAttribute("aria-pressed", t === "dark" ? "true" : "false");
  };
  apply(root.getAttribute("data-theme") === "dark" ? "dark" : "light");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      apply(next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* storage blocked: still switches */ }
    });
  }
  // Follow system changes until the visitor picks a theme.
  if (window.matchMedia) {
    var mq = matchMedia("(prefers-color-scheme: dark)");
    var onChange = function (e) { if (!stored()) apply(e.matches ? "dark" : "light"); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
})();
