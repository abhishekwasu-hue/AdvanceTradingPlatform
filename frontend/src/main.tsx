import React, { type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// UX-4: the product fonts are served from this app (no request to a font CDN), Inter with fixed-width digits.
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/index.css";
import { initAppearance } from "./theme";
import { startPwaEvents } from "./pwa/events";
import { startWebVitals } from "./pwa/webVitals";
import { initPwa } from "./pwa/pwa";
import { isNativeApp } from "./native/platform";

function start(app: ReactNode) {
  initAppearance();   // P1.2: theme + colour-blind option before the first paint
  initPwa();          // PW1: service worker (production builds), update and install offers; not inside the app
  // PW6: install/update metrics are the browser's; the Android app's versions are its own (plan §9)
  if (!isNativeApp()) {
    try { startPwaEvents(); } catch { /* PW6: install/update metrics are best effort */ }
    try { startWebVitals(); } catch { /* AUD-G1.5: Core Web Vitals are best effort */ }
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      {app}
    </React.StrictMode>,
  );
}

if (__ATP_NATIVE__) {
  // Mobile B1: the Android app connects the native runtime first (src/native/boot.ts, a chunk only the app's bundle
  // has; the web build drops this branch). If it cannot load, the app still starts as plain web.
  import("./native/boot").then(
    ({ bootNative }) => { const Frame = bootNative(); start(<Frame><App /></Frame>); },
    () => start(<App />),
  );
} else {
  start(<App />);
}
