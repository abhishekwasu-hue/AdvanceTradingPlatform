import { useEffect, useState } from "react";
import { STREAM_DOWN_NOTICE_MS, liveTicks, type HubStatus } from "../live/liveTicks";
import type { SystemStatus } from "../types";

/**
 * UX-7 (owner order UX-2, 25 Oct): one small dot in the header that says how fresh the prices are - green "Live",
 * amber "Reconnecting" / "Prices every few seconds", red "Broker login needed", grey "Market closed". Words go with the
 * colour (the label on desktop, the screen-reader name and tooltip everywhere), never colour alone. Shown only when
 * live prices are on (flag `data_hub`) for a signed-in user; it reads the same connection state as the live-prices
 * notice (src/live/liveTicks.ts) and the NSE session from the shared status read.
 */
export type FeedState = "live" | "polling" | "reconnecting" | "renew" | "closed";

export const FEED_TEXT: Record<FeedState, { label: string; title: string; dot: string }> = {
  live: { label: "Live", title: "Live prices from your broker connection.", dot: "bg-up" },
  polling: { label: "Every few seconds", title: "Prices refresh every few seconds (no live stream right now).", dot: "bg-warn" },
  reconnecting: { label: "Reconnecting", title: "Live prices are reconnecting. Prices may be a few seconds old.", dot: "bg-warn" },
  renew: { label: "Broker login needed", title: "Your broker login has ended. Log in again to get live prices.", dot: "bg-down" },
  closed: { label: "Market closed", title: "The market is closed. Prices are from the last session close.", dot: "bg-fg-muted" },
};

/** The dot's state, or null (nothing known yet). The market being closed wins over a quiet stream. Pure. */
export function feedState(hub: HubStatus | null, streamDownSince: number | null, marketOpen: boolean | null, now: number): FeedState | null {
  if (hub?.state === "renew_needed") return "renew";
  if (marketOpen === false) return "closed";
  if (!hub) return null;
  if (hub.state === "reconnecting" || hub.state === "connecting") return "reconnecting";
  if (streamDownSince !== null && now - streamDownSince >= STREAM_DOWN_NOTICE_MS) return "reconnecting";
  if (hub.state === "polling") return "polling";
  return "live";
}

/** Whether the NSE session is open, from the shared status read (null: not known). Pure. */
export function nseOpen(status: SystemStatus | null): boolean | null {
  const s = status?.sessions?.NSE;
  return s ? s.is_open : null;
}

function useFeedState(enabled: boolean, marketOpen: boolean | null): FeedState | null {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!enabled) return undefined;
    const store = liveTicks();
    const off = store.onStatus(() => bump((n) => n + 1));
    const timer = setInterval(() => { if (store.streamDownSince !== null) bump((n) => n + 1); }, 1000);
    return () => { off(); clearInterval(timer); };
  }, [enabled]);
  if (!enabled) return null;
  const store = liveTicks();
  return feedState(store.status, store.streamDownSince, marketOpen, Date.now());
}

export function FeedDotView({ state }: { state: FeedState }) {
  const t = FEED_TEXT[state];
  return (
    <span role="status" aria-label={`Prices: ${t.label}`} title={t.title} data-feed={state}
      className="inline-flex h-11 shrink-0 items-center gap-space-1 px-space-1 text-t12 font-medium text-fg-muted md:h-9">
      <span aria-hidden className={`inline-block h-2 w-2 rounded-pill ${t.dot}`} />
      <span className="hidden whitespace-nowrap lg:inline">{t.label}</span>
    </span>
  );
}

export function FeedDot({ enabled, status }: { enabled: boolean; status: SystemStatus | null }) {
  const state = useFeedState(enabled, nseOpen(status));
  return state ? <FeedDotView state={state} /> : null;
}
