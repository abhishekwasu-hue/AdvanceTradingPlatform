/**
 * U9 TX2: the screen library's confluence view, mirror screens and watch-then-trigger lists, as the server sends them
 * (GET /api/screener/library/mirrors, /watch-lists; POST /confluence, /watch-trigger - flag `screener_tx2`, off by
 * default: the calls then fail and the page shows none of it). Pure functions only, so the panel is tested without a
 * browser.
 *
 * Agreement between screens is a count, never a score, a pick or a recommendation.
 */
import type { Taxonomy } from "./taxonomy";

export const MIN_CONFLUENCE = 2;
export const MAX_CONFLUENCE = 6;

/** The short / breakdown mirror of a long pre-built screen: its text, or the reason it has none. */
export interface MirrorEntry {
  id: string;
  mirror_of: string;
  name: string;
  method: string;
  setup: string;
  direction: "short";
  base_tf: string | null;
  source: string;
  text: string | null;
  reason: string | null;
  how: "reflected" | "template_down_side" | null;
}
export interface MirrorsResponse { mirrors: MirrorEntry[]; note: string }

export interface WatchListDef {
  id: string; name: string; definition: string; method: string; setup: string; direction: string; base_tf: string;
  ready: string; trigger: string; params: Record<string, number | string | boolean>; transition: string;
}
export interface WatchListsResponse { lists: WatchListDef[]; note: string }

export interface ConfluenceScreen { ref: string; name: string; method: string; setup: string; text: string }
export interface ConfluenceRow { symbol: string; methods: string[]; method_count: number; screen_count: number; screens: ConfluenceScreen[] }
export interface ConfluenceResponse {
  rows: ConfluenceRow[];
  unflagged: string[];
  not_evaluated: Record<string, { ref: string; reason: string }[]>;
  methods_run: string[];
  screens: (ConfluenceScreen & { base_tf: string; matched: number })[];
  scanned: number;
  note: string;
  disclaimer: string;
}

export interface WatchResponse {
  triggered: string[]; ready: string[]; neither: string[]; not_evaluated: Record<string, string>; note: string;
  name: string; base_tf: string; ready_text: string; trigger_text: string; transition_text: string; scanned: number; disclaimer: string;
}

export function isMirrors(x: unknown): x is MirrorsResponse {
  const m = x as Partial<MirrorsResponse> | null;
  return !!m && Array.isArray(m.mirrors) && typeof m.note === "string";
}

export function isWatchLists(x: unknown): x is WatchListsResponse {
  const w = x as Partial<WatchListsResponse> | null;
  return !!w && Array.isArray(w.lists) && typeof w.note === "string";
}

/** Mirror entries by the long screen they mirror. */
export function mirrorsByScreen(m: MirrorsResponse | null): Record<string, MirrorEntry> {
  const out: Record<string, MirrorEntry> = {};
  for (const e of m?.mirrors ?? []) out[e.mirror_of] = e;
  return out;
}

export interface Choice { ref: string; label: string; method: string; mirror: boolean }

/** What the confluence view can run: every screen of the gallery, then each mirror that has a text (its own entry). */
export function confluenceChoices(tax: Taxonomy, mirrors: MirrorsResponse | null): Choice[] {
  const out: Choice[] = tax.screens.map((s) => ({ ref: s.id, label: s.name, method: s.method, mirror: false }));
  for (const m of mirrors?.mirrors ?? []) {
    if (m.text) out.push({ ref: m.id, label: m.name, method: m.method, mirror: true });
  }
  return out;
}

/** Adds or removes a screen; a pick beyond the maximum is ignored (the UI disables it). */
export function toggleChoice(selected: string[], ref: string, max = MAX_CONFLUENCE): string[] {
  if (selected.includes(ref)) return selected.filter((r) => r !== ref);
  return selected.length >= max ? selected : [...selected, ref];
}

export function methodLabel(tax: Taxonomy, id: string): string {
  return tax.methods.find((m) => m.id === id)?.label ?? id;
}

/** "2 methods · 3 screens" - a count of agreement, never a score. */
export function agreementText(row: ConfluenceRow): string {
  const m = row.method_count === 1 ? "1 method" : `${row.method_count} methods`;
  const s = row.screen_count === 1 ? "1 screen" : `${row.screen_count} screens`;
  return `${m} · ${s}`;
}

// --- U9 TX3: regime-aware greying and the setup-of-the-day digest (flag `screener_tx3`) ---------------------------------

export interface RegimeAxisReading { value: string | null; reasons: string[]; numbers: Record<string, number | string | null> }
export interface ScreenFit { fits: boolean | null; reason: string }
export interface RegimeResponse {
  as_of: string; session: string | null; index: string; trend: RegimeAxisReading; volatility: RegimeAxisReading; note: string;
  fits: Record<string, ScreenFit>;
}

export function isRegime(x: unknown): x is RegimeResponse {
  const r = x as Partial<RegimeResponse> | null;
  return !!r && !!r.fits && typeof r.fits === "object" && !!r.trend && !!r.volatility;
}

/** "Trending market · High volatility" from the taxonomy's labels ("unclear" where an axis has no clear reading). */
export function regimeText(r: RegimeResponse, tax: Taxonomy): string {
  const label = (id: string | null, axis: string) =>
    id ? (tax.regimes ?? []).find((v) => v.id === id)?.label ?? id : `${axis} unclear`;
  return `${label(r.trend.value, "trend")} · ${label(r.volatility.value, "volatility")}`;
}

/** Screens greyed for today's regime (only a clear mismatch greys a screen). */
export function greyed(r: RegimeResponse | null, screenId: string): ScreenFit | null {
  const f = r?.fits[screenId];
  return f && f.fits === false ? f : null;
}

export interface DigestFolder { method: string; label: string; screens: { ref: string; name: string; base_tf: string; first: string[]; more: number; evidence_id: number | null }[] }
export interface Digest { day: string; built_at: string; methods: DigestFolder[]; scanned: number; problems: string[]; note: string }
export interface DigestSettings { enabled: boolean; send_time: string; pinned: string[]; symbols: string[]; exchange: string }
export interface DigestState extends DigestSettings {
  last_sent_on: string | null; last_status: string | null; last_digest: Digest | null; messages?: string[];
  limits?: { pinned: number; symbols: number }; note?: string;
}

export function isDigestState(x: unknown): x is DigestState {
  const d = x as Partial<DigestState> | null;
  return !!d && typeof d.send_time === "string" && Array.isArray(d.pinned) && Array.isArray(d.symbols);
}

export const MAX_PINNED = 5;
