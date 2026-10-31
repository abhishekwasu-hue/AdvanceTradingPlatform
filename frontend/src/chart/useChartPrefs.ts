/**
 * UI2-4b1 (Charts page plan §3, CHP-11 / 12 / 13): the chart settings, stored on the server per user
 * (`/api/me/prefs`, namespace `chart`, UI2-4a1) so phone and desktop agree, and the one place every v2 chart reads them.
 *
 * - One store per signed-in user, shared by every chart on the page: a change in one settings popover shows on all.
 * - A localStorage copy per user paints the last known settings before the server answers (every access in try/catch:
 *   a private window or blocked storage only loses the copy). Signed out, nothing is read or sent: the defaults.
 * - A save is a compare-and-set on the version. On 409 (another device saved first) the server's value is taken and
 *   this change is applied on top of it once more, so neither device's change is lost; a second conflict keeps the
 *   server's value.
 */
import { useEffect, useSyncExternalStore } from "react";
import { ApiError, request } from "../api/client";
import type { CandleColors, ChartThemeChoice } from "./chartTheme";
import { DEFAULT_FAVOURITES, cleanFavourites, type TimeframeId } from "./timeframes";

/** `chart-prefs/1` (backend `app/me/prefs.py` ChartPrefs). */
export interface ChartPrefs {
  theme: ChartThemeChoice;
  grid: boolean;
  candle_colors: CandleColors | null;
  indicators: { volume: boolean; rsi: boolean };
  reader_overlay: boolean;
  /** TF-1: the starred timeframes, shown as one-click buttons beside the timeframe dropdown. */
  timeframe_favourites: TimeframeId[];
}

/** The server's defaults: the app theme, no grid, the theme's candle colours, volume and RSI on (CHP-13). */
export const DEFAULT_CHART_PREFS: ChartPrefs = {
  theme: "app", grid: false, candle_colors: null, indicators: { volume: true, rsi: true }, reader_overlay: false,
  timeframe_favourites: [...DEFAULT_FAVOURITES],
};

export type ChartPrefsPatch = Partial<Omit<ChartPrefs, "indicators">> & { indicators?: Partial<ChartPrefs["indicators"]> };

const HEX = /^#[0-9a-f]{6}$/i;
const CACHE_PREFIX = "atp_chart_prefs:";

/** Any value (the server's, the cache's) as valid prefs: a field that is missing or wrong takes its default. */
export function cleanPrefs(raw: unknown): ChartPrefs {
  const v = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const ind = (v.indicators && typeof v.indicators === "object" ? v.indicators : {}) as Record<string, unknown>;
  const cc = v.candle_colors as Record<string, unknown> | null | undefined;
  const colors = cc && typeof cc.up === "string" && typeof cc.down === "string" && HEX.test(cc.up) && HEX.test(cc.down)
    && cc.up.toLowerCase() !== cc.down.toLowerCase() ? { up: cc.up, down: cc.down } : null;
  const bool = (x: unknown, d: boolean) => (typeof x === "boolean" ? x : d);
  const d = DEFAULT_CHART_PREFS;
  return {
    theme: v.theme === "light" || v.theme === "dark" || v.theme === "app" ? v.theme : d.theme,
    grid: bool(v.grid, d.grid),
    candle_colors: colors,
    indicators: { volume: bool(ind.volume, d.indicators.volume), rsi: bool(ind.rsi, d.indicators.rsi) },
    reader_overlay: bool(v.reader_overlay, d.reader_overlay),
    timeframe_favourites: cleanFavourites(v.timeframe_favourites),
  };
}

export function applyPatch(prefs: ChartPrefs, patch: ChartPrefsPatch): ChartPrefs {
  return cleanPrefs({ ...prefs, ...patch, indicators: { ...prefs.indicators, ...patch.indicators } });
}

/** The current value and version a 409 carries (`detail: {message, version, current}`), or null. */
export function conflictOf(error: unknown): { version: number; current: ChartPrefs } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  try {
    const detail = (JSON.parse(error.body) as { detail?: { version?: unknown; current?: unknown } }).detail;
    return typeof detail?.version === "number" ? { version: detail.version, current: cleanPrefs(detail.current) } : null;
  } catch {
    return null;
  }
}

export interface ChartPrefsState { prefs: ChartPrefs; loaded: boolean; error: string | null }

/** The prefs of one user. `fetcher` is the API client's `request` (a test passes a fake). */
export class ChartPrefsStore {
  private state: ChartPrefsState;
  private version: number | null = null;           // null until read: a save then replaces without a version check
  private listeners = new Set<() => void>();
  private queue: Promise<void> = Promise.resolve();
  private loading: Promise<void> | null = null;

  constructor(readonly userKey: number | string, private readonly fetcher: typeof request = request) {
    this.state = { prefs: this.readCache() ?? DEFAULT_CHART_PREFS, loaded: false, error: null };
  }

  private readCache(): ChartPrefs | null {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + this.userKey);
      return raw ? cleanPrefs(JSON.parse(raw)) : null;
    } catch {
      return null;
    }
  }

  private set(next: Partial<ChartPrefsState>): void {
    this.state = { ...this.state, ...next };
    try { localStorage.setItem(CACHE_PREFIX + this.userKey, JSON.stringify(this.state.prefs)); } catch { /* intentional: storage unavailable */ }
    for (const l of this.listeners) l();
  }

  get = (): ChartPrefsState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** The first read of this user's settings (later calls do nothing). */
  loadOnce(): Promise<void> {
    this.loading ??= this.load();
    return this.loading;
  }

  /** Reads the server's value (queued: a change made meanwhile is saved after it, on the version read). */
  load(): Promise<void> {
    this.queue = this.queue.then(async () => {
      try {
        const r = await this.fetcher<{ prefs: { chart?: unknown }; versions: { chart?: number } }>("/me/prefs");
        this.version = r.versions?.chart ?? 0;
        this.set({ prefs: cleanPrefs(r.prefs?.chart), loaded: true, error: null });
      } catch {
        // The cached (or default) settings stay on screen; a change is still saved.
        this.set({ loaded: true, error: "Chart settings could not be loaded. Changes still apply on this device." });
      }
    });
    return this.queue;
  }

  /** Applies a change at once and saves it (one save at a time, in order). */
  update(patch: ChartPrefsPatch): Promise<void> {
    this.set({ prefs: applyPatch(this.state.prefs, patch), error: null });
    this.queue = this.queue.then(() => this.save(patch, true));
    return this.queue;
  }

  // The patch is applied again on the value at save time (a patch holds whole values, so twice is the same as once):
  // the server's value may have come in, or a conflict replaced it, since the change was made.
  private async save(patch: ChartPrefsPatch, retry: boolean): Promise<void> {
    if (this.version == null) {
      // The first read failed: read again before writing, so a save never replaces another device's newer value blindly.
      try {
        const r = await this.fetcher<{ prefs: { chart?: unknown }; versions: { chart?: number } }>("/me/prefs");
        this.version = r.versions?.chart ?? 0;
        this.set({ prefs: cleanPrefs(r.prefs?.chart), loaded: true });
      } catch {
        this.set({ error: "Chart settings were not saved. Check your connection and try again." });
        return;
      }
    }
    const value = applyPatch(this.state.prefs, patch);
    this.set({ prefs: value });
    try {
      const r = await this.fetcher<{ version: number }>(`/me/prefs/chart?version=${this.version}`, { method: "PUT", body: JSON.stringify(value) });
      this.version = r.version;
    } catch (error) {
      const conflict = conflictOf(error);
      if (!conflict) { this.set({ error: "Chart settings were not saved. Check your connection and try again." }); return; }
      this.version = conflict.version;
      this.set({ prefs: conflict.current });
      if (retry) return this.save(patch, false);
      this.set({ error: "Chart settings were changed on another device. Those settings are shown now." });
    }
  }
}

const stores = new Map<number | string, ChartPrefsStore>();

/** The store of this user (one per user for the page's life), or null while signed out. */
export function chartPrefsStore(userKey: number | string | null): ChartPrefsStore | null {
  if (userKey == null) return null;
  let store = stores.get(userKey);
  if (!store) {
    store = new ChartPrefsStore(userKey);
    stores.set(userKey, store);
  }
  return store;
}

const SIGNED_OUT: ChartPrefsState = { prefs: DEFAULT_CHART_PREFS, loaded: false, error: null };
const signedOut = () => SIGNED_OUT;
const noop = () => () => {};

/** The chart settings of the signed-in user and the function that changes them (does nothing while signed out). */
export function useChartPrefs(userKey: number | string | null): ChartPrefsState & { update: (patch: ChartPrefsPatch) => void } {
  const store = chartPrefsStore(userKey);                // the cached copy shows on the first paint
  useEffect(() => { store?.loadOnce(); }, [store]);
  const state = useSyncExternalStore(store?.subscribe ?? noop, store?.get ?? signedOut, signedOut);
  return { ...state, update: (patch) => { void store?.update(patch); } };
}
