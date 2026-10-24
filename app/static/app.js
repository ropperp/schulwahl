// Bestätigungsdialog für Formulare mit data-confirm (inline-JS ist per CSP gesperrt)
document.addEventListener("submit", function (ev) {
  var msg = ev.target.getAttribute && ev.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) ev.preventDefault();
});
// Wahlcode beim Tippen in Großbuchstaben umwandeln
document.addEventListener("input", function (ev) {
  if (ev.target.classList && ev.target.classList.contains("codeinput")) {
    var pos = ev.target.selectionStart;
    ev.target.value = ev.target.value.toUpperCase();
    try { ev.target.setSelectionRange(pos, pos); } catch (e) {}
  }
});
// Benutzer-Menü schließen bei Klick daneben oder Escape
document.addEventListener("click", function (ev) {
  document.querySelectorAll("details.usermenu[open]").forEach(function (d) { if (!d.contains(ev.target)) d.removeAttribute("open"); });
});
document.addEventListener("keydown", function (ev) {
  if (ev.key === "Escape") document.querySelectorAll("details.usermenu[open]").forEach(function (d) { d.removeAttribute("open"); });
});
// Formulare mit data-reload-after (z. B. Codes erzeugen): das Ergebnis (PDF) öffnet in einem neuen Tab,
// diese Seite lädt danach neu und zeigt die aktuellen Zahlen und die Erfolgsmeldung.
document.addEventListener("submit", function (ev) {
  var ms = ev.target.getAttribute && ev.target.getAttribute("data-reload-after");
  if (ms && !ev.defaultPrevented) setTimeout(function () { window.location.reload(); }, parseInt(ms, 10) || 1500);
});
