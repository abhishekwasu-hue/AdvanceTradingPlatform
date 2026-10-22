import type { SignalFeedItem } from "./feed";

/** UI2-3: one made-up inbox row for the unit tests (no real data): a waiting Autopilot proposal on "DEMO 50" at
 * 10:28 IST, expiring 10:38:30 IST. */
export function feedItem(extra: Partial<SignalFeedItem> = {}): SignalFeedItem {
  return {
    id: "action:11", at: "2026-10-08T04:58:00+00:00", symbol: "DEMO 50", exchange: "NSE", direction: "LONG", setup: "Demo breakout",
    entry: 100, stop: 98, target: 104, rr: 2, source: { kind: "autopilot", name: "Deployment #4" }, mode: "PAPER", status: "waiting",
    expires_at: "2026-10-08T05:08:30+00:00", snoozed_until: null, reasons: ["Made-up reason"], action_id: 11, outcome: null, ...extra,
  };
}
