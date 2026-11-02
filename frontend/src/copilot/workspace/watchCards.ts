/**
 * M76 MP-11 (owner order M76 D-1; flag `copilot_workspace`): watch cards from the workspace - the pure rules and the
 * reads. His choices (`GET / PUT /api/charts/workspace/watch`, backend app/charts/workspace_watch.py) are OFF and every
 * number Not set until he saves one; a type with a number cannot be on without HIS number (ATP chooses none, M68). The
 * worker raises the cards (in the app with "Open here", and his own Telegram chat when he turns it on).
 *
 * Information only: nothing here places, blocks or changes an order or an exit.
 */
import { request } from "../../api/client";
import type { AnnotationArea } from "./annotations";

export type WatchTf = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "1d";
export interface WatchedArea {
  id: string; exchange: string; symbol: string; tf: WatchTf; side: "buyer" | "seller"; low: number; high: number; since?: string | null;
}
export interface WatchPrefs {
  telegram: boolean;
  area_entry: boolean; area_rejection: boolean;
  strike_points: boolean; strike_points_n: number | null;
  iv_spike: boolean; iv_spike_points: number | null;
  margin_use: boolean; margin_use_pct: number | null;
  cut_off_reminder: boolean; cut_off_minutes: number | null;
  areas: WatchedArea[];
}
export interface WatchSettings {
  prefs: WatchPrefs; version: number; square_off_time: string | null;
  telegram: { connected: boolean; reaches_you: boolean }; blocks: false;
}

export type WatchKind = "area_entry" | "area_rejection" | "strike_points" | "iv_spike" | "margin_use" | "cut_off_reminder";
type NumberField = "strike_points_n" | "iv_spike_points" | "margin_use_pct" | "cut_off_minutes";
export interface KindRow { kind: WatchKind; label: string; field: NumberField | null; unit: string | null; whole?: boolean; max: number }

/** The six types in the order shown; each number is his (Not set until he types it). */
export const KIND_ROWS: readonly KindRow[] = [
  { kind: "area_entry", label: "Price inside a watched area", field: null, unit: null, max: 0 },
  { kind: "area_rejection", label: "Commitment candle at a watched area", field: null, unit: null, max: 0 },
  { kind: "strike_points", label: "Short strike within", field: "strike_points_n", unit: "points", max: 100_000 },
  { kind: "iv_spike", label: "IV at your strike moved by", field: "iv_spike_points", unit: "IV points", max: 100 },
  { kind: "margin_use", label: "Margin in use at or above", field: "margin_use_pct", unit: "%", max: 100 },
  { kind: "cut_off_reminder", label: "Before your square-off time", field: "cut_off_minutes", unit: "min", whole: true, max: 600 },
];

/** At most this many watched areas (the server's MAX_WATCHED_AREAS). */
export const MAX_WATCHED = 10;
const TFS: readonly WatchTf[] = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"];

export const watchApi = {
  read: () => request<WatchSettings>("/charts/workspace/watch"),
  save: (prefs: WatchPrefs) => request<WatchSettings>("/charts/workspace/watch", { method: "PUT", body: JSON.stringify(prefs) }),
};
export type WatchApi = typeof watchApi;

/** His number from what he typed: a positive number within the type's range (whole minutes for the reminder), else
 * null (Not set). Pure. */
export function numberOf(row: KindRow, text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const v = Number(t);
  if (!Number.isFinite(v) || v <= 0 || v > row.max) return null;
  if (row.whole && !Number.isInteger(v)) return null;
  return v;
}

/** Why a type cannot be turned on now, or null. Pure. */
export function blockedReason(prefs: WatchPrefs, row: KindRow, squareOff: string | null): string | null {
  if (row.field && prefs[row.field] == null) return "Set your number first.";
  if (row.kind === "cut_off_reminder" && !squareOff) return "Save your square-off time in Trade settings first.";
  return null;
}

/** The area to watch from the reading's area on the open chart (its exchange, symbol and reading timeframe), or null
 * when the chart is not one the watch reads. Pure. */
export function areaToWatch(a: AnnotationArea, instrument: string, tf: string): WatchedArea | null {
  const [exchange, symbol] = instrument.split(":");
  if (!exchange || !symbol || !(TFS as readonly string[]).includes(tf) || !/^[A-Z0-9&_\- ]{1,30}$/.test(symbol)) return null;
  const id = `${symbol}-${tf}-${a.side}-${Math.round(a.low * 100)}-${Math.round(a.high * 100)}`.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 40);
  return { id, exchange, symbol, tf: tf as WatchTf, side: a.side, low: Math.min(a.low, a.high), high: Math.max(a.low, a.high) };
}

/** His list with this area added (null when it is full or already watched). Pure. */
export function addArea(list: readonly WatchedArea[], a: WatchedArea): WatchedArea[] | null {
  if (list.length >= MAX_WATCHED || list.some((x) => x.id === a.id)) return null;
  return [...list, a];
}

/** "Buyer area 24,480 - 24,560 · 1h · NIFTY". Pure. */
export function areaText(a: WatchedArea, fmt: (n: number) => string): string {
  return `${a.side === "buyer" ? "Buyer" : "Seller"} area ${fmt(a.low)} - ${fmt(a.high)} · ${a.tf} · ${a.symbol}`;
}
