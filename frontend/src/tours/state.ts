import { useEffect, useSyncExternalStore } from "react";
import { ApiError, request } from "../api/client";
import { isTourId, type TourId } from "./pages";

/**
 * MO2 part C: the first-time guidance marks, stored on the server per user (`/api/me/prefs`, namespace `tours`, backend
 * app/me/prefs.py ToursPrefs) so a tip seen on the phone is not shown again on the desktop.
 *
 * - `welcome_done_at`: the welcome tour was finished or closed. `seen[page]`: that page's tips were finished or closed.
 *   `skip_all_at`: "Skip all tips" - no tour starts by itself any more (the "?" button still replays one).
 * - The server's answer is the only thing a tour starts on (`fresh`). A localStorage copy per user is only a paint cache
 *   (the "?" button and "Skip all tips" show before the server answers); every access is in try/catch.
 * - A server that does not know the namespace (an older release) or cannot be reached starts nothing: guidance is never
 *   pushed on a guess.
 * - A change shows at once and is saved as a compare-and-set on the version; on 409 (another device saved first) it is
 *   applied again on the server's marks once. Signed out, nothing is read or sent.
 * - Replays ("?", Help) are requests the shell's tour host listens to (`onRequest`), so Settings can ask for one.
 */

export interface TourMarks {
  welcome_done_at: string | null;
  skip_all_at: string | null;
  seen: Partial<Record<TourId, string>>;
}

export const NO_MARKS: TourMarks = { welcome_done_at: null, skip_all_at: null, seen: {} };

export interface ToursState {
  marks: TourMarks;
  /** The server answered with the marks (not only the cache). */
  fresh: boolean;
  /** Marks are known, from the server or the cache. */
  known: boolean;
  error: string | null;
}

export type TourRequest = { kind: "welcome" } | { kind: "page"; id: TourId };

type Change = (marks: TourMarks) => TourMarks;

const CACHE_PREFIX = "atp_tours:";
const SAVE_FAILED = "Your tips setting was not saved. Check your connection and try again.";

const isTime = (v: unknown): v is string => typeof v === "string" && v.length > 0 && !Number.isNaN(Date.parse(v));

/** The marks from anything the server or the cache holds; whatever does not fit is dropped. */
export function cleanMarks(raw: unknown): TourMarks | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const seen: Partial<Record<TourId, string>> = {};
  if (r.seen && typeof r.seen === "object" && !Array.isArray(r.seen)) {
    for (const [k, v] of Object.entries(r.seen as Record<string, unknown>)) if (isTourId(k) && isTime(v)) seen[k] = v;
  }
  return { welcome_done_at: isTime(r.welcome_done_at) ? r.welcome_done_at : null, skip_all_at: isTime(r.skip_all_at) ? r.skip_all_at : null, seen };
}

// --- the rules, pure ------------------------------------------------------------------------------------------------

/** The welcome tour opens by itself on the first sign-in: never finished, tips not skipped. Not for a VIEWER, who cannot
 * connect a broker or start a trading bot. */
export function welcomeDue(state: Pick<ToursState, "marks" | "fresh">, role: string | null | undefined): boolean {
  return state.fresh && role !== "VIEWER" && !state.marks.skip_all_at && !state.marks.welcome_done_at;
}

/** A page tour opens by itself on the first visit: server marks read, tips not skipped, the welcome out of the way. */
export function pageTourDue(state: Pick<ToursState, "marks" | "fresh">, id: TourId | null, role: string | null | undefined): boolean {
  if (!id || !state.fresh || state.marks.skip_all_at) return false;
  if (welcomeDue(state, role)) return false;
  return !state.marks.seen[id];
}

export const markSeen = (id: TourId, at: string): Change => (m) => ({ ...m, seen: { ...m.seen, [id]: m.seen[id] ?? at } });
export const markWelcomeDone = (at: string): Change => (m) => ({ ...m, welcome_done_at: m.welcome_done_at ?? at });
export const skipAllTips = (at: string): Change => (m) => ({ ...m, skip_all_at: at, welcome_done_at: m.welcome_done_at ?? at });
/** Help -> "Show all tips again": every mark cleared, so the welcome and each page's tips show once more. */
export const showAllTipsAgain: Change = () => ({ ...NO_MARKS, seen: {} });

/** The tour that opens by itself now, if any: the welcome first, then the page's own tips. */
export function autoTour(state: Pick<ToursState, "marks" | "fresh">, role: string | null | undefined, id: TourId | null): TourRequest | null {
  if (welcomeDue(state, role)) return { kind: "welcome" };
  if (id && pageTourDue(state, id, role)) return { kind: "page", id };
  return null;
}

/** How a tour ended: finished, closed (Skip, X, Esc, a tap outside), "Skip all tips", or the welcome's last button. */
export type CloseReason = "done" | "skip" | "skip-all" | "cta";

export interface Closed {
  /** The mark to save. */
  change: Change;
  /** Where to go next (the welcome's "Connect your broker"). */
  go: string | null;
  /** True: no tip opens by itself again on this page in this visit (the welcome was closed early). */
  hold: boolean;
}

/** What closing a tour does: the welcome is marked done (its button opens the Broker page), a page's tips are marked
 * seen - replayed or not - and "Skip all tips" turns every automatic tip off. */
export function closeTour(tour: TourRequest, reason: CloseReason, now: string, brokerPath = "/broker"): Closed {
  if (reason === "skip-all") return { change: skipAllTips(now), go: null, hold: true };
  if (tour.kind === "welcome") return { change: markWelcomeDone(now), go: reason === "cta" ? brokerPath : null, hold: reason !== "cta" };
  return { change: markSeen(tour.id, now), go: null, hold: false };
}

/** The 409 body (`detail: {message, version, current}`), or null. */
export function toursConflict(error: unknown): { version: number; current: TourMarks } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  try {
    const detail = (JSON.parse(error.body) as { detail?: { version?: unknown; current?: unknown } }).detail;
    return typeof detail?.version === "number" ? { version: detail.version, current: cleanMarks(detail.current) ?? NO_MARKS } : null;
  } catch {
    return null;
  }
}

interface PrefsRead { prefs?: { tours?: unknown }; versions?: { tours?: unknown } }

export class ToursStore {
  private state: ToursState;
  private version = 0;
  private listeners = new Set<() => void>();
  private requestListeners = new Set<(r: TourRequest) => void>();
  private queue: Promise<void> = Promise.resolve();
  private loading: Promise<void> | null = null;

  constructor(readonly userKey: number | string, private readonly fetcher: typeof request = request) {
    const cached = this.readCache();
    this.state = { marks: cached ?? NO_MARKS, fresh: false, known: cached !== null, error: null };
  }

  private readCache(): TourMarks | null {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + this.userKey);
      return raw ? cleanMarks(JSON.parse(raw)) : null;
    } catch {
      return null;
    }
  }

  private set(next: Partial<ToursState>): void {
    this.state = { ...this.state, ...next };
    if (this.state.known) {
      try { localStorage.setItem(CACHE_PREFIX + this.userKey, JSON.stringify(this.state.marks)); } catch { /* intentional: storage unavailable */ }
    }
    for (const l of this.listeners) l();
  }

  get = (): ToursState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** Asks the shell's tour host to play a tour now (the "?" button, Help). */
  request(r: TourRequest): void {
    for (const l of this.requestListeners) l(r);
  }

  onRequest(listener: (r: TourRequest) => void): () => void {
    this.requestListeners.add(listener);
    return () => { this.requestListeners.delete(listener); };
  }

  loadOnce(): Promise<void> {
    this.loading ??= this.load();
    return this.loading;
  }

  private async read(): Promise<{ marks: TourMarks; version: number } | null> {
    try {
      const r = await this.fetcher<PrefsRead>("/me/prefs");
      const marks = cleanMarks(r?.prefs?.tours);
      if (!marks) return null;                                  // a server without the namespace: nothing starts
      const version = typeof r?.versions?.tours === "number" ? r.versions.tours : 0;
      return { marks, version };
    } catch {
      return null;
    }
  }

  load(): Promise<void> {
    this.queue = this.queue.then(async () => {
      const got = await this.read();
      if (!got) return;
      this.version = got.version;
      this.set({ marks: got.marks, fresh: true, known: true, error: null });
    });
    return this.queue;
  }

  /** Applies a change at once and saves it (one save at a time, in order). */
  update(change: Change): Promise<void> {
    this.set({ marks: change(this.state.marks), known: true, error: null });
    this.queue = this.queue.then(() => this.save(change, true));
    return this.queue;
  }

  private async save(change: Change, retry: boolean): Promise<void> {
    if (!this.state.fresh) {
      // Never read from the server: read first, so a save never replaces another device's newer marks blindly.
      const got = await this.read();
      if (!got) { this.set({ error: SAVE_FAILED }); return; }
      this.version = got.version;
      this.set({ marks: change(got.marks), fresh: true });
    }
    const marks = this.state.marks;
    try {
      const saved = await this.fetcher<{ value?: unknown; version?: unknown }>(`/me/prefs/tours?version=${this.version}`,
        { method: "PUT", body: JSON.stringify(marks) });
      if (typeof saved?.version === "number") this.version = saved.version;
      this.set({ marks: cleanMarks(saved?.value) ?? marks, error: null });
    } catch (error) {
      const conflict = toursConflict(error);
      if (!conflict) { this.set({ error: SAVE_FAILED }); return; }
      this.version = conflict.version;
      this.set({ marks: change(conflict.current) });
      if (retry) return this.save(change, false);
      this.set({ marks: conflict.current, error: "Your tips setting was changed on another device. That setting is shown now." });
    }
  }
}

const stores = new Map<number | string, ToursStore>();

/** The store of this user, or null while signed out. Another user signing in drops the other stores. */
export function toursStore(userKey: number | string | null): ToursStore | null {
  for (const key of [...stores.keys()]) if (key !== userKey) stores.delete(key);
  if (userKey == null) return null;
  let store = stores.get(userKey);
  if (!store) {
    store = new ToursStore(userKey);
    stores.set(userKey, store);
  }
  return store;
}

const SIGNED_OUT: ToursState = { marks: NO_MARKS, fresh: false, known: false, error: null };
const signedOut = () => SIGNED_OUT;
const noop = () => () => {};

/** The signed-in user's tour marks, read from the server once per user. */
export function useTours(userKey: number | string | null): ToursState & { store: ToursStore | null } {
  const store = toursStore(userKey);
  const state = useSyncExternalStore(store ? store.subscribe : noop, store ? store.get : signedOut, store ? store.get : signedOut);
  useEffect(() => { void store?.loadOnce(); }, [store]);
  return { ...state, store };
}
