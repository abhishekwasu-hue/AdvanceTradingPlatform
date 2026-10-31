import { useEffect, useSyncExternalStore } from "react";
import { ApiError, request } from "../api/client";
import { cleanStored, cleanView, withStored, type SetupStored, type SetupView } from "./model";

/**
 * UI2-7 (UI v2 proposal §11, §13 UI-8): the trader's setup progress, stored on the server per user (`GET/PUT
 * /api/me/setup`) so it resumes on any device. One store per signed-in user, shared by the Get started card (P3, both
 * dashboards), Settings and the wizard.
 *
 * - A localStorage copy per user is only a cache: it paints the last known progress before the server answers (every
 *   access in try/catch). The Get started card's "for good" mark waits for the server's answer, never the cache.
 * - A change (skip, dismiss, opened) shows at once and is saved as a compare-and-set on the version. On 409 (another
 *   device saved first) the change is applied again on the server's marks once; a second conflict keeps the server's.
 * - Signed out, nothing is read or sent.
 */

export interface SetupState {
  view: SetupView | null;
  /** The first read has finished (answered or failed). */
  loaded: boolean;
  /** The server has answered (the view is not only the cache). */
  fresh: boolean;
  error: string | null;
}

type Change = (stored: SetupStored) => SetupStored;

const CACHE_PREFIX = "atp_setup:";
const SAVE_FAILED = "Your setup progress was not saved. Check your connection and try again.";

/** The marks and version a 409 carries (`detail: {message, version, current}`), or null. */
export function setupConflict(error: unknown): { version: number; current: SetupStored } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  try {
    const detail = (JSON.parse(error.body) as { detail?: { version?: unknown; current?: unknown } }).detail;
    return typeof detail?.version === "number" ? { version: detail.version, current: cleanStored(detail.current) } : null;
  } catch {
    return null;
  }
}

export class SetupStore {
  private state: SetupState;
  private listeners = new Set<() => void>();
  private queue: Promise<void> = Promise.resolve();
  private loading: Promise<void> | null = null;

  constructor(readonly userKey: number | string, private readonly fetcher: typeof request = request) {
    this.state = { view: this.readCache(), loaded: false, fresh: false, error: null };
  }

  private readCache(): SetupView | null {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + this.userKey);
      return raw ? cleanView(JSON.parse(raw)) : null;
    } catch {
      return null;
    }
  }

  private set(next: Partial<SetupState>): void {
    this.state = { ...this.state, ...next };
    if (this.state.view) {
      try { localStorage.setItem(CACHE_PREFIX + this.userKey, JSON.stringify(this.state.view)); } catch { /* intentional: storage unavailable */ }
    }
    for (const l of this.listeners) l();
  }

  get = (): SetupState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** The first read (later calls do nothing). */
  loadOnce(): Promise<void> {
    this.loading ??= this.load();
    return this.loading;
  }

  /** Reads the server's answer again (after a step's action, so its "done" shows). Queued after any save. */
  load(): Promise<void> {
    this.queue = this.queue.then(async () => {
      try {
        const view = cleanView(await this.fetcher<unknown>("/me/setup"));
        this.set({ view: view ?? this.state.view, loaded: true, fresh: !!view || this.state.fresh, error: null });
      } catch {
        // The cached progress (if any) stays; nothing opens by itself on a guess.
        this.set({ loaded: true });
      }
    });
    return this.queue;
  }

  /** Applies a change to the marks at once and saves it (one save at a time, in order). */
  update(change: Change): Promise<void> {
    const view = this.state.view;
    if (view) this.set({ view: withStored(view, change(view.stored)), error: null });
    this.queue = this.queue.then(() => this.save(change, true));
    return this.queue;
  }

  private async save(change: Change, retry: boolean): Promise<void> {
    let view = this.state.view;
    if (!view || !this.state.fresh) {
      // Not read yet (or the read failed): read first, so a save never replaces another device's newer marks blindly.
      try {
        view = cleanView(await this.fetcher<unknown>("/me/setup"));
      } catch {
        view = null;
      }
      if (!view) { this.set({ error: SAVE_FAILED }); return; }
      this.set({ view, loaded: true, fresh: true });
    }
    const stored = change(view.stored);
    this.set({ view: withStored(view, stored) });
    try {
      const saved = cleanView(await this.fetcher<unknown>(`/me/setup?version=${view.version}`, { method: "PUT", body: JSON.stringify(stored) }));
      if (saved) this.set({ view: saved, error: null });
    } catch (error) {
      const conflict = setupConflict(error);
      if (!conflict) { this.set({ error: SAVE_FAILED }); return; }
      this.set({ view: withStored({ ...view, version: conflict.version }, conflict.current) });
      if (retry) return this.save(change, false);
      this.set({ error: "Your setup was changed on another device. That progress is shown now." });
    }
  }
}

const stores = new Map<number | string, SetupStore>();

/** The store of this user, or null while signed out. Signing out, or another user signing in, drops the other stores. */
export function setupStore(userKey: number | string | null): SetupStore | null {
  for (const key of [...stores.keys()]) if (key !== userKey) stores.delete(key);
  if (userKey == null) return null;
  let store = stores.get(userKey);
  if (!store) {
    store = new SetupStore(userKey);
    stores.set(userKey, store);
  }
  return store;
}

const SIGNED_OUT: SetupState = { view: null, loaded: false, fresh: false, error: null };
const signedOut = () => SIGNED_OUT;
const noop = () => () => {};

/** The signed-in trader's setup progress, a change function and a re-read (each does nothing while signed out). */
export function useSetup(userKey: number | string | null): SetupState & { update: (change: Change) => void; reload: () => void } {
  const store = setupStore(userKey);
  useEffect(() => { store?.loadOnce(); }, [store]);
  const state = useSyncExternalStore(store?.subscribe ?? noop, store?.get ?? signedOut, signedOut);
  return { ...state, update: (change) => { void store?.update(change); }, reload: () => { void store?.load(); } };
}
