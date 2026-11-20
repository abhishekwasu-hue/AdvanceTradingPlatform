/**
 * TERM-CON (docs/plans/terminal-conclusion-PLAN.md): the Conclusion panel's one read - `GET /api/terminal/conclusion`
 * (flags ui_v2 + terminal + terminal_conclusion). Read only: what ATP's readings show for the Terminal's symbol, never a
 * recommendation; no order is placed from here (ADR-0006). The meaning of a direction is carried by a glyph AND a colour
 * token (never colour alone; the colour-blind mode swaps up / down to blue / orange in styles/tokens.css).
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { formatINR, formatTimeIST } from "../ui/format";

export type Direction = "up" | "down" | "range" | "mixed" | "none";
export type Tone = "up" | "down" | "neutral" | "warn";

export interface ConclusionLine { text: string; tone: Tone; kind: "signal" | "data" | "record"; source: string; as_of: string | null }
export interface ConclusionSection {
  key: string; title: string; status: "ok" | "stale" | "na"; source: string; as_of: string | null; lines: ConclusionLine[]; note: string | null;
}
export interface Reading { source: string; label: string; tf: string | null; direction: Direction; as_of: string | null; stale?: boolean }
export interface NeedsYou { key: string; text: string; action: string | null }
export interface Glance {
  lean: { side: "up" | "down" | "sideways"; count: number; of: number; words: string };
  meter: { up: number; down: number; range: number; none: number; total: number };
  agrees: { direction: "up" | "down"; text: string; sources: string[] }[];
  conflicts: { text: string; sources: string[] }[];
  line: string;
}
export interface Conclusion {
  symbol: string; expiry: string | null; tf: string; as_of: string; sections: ConclusionSection[]; readings: Reading[];
  needs_you: NeedsYou[]; glance: Glance; note: string; fingerprint: string;
}

export function conclusionPath(symbol: string, expiry: string | null, tf: string, also: readonly string[] = [], v?: string): string {
  const q = new URLSearchParams({ symbol, tf });
  if (expiry) q.set("expiry", expiry);
  for (const t of also.filter((x) => x !== tf).slice(0, 2)) q.append("also", t);
  if (v) q.set("v", v.slice(0, 200));
  return `/terminal/conclusion?${q.toString()}`;          // request() adds the API base (/api/v1)
}

/** `v`: the page's change key (bar, OI slot, his positions / orders) - the server's short cache is per key (C3). */
export const fetchConclusion = (symbol: string, expiry: string | null, tf: string, also: readonly string[] = [], v?: string) =>
  request<Conclusion>(conclusionPath(symbol, expiry, tf, also, v));

// --- C4: the AI summary, on tap only, on his own key ------------------------------------------------------------------

export interface ConclusionSummary {
  ready: boolean; text: string | null; reason: string | null;
  usage: { calls: number; tokens_input: number; tokens_output: number; cost_inr: number } | null;
}

export const summarizeConclusion = (symbol: string, expiry: string | null, tf: string, also: readonly string[] = [], v?: string) =>
  request<ConclusionSummary>("/terminal/conclusion/summary", {
    method: "POST", body: JSON.stringify({ symbol, expiry, tf, also: also.filter((x) => x !== tf).slice(0, 2), v: v?.slice(0, 200) }),
  });

/** "₹0.42" for this call's cost (from his own key's metering); empty when the call reported none. */
export function costText(u: ConclusionSummary["usage"]): string {
  if (!u) return "";
  return `This summary cost ${formatINR(Math.max(u.cost_inr, 0.01), { paise: true })} on your AI key.`;
}

// --- C3: refresh on bar close and on a new OI slot, with no polling ---------------------------------------------------

const MINUTE = 60_000;
/** NSE bars start at 09:15 IST (03:45 UTC); a day's bar closes at 15:30 IST (10:00 UTC). */
const ANCHOR_MS = (3 * 60 + 45) * MINUTE;
const DAY_ANCHOR_MS = 10 * 60 * MINUTE;
/** The server's bar settle time (LIVE_PUSH_BAR_SETTLE_SECONDS): a bar is read a few seconds after it closes. */
export const SETTLE_MS = 5_000;
/** The OI collector's slot (OI_BANNER_SLOT_MINUTES). */
export const OI_SLOT_MS = 5 * MINUTE;

export function tfMs(tf: string): number {
  const m = /^(\d+)(m|h|d)$/.exec(tf);
  if (!m) return 15 * MINUTE;
  const n = Number(m[1]);
  return m[2] === "m" ? n * MINUTE : m[2] === "h" ? n * 60 * MINUTE : n * 1440 * MINUTE;
}

/** The index of the last CLOSED step (bar or slot) at `now`, after the settle time. */
export function closedIndex(now: number, stepMs: number): number {
  const anchor = stepMs >= 1440 * MINUTE ? DAY_ANCHOR_MS : ANCHOR_MS;
  return Math.floor((now - SETTLE_MS - anchor) / stepMs);
}

/** Milliseconds until the next step closes (plus the settle time). */
export function msToNext(now: number, stepMs: number): number {
  const anchor = stepMs >= 1440 * MINUTE ? DAY_ANCHOR_MS : ANCHOR_MS;
  const next = (closedIndex(now, stepMs) + 1) * stepMs + anchor + SETTLE_MS;
  return Math.max(1_000, next - now);
}

/** One timer to the next close of `stepMs` (no interval, no polling); the index of the last closed step. */
export function useStepClose(stepMs: number, enabled: boolean): number {
  const [index, setIndex] = useState(() => closedIndex(Date.now(), stepMs));
  useEffect(() => {
    if (!enabled) return undefined;
    let timer: ReturnType<typeof setTimeout>;
    const arm = () => {
      timer = setTimeout(() => { setIndex(closedIndex(Date.now(), stepMs)); arm(); }, msToNext(Date.now(), stepMs));
    };
    arm();
    return () => clearTimeout(timer);
  }, [stepMs, enabled]);
  return index;
}

/** ▲ up, ▼ down, ◆ range / mixed / sideways, — no direction: the shape carries the meaning with the colour. */
export function glyph(d: Direction | "sideways"): string {
  return d === "up" ? "▲" : d === "down" ? "▼" : d === "none" ? "—" : "◆";
}

/** The token colour classes of a direction (text, tint background, border). */
export function toneOf(d: Direction | "sideways" | Tone): { text: string; tint: string; border: string } {
  if (d === "up") return { text: "text-up", tint: "bg-up-tint", border: "border-up" };
  if (d === "down") return { text: "text-down", tint: "bg-down-tint", border: "border-down" };
  if (d === "warn") return { text: "text-warn", tint: "bg-warn-tint", border: "border-warn" };
  return { text: "text-fg-muted", tint: "bg-surface-2", border: "border-subtle" };
}

/** "Chart 15m" -> the chip's short label; the server's label otherwise. */
export function chipLabel(r: Reading): string {
  if (r.source === "chart") return r.tf ?? "Chart";
  if (r.source === "signals") return "Strategy";
  return r.label;
}

/** The meter's segments, one per reading, in the order up, down, range / mixed, none. */
export function segments(m: Glance["meter"]): Direction[] {
  return [...Array(m.up).fill("up"), ...Array(m.down).fill("down"), ...Array(m.range).fill("range"), ...Array(m.none).fill("none")];
}

/** A short time ("10:35") from an ISO time; the text itself when it is not one. */
export function shortTime(iso: string | null): string {
  if (!iso || Number.isNaN(Date.parse(iso))) return "";
  return formatTimeIST(iso, { suffix: false });
}
