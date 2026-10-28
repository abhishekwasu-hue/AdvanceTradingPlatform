/** MO4 (backend app/rhythm): the daily rhythm's digests and the user's Telegram choice, as `/api/rhythm/*` sends them. */

export type RhythmKind = "pre_market" | "mid_session" | "closing" | "weekly";

import type { Mood, Trend, VixLevel } from "../ui/levels";

/**
 * UX-11b C1: the typed facts of a section (numbers, signs, states), as app/rhythm/build.py and app/growth/dishes.py send
 * them in `rows`; the page renders them by the section's `kind`. `lines` keep the same facts as sentences (Telegram, and
 * a section kind the page does not know yet). Dates are ISO days; money in rupees; `*_pct` in percent units.
 */
export type SectionKind =
  | "lines" | "mood" | "events" | "structure" | "setups" | "positions" | "budget" | "day" | "market" | "next" | "week"
  | "waiting" | "sources" | "record" | "risk" | "premium" | "discipline" | "extremes" | "habits" | "story" | "yours"
  | "matches" | "scans";
export type Side = "LONG" | "SHORT";
export type Location = "IN_BUYER" | "IN_SELLER" | "NEARER_BUYER" | "NEARER_SELLER" | "ABOVE_BUYER" | "BELOW_SELLER";
export interface PriceArea { low: number; high: number }
export interface StructureEvent { kind: "BOS" | "CHOCH"; direction: "UP" | "DOWN" }

export type SectionRow =
  | { type: "cues"; mood: Mood; markets: { key: string; name: string; last: number | null; change_pct: number; stale: boolean }[] }
  | { type: "vix"; value: number; change_pct: number | null; level: VixLevel; percentile: number | null; as_of: string | null }
  | { type: "flows"; fii_crore: number; dii_crore: number; as_of: string | null }
  | { type: "event"; date: string; label: string; scope: string | null; window: string | null; detail: string | null; pauses_entries: boolean }
  | { type: "holiday"; date: string; name: string | null }
  | { type: "index"; symbol: string; label: string; trend: Trend | null; event: StructureEvent | null; location: Location | null;
      /** The user's OWN brief only: the nearest buyer / seller area as a price range, shown inside Details. */
      buyer_area: PriceArea | null; seller_area: PriceArea | null; last_read: string | null; daily?: Trend; weekly?: Trend }
  | { type: "setup"; symbol: string; side: Side | null; source: "strategy" | "scan"; detail: string; today?: boolean }
  | { type: "position"; symbol: string; side: Side; pnl: number | null; near: boolean }
  | { type: "more"; count: number }
  | { type: "budget"; left: number; limit: number; used: number; losses_in_row: number; pause_after: number | null }
  | { type: "day"; booked: number | null; open_pnl: number | null; inside_limits: boolean | null; discipline: number | null }
  | { type: "index_move"; label: string; close: number; change_pct: number; as_of: string | null; sessions: number }
  | { type: "breadth"; advances: number; declines: number; as_of: string | null }
  | { type: "sectors"; strongest: { label: string; change_pct: number }; weakest: { label: string; change_pct: number } }
  | { type: "next_session"; date: string }
  | { type: "carry"; count: number }
  | { type: "record"; closed: number; won: number; lost: number; net: number; after_charges: boolean }
  | { type: "discipline"; score: number; previous: number | null; history?: number[] }
  | { type: "costliest"; label: string; net: number; closed: number }
  | { type: "days_inside"; inside: number; days: number }
  // the dish pages (app/growth/dishes.py)
  | { type: "waiting"; count: number }
  | { type: "sources"; counts: Record<string, number>; words: Record<string, string>; more: boolean }
  | { type: "strategy_record"; name: string; closed: number; won: number; lost: number; days: number }
  | { type: "risk"; positions: number; open_pnl: number | null; prices_missing: boolean; at_invalidation: number | null;
      of_loss_limit_pct: number | null; no_invalidation: number }
  | { type: "premium"; label: string; kept_pct: number | null; kept: number | null; received: number }
  | { type: "expiry"; date: string; underlying: string; count: number }
  | { type: "habit"; label: string; trades: number; net: number | null }
  | { type: "suggested_tags"; count: number }
  | { type: "no_note"; count: number; closed: number }
  | { type: "best"; name: string; net: number; closed: number }
  | { type: "worst"; name: string; net: number; closed: number }
  | { type: "even" }
  | { type: "story"; daily: Trend; event: StructureEvent | null; place: "ABOVE_HIGH" | "BELOW_LOW" | "BETWEEN" | null; weekly: Trend | null;
      as_of: string | null }
  | { type: "held"; count: number; open_pnl: number | null; near: boolean }
  | { type: "lists"; names: string[] }
  | { type: "scan"; name: string; count: number; symbols: string[] }
  | { type: "on_lists"; symbols: string[] }
  | { type: "active"; count: number };

/** One row type of a section, by its `type`. */
export type RowOf<T extends SectionRow["type"]> = Extract<SectionRow, { type: T }>;

export interface RhythmSection {
  id: string;
  /** UX-11b: what the section is; absent from an older server (render `lines`). */
  kind?: SectionKind;
  /** UX-11b: the typed facts; absent or empty when there is nothing typed to show. */
  rows?: SectionRow[];
  title: string;
  /** Plain sentences built from the user's own data and the market's; empty when there is nothing to say. */
  lines: string[];
  /** What to say when `lines` is empty (never a sample figure). */
  empty: string | null;
  /** The page this section opens. */
  link: { label: string; to: string } | null;
}

export interface RhythmBrief {
  kind: RhythmKind;
  title: string;
  subtitle: string;
  as_of: string;
  sections: RhythmSection[];
  has_content: boolean;
  note: string;
}

export interface RhythmToday {
  /** The digest of the moment on the IST market calendar. */
  phase: RhythmKind;
  kinds: RhythmKind[];
  brief: RhythmBrief;
}

export interface RhythmPrefs {
  telegram: boolean;
  pre_market: boolean;
  mid_session: boolean;
  closing: boolean;
  weekly: boolean;
}

export interface RhythmSettings {
  prefs: RhythmPrefs;
  version: number;
  telegram: { connected: boolean; reaches_you: boolean };
}
