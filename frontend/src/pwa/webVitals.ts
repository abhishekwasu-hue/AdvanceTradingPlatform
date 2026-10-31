// AUD-G1.5 (owner audit 28 Oct): Core Web Vitals from real browsers, measured with the browser's own PerformanceObserver
// (no third-party script). One report per page view when the page is hidden: LCP, INP (the slowest interaction) and CLS,
// with the page family and phone / desktop only - no URL, no user, no device id (POST /api/pwa/vitals, counted in
// /metrics). Best effort: an unsupported browser sends nothing.

import { apiUrl } from "../api/base";

export type VitalsPage = "today" | "charts" | "chain" | "terminal" | "positions" | "other";

const ENDPOINT = apiUrl("/pwa/vitals");
const PHONE_MAX_WIDTH = 767;

export function pageFamily(path: string): VitalsPage {
  const p = path.toLowerCase();
  if (p === "/" || p.startsWith("/today") || p.startsWith("/dashboard")) return "today";
  if (p.startsWith("/chart")) return "charts";
  if (p.includes("option-chain") || p.startsWith("/chain")) return "chain";
  if (p.startsWith("/terminal")) return "terminal";
  if (p.startsWith("/positions") || p.startsWith("/portfolio")) return "positions";
  return "other";
}

export interface Vitals { lcp_ms: number | null; inp_ms: number | null; cls: number | null }

/** Starts observing; returns a stop function. `send` receives the report once, when the page is first hidden. */
export function startWebVitals(win: Window = window, send: (body: Record<string, unknown>) => void = beacon): () => void {
  const PO = (win as unknown as { PerformanceObserver?: typeof PerformanceObserver }).PerformanceObserver;
  if (!PO) return () => {};
  const v: Vitals = { lcp_ms: null, inp_ms: null, cls: null };
  const observers: PerformanceObserver[] = [];
  const watch = (type: string, onEntry: (e: PerformanceEntry) => void, extra: Record<string, unknown> = {}) => {
    try {
      const o = new PO((list) => list.getEntries().forEach(onEntry));
      o.observe({ type, buffered: true, ...extra } as PerformanceObserverInit);
      observers.push(o);
    } catch { /* intentional: this entry type is not supported here */ }
  };
  watch("largest-contentful-paint", (e) => { v.lcp_ms = e.startTime; });
  watch("layout-shift", (e) => {
    const s = e as PerformanceEntry & { value?: number; hadRecentInput?: boolean };
    if (!s.hadRecentInput && typeof s.value === "number") v.cls = (v.cls ?? 0) + s.value;
  });
  watch("event", (e) => {
    const t = e as PerformanceEntry & { interactionId?: number };
    if (t.interactionId) v.inp_ms = Math.max(v.inp_ms ?? 0, e.duration);
  }, { durationThreshold: 40 });
  let sent = false;
  const onHidden = () => {
    if (sent || win.document.visibilityState !== "hidden") return;
    sent = true;
    if (v.lcp_ms === null && v.inp_ms === null && v.cls === null) return;
    const device = win.innerWidth <= PHONE_MAX_WIDTH ? "phone" : "desktop";
    send({ page: pageFamily(win.location.pathname), device, ...v });
  };
  win.document.addEventListener("visibilitychange", onHidden);
  return () => {
    observers.forEach((o) => o.disconnect());
    win.document.removeEventListener("visibilitychange", onHidden);
  };
}

function beacon(body: Record<string, unknown>): void {
  try {
    const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
    if (!navigator.sendBeacon?.(ENDPOINT, blob)) {
      void fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        credentials: "omit", keepalive: true }).catch(() => {});  // intentional: metrics are best effort
    }
  } catch { /* intentional: metrics are best effort */ }
}
