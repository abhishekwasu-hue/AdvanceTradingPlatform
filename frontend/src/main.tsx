import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import { initAppearance } from "./theme";
import { startPwaEvents } from "./pwa/events";

initAppearance();   // P1.2: theme + colour-blind option before the first paint
try { startPwaEvents(); } catch { /* PW6: install/update metrics are best effort */ }

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
