/* P1.2: apply the saved theme before the first paint, so a light-theme user never sees a dark flash while the app
 * bundle loads. A classic same-origin script: the CSP (script-src 'self') forbids an inline one. It mirrors
 * loadAppearance / resolvedTheme in src/theme.ts, which re-applies (and owns) the setting once the app starts. */
(function () {
  var theme = "dark", cvd = false;
  try {
    var v = JSON.parse(localStorage.getItem("atp_appearance") || "{}");
    if (v.theme === "light" || v.theme === "system") theme = v.theme;
    cvd = v.colorBlind === true;
  } catch (e) { /* storage unavailable: the dark default */ }
  if (theme === "system") theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  var root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-cvd", cvd ? "on" : "off");
  if (theme === "dark") root.classList.add("dark"); else root.classList.remove("dark");
})();
