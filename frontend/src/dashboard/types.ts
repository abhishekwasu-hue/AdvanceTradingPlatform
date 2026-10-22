/**
 * UI2-2 (UI v2 proposal §5, §13 UI-3): the shape of GET /api/market/strip - the v2 dashboard's market strip in one
 * read-only call (backend/app/market_data/strip_routes.py).
 */

/** Where an index's level came from: the broker session now, or the organisation's market memory (the last reading). */
export type StripSource = "live" | "memory";

export interface StripIndex {
  /** The broker's trading symbol ("NIFTY 50", "NIFTY BANK", "INDIA VIX"). */
  key: string;
  /** The name the trader reads ("BANK NIFTY"). */
  label: string;
  symbol: string;
  exchange: string;
  /** null when no real price is known - never 0. */
  ltp: number | null;
  prev_close: number | null;
  change: number | null;
  /** In percent units (0.48 = +0.48%). */
  change_pct: number | null;
  /** When the price was taken (ISO). */
  as_of: string | null;
  stale: boolean | null;
  source: StripSource | null;
  missing: boolean;
}

export type MarketPhase = "open" | "pre-open" | "closed";

export interface StripMarket {
  exchange: string;
  phase: MarketPhase;
  is_open: boolean;
  /** "09:15" / "15:30", IST. */
  opens_at: string;
  closes_at: string;
  /** The next regular open (ISO, IST), null while open. */
  next_open: string | null;
  /** Today's holiday name, when closed for one. */
  holiday: string | null;
  reason: "weekend" | "holiday" | "before-open" | "after-close" | null;
}

export interface MarketStrip {
  indices: StripIndex[];
  source: StripSource | "mixed" | "none";
  broker: { broker: string; account_label: string } | null;
  market: StripMarket;
  /** How long until the next read: 15 s while the market is open (or about to), 300 s otherwise. */
  refresh_seconds: number;
  fetched_at: string;
}
