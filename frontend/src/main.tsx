import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import { initAppearance } from "./theme";
import { startPwaEvents } from "./pwa/events";
import { initPwa } from "./pwa/pwa";

initAppearance();   // P1.2: theme + colour-blind option before the first paint
initPwa();          // PW1: service worker (production builds), update and install offers
try { startPwaEvents(); } catch { /* PW6: install/update metrics are best effort */ }

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
