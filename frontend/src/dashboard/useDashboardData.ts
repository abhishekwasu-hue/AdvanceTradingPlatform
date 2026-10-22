import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { OiBannerResponse } from "../oi/types";
import type {
  AiAction, AnalyticsSummary, Deployment, NotificationEntry, PaperRiskStatus, PortfolioExposure, SystemStatus, TradeRecord,
} from "../types";
import { WIN_RATE_DAYS } from "./model";
import type { MarketStrip } from "./types";

/**
 * UI2-2 (UI v2 proposal §5 "Data behind each block", "Refresh"): the dashboard's reads, all existing read-only endpoints
 * plus the new GET /api/market/strip. Nothing here writes or places an order.
 *
 * Refresh: positions, P&L and Needs attention every 30 s (as today); the strip at the interval the server names
 * (15 s while the market is open, 5 min outside it); the OI lines every minute. A tab in the background skips its turns
 * and reads again when it is shown. (UI2-7: the Setup chip reads the stored setup progress, src/setup/useSetup.ts.)
 */

export type Loadable<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "error" };

export const LOADING = { status: "loading" } as const;
export const REFRESH_MS = 30_000;
export const OI_REFRESH_MS = 60_000;
export const STRIP_FALLBACK_MS = 15_000;

/** The data, or null while loading or after a failure. */
export function dataOf<T>(l: Loadable<T>): T | null {
  return l.status === "ok" ? l.data : null;
}

/** How long until the strip is read again: the server's `refresh_seconds`, bounded to 10 s - 10 min. */
export function stripDelay(strip: MarketStrip | null): number {
  const s = strip?.refresh_seconds;
  return typeof s === "number" && Number.isFinite(s) ? Math.min(600, Math.max(10, s)) * 1000 : STRIP_FALLBACK_MS;
}

export interface PollOptions<T> {
  load: () => Promise<T>;
  /** Milliseconds until the next read, given the last good data (null before one). */
  delay: (data: T | null) => number;
  onData: (data: T) => void;
  onError: () => void;
  /** True while the tab is in the background: the turn is skipped (no read) and the next one scheduled. */
  hidden?: () => boolean;
}

/** Reads now, then again after `delay(lastData)` ms after each answer (never two reads at once). Returns `stop`. */
export function startPolling<T>({ load, delay, onData, onError, hidden }: PollOptions<T>): () => void {
  let alive = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let last: T | null = null;
  const schedule = () => { if (alive) timer = setTimeout(tick, delay(last)); };
  function tick() {
    if (!alive) return;
    if (hidden?.()) { schedule(); return; }
    load()
      .then((data) => { if (alive) { last = data; onData(data); } })
      .catch(() => { if (alive) onError(); })
      .finally(schedule);
  }
  tick();
  return () => { alive = false; if (timer !== undefined) clearTimeout(timer); };
}

const tabHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

/**
 * Reads `load` now and again after `delay(lastData)` ms while `enabled`. A failed re-read keeps the last good data (a
 * blip does not blank the block); a first read that fails is "error". Returns the state and a `reload`.
 */
export function usePolled<T>(load: () => Promise<T>, delay: (data: T | null) => number, enabled: boolean): [Loadable<T>, () => void] {
  const [state, setState] = useState<Loadable<T>>(LOADING);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  const delayRef = useRef(delay);
  loadRef.current = load;
  delayRef.current = delay;
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) { setState(LOADING); return; }
    return startPolling<T>({
      load: () => loadRef.current(), delay: (d) => delayRef.current(d), hidden: tabHidden,
      onData: (data) => setState({ status: "ok", data }),
      onError: () => setState((s) => (s.status === "ok" ? s : { status: "error" })),
    });
  }, [enabled, nonce]);

  return [state, reload];
}

export interface DashboardData {
  strip: Loadable<MarketStrip>;
  oi: Loadable<OiBannerResponse[]>;
  exposure: Loadable<PortfolioExposure>;
  positions: Loadable<TradeRecord[]>;
  /** The newest proposals of every status (today's signals). */
  actions: Loadable<AiAction[]>;
  /** Every proposal waiting for a decision (Needs attention), read on its own so none is cut off by the newest-100 list. */
  pending: Loadable<AiAction[]>;
  deployments: Loadable<Deployment[]>;
  risk: Loadable<PaperRiskStatus | null>;
  unread: Loadable<NotificationEntry[]>;
  status: Loadable<SystemStatus>;
  summary: Loadable<AnalyticsSummary>;
  /** Reads everything again now (after a broker login, or "Try again"). */
  reload: () => void;
}

const every = (ms: number) => () => ms;
/** Risk by default answers 404 while its flag is off: no limits to show, not a failure. */
const winRateSummary = () => api.getAnalyticsSummary(WIN_RATE_DAYS);
const riskOrNone = () => api.paperRisk().catch((e: unknown) => {
  if (typeof e === "object" && e !== null && (e as { status?: number }).status === 404) return null;
  throw e;
});

/** Every block's data while `enabled` (signed in, broker state known and not blocking the page). */
export function useDashboardData(enabled: boolean): DashboardData {
  const [strip, r1] = usePolled(api.marketStrip, stripDelay, enabled);
  const [oi, r2] = usePolled(() => api.oiBanners().then((d) => d.banners ?? []), every(OI_REFRESH_MS), enabled);
  const [exposure, r3] = usePolled(() => api.portfolioExposure(true), every(REFRESH_MS), enabled);
  const [positions, r4] = usePolled(api.listPositions, every(REFRESH_MS), enabled);
  const [actions, r5] = usePolled(() => api.aiActions(), every(REFRESH_MS), enabled);
  const [pending, r12] = usePolled(() => api.aiActions("PROPOSED"), every(REFRESH_MS), enabled);
  const [deployments, r6] = usePolled(() => api.listDeployments(), every(REFRESH_MS), enabled);
  const [risk, r7] = usePolled(riskOrNone, every(REFRESH_MS), enabled);
  const [unread, r8] = usePolled(() => api.listNotifications(true), every(REFRESH_MS), enabled);
  const [status, r9] = usePolled(api.systemStatus, every(REFRESH_MS), enabled);
  const [summary, r10] = usePolled(winRateSummary, every(REFRESH_MS), enabled);
  const reload = useCallback(() => { for (const r of [r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r12]) r(); },
    [r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r12]);
  return { strip, oi, exposure, positions, actions, pending, deployments, risk, unread, status, summary, reload };
}
