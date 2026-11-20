/**
 * TERM-CON (docs/plans/terminal-conclusion-PLAN.md): the Conclusion panel's one read - `GET /api/terminal/conclusion`
 * (flags ui_v2 + terminal + terminal_conclusion). Read only: what ATP's readings show for the Terminal's symbol, never a
 * recommendation; no order is placed from here (ADR-0006). The meaning of a direction is carried by a glyph AND a colour
 * token (never colour alone; the colour-blind mode swaps up / down to blue / orange in styles/tokens.css).
 */
import { request } from "../api/client";

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

export function conclusionPath(symbol: string, expiry: string | null, tf: string, also: readonly string[] = []): string {
  const q = new URLSearchParams({ symbol, tf });
  if (expiry) q.set("expiry", expiry);
  for (const t of also.filter((x) => x !== tf).slice(0, 2)) q.append("also", t);
  return `/terminal/conclusion?${q.toString()}`;          // request() adds the API base (/api/v1)
}

export const fetchConclusion = (symbol: string, expiry: string | null, tf: string, also: readonly string[] = []) =>
  request<Conclusion>(conclusionPath(symbol, expiry, tf, also));

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
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}
