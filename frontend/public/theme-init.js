/* P1.2: apply the saved theme before the first paint, so a light-theme user never sees a dark flash while the app
 * bundle loads. A classic same-origin script: the CSP (script-src 'self') forbids an inline one. It mirrors
 * loadAppearance / resolvedTheme in src/theme.ts, which re-applies (and owns) the setting once the app starts. */
(function () {
  var theme = "dark", cvd = false, v2 = false, size = "normal", high = false;
  try {
    // UI2-0 (UI-1): with UI v2 on (the shell leaves this hint), no saved theme means "follow the system, light when unknown".
    v2 = localStorage.getItem("atp_ui_v2_theme") === "system";
    if (v2) theme = "system";
    var v = JSON.parse(localStorage.getItem("atp_appearance") || "{}");
    if (v.theme === "light" || v.theme === "system" || v.theme === "dark") theme = v.theme;
    cvd = v.colorBlind === true;
    if (v.textSize === "large" || v.textSize === "xlarge") size = v.textSize;
    high = v.highContrast === true;
  } catch (e) { /* storage unavailable: the dark default */ }
  if (theme === "system") {
    var mm = !!window.matchMedia;
    theme = v2 ? (mm && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
               : (mm && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  }
  var root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-cvd", cvd ? "on" : "off");
  root.setAttribute("data-text-size", size);
  root.setAttribute("data-contrast", high ? "high" : "normal");
  if (theme === "dark") root.classList.add("dark"); else root.classList.remove("dark");
})();
