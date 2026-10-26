// Hell/Dunkel: Auswahl aus dem Browser lesen (synchron im <head>, damit die Seite nicht aufblitzt) und Umschalter bedienen.
(function () {
  var root = document.documentElement;
  try {
    var saved = window.localStorage.getItem("theme");
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
  } catch (e) { /* ohne Speicher: Standard folgt dem Gerät */ }
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest(".themetoggle");
    if (!btn) return;
    var current = root.getAttribute("data-theme") ||
      (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    var next = current === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { window.localStorage.setItem("theme", next); } catch (e) { /* egal */ }
  });
})();
