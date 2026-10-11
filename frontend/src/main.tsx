import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import { initAppearance } from "./theme";
import { initPwa } from "./pwa/pwa";

initAppearance();   // P1.2: theme + colour-blind option before the first paint
initPwa();          // PW1: service worker (production builds), update and install offers

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
