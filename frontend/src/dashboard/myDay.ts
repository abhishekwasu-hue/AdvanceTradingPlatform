import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import type { ChipTone } from "../ui/Chip";

/**
 * AUD-T1 (owner audit 28 Oct): "My day in one card" - the data of `GET /api/today/my-day` (app/today/my_day.py) and the
 * card's pure model. Flag `my_day_card` (depends on `ui_v2`, OFF by default): the server answers 404 while it is off and
 * the card is not shown. Information only: every fact is the user's own position or the market data ATP holds.
 */
export type MyDayChip = "safe" | "watch" | "near";

export interface MyDayArea {
  low: number; high: number; state: string; where: "above" | "below" | "inside"; distance_pct: number; range_text: string;
  from: "strike" | "stop";
  change: { event: string; time: string; words: string } | null;
}

export interface MyDayPosition {
  id: number; symbol: string; mode: string; side: "LONG" | "SHORT"; kind: string; underlying: string; deployment_id: number | null;
  chip: MyDayChip | null; chip_label: string | null; room_left_pct: number | null; chip_text: string | null;
  expiry: string | null; expiry_sessions: number | null; expiry_text: string | null;
  area: MyDayArea | null; area_text: string;
  /** TERM-2 R3: the held option's strike and right from the instrument master (null for anything else). */
  strike?: number | null; right?: string | null;
}

export interface MyDay {
  as_of: string; today: string; previous_session: string | null; note: string;
  thresholds: { watch_room_left_pct: number; near_room_left_pct: number };
  positions: MyDayPosition[];
  since_last_session: { gaps: { underlying: string; session: string; gap_pct: number; text: string }[]; events: string[]; areas: string[];
    empty: string | null };
}

export const CHIP_TONE: Record<MyDayChip, ChipTone> = { safe: "up", watch: "warn", near: "down" };
const ORDER: Record<string, number> = { near: 0, watch: 1, none: 2, safe: 3 };

/** What needs the eye first: near invalidation, watch, no chip (no stop / no price), then safe. Stable otherwise. */
export function sortPositions(rows: readonly MyDayPosition[]): MyDayPosition[] {
  return rows.map((p, i) => ({ p, i }))
    .sort((a, b) => (ORDER[a.p.chip ?? "none"] - ORDER[b.p.chip ?? "none"]) || a.i - b.i)
    .map((x) => x.p);
}

/** The short facts line of a row: expiry and the nearest area, each said once. */
export function factsLine(p: MyDayPosition): string {
  return [p.expiry_text, p.area_text].filter((x): x is string => !!x).join(" · ");
}

/** The "since the last session" lines, each once (several positions on one underlying never repeat a line). */
export function sinceLines(d: MyDay): string[] {
  const s = d.since_last_session;
  return Array.from(new Set([...s.gaps.map((g) => g.text), ...s.events, ...s.areas]));
}

export type MyDayState = { kind: "loading" } | { kind: "off" } | { kind: "error" } | { kind: "ok"; data: MyDay };

export const MY_DAY_REFRESH_MS = 120_000;

/** Reads the card while `enabled`; a 404 (flag off) hides it; refreshes every two minutes (marks move). */
export function useMyDay(enabled: boolean): MyDayState {
  const [state, setState] = useState<MyDayState>({ kind: "loading" });
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    const load = () => api.myDay().then(
      (data) => { if (live) setState({ kind: "ok", data }); },
      (e: unknown) => { if (live) setState((prev) => (e instanceof ApiError && e.status === 404 ? { kind: "off" } : prev.kind === "ok" ? prev : { kind: "error" })); },
    );
    load();
    const id = window.setInterval(load, MY_DAY_REFRESH_MS);
    return () => { live = false; window.clearInterval(id); };
  }, [enabled]);
  return enabled ? state : { kind: "off" };
}
