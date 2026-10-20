import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { TenantFeatures } from "../types";

/**
 * UI2-0 (UI v2 proposal §2, §12): the organisation's feature flags, read by the signed-in shell
 * (GET /api/system/features) and shared with every component through `useFeatures()`, instead of each page and card
 * fetching the same list on its own.
 *
 * Freshness, without re-reading on every click:
 * - a new signed-in user is read at once (the provider's own read), and so is a re-read of the same user (the top
 *   bar and charts used to read on `user`) and an admin flag change (`refresh()`, called by the Admin console);
 * - a route change and showing the tab again re-read at most once per REREAD_AFTER_MS (`refreshIfStale()`), so a
 *   flag change reaches every page within 30 s, and the server is not asked on every click (M3).
 * A re-read keeps the flags on screen until the answer is in, so nothing flickers.
 *
 * Safety:
 * - `loaded` is false until the first answer for this user, so "not known yet" is never "on".
 * - The first read for a user that fails means every flag is off (fail closed: a feature never shows by mistake).
 *   A later re-read that fails keeps the last good flags (L7). `answered` tells a real answer from that fallback.
 * - The answer is keyed by user: another account never sees the previous account's flags, not even for one render.
 * - Outside a provider (a test or a story renders one component alone), the hook reads for itself once.
 */
export interface FeaturesState {
  features: TenantFeatures;
  loaded: boolean;
  /** True only when the flag is known and on. */
  on: (name: string) => boolean;
  /** True when the flags come from a successful read (false before one, or when the first read failed). */
  answered: boolean;
  /** Re-reads the flags now (keeps the current ones until the answer is in). */
  refresh: () => void;
  /** Re-reads only when the last read started REREAD_AFTER_MS or more ago (route changes, tab shown again). */
  refreshIfStale: () => void;
}

/** Route changes and a tab shown again re-read the flags at most this often. */
export const REREAD_AFTER_MS = 30_000;

/** True when a throttled re-read may go out: never read yet, or the last read started REREAD_AFTER_MS ago or more. */
export function shouldReread(lastReadAt: number | null, now: number, minMs = REREAD_AFTER_MS): boolean {
  return lastReadAt === null || now - lastReadAt >= minMs;
}

const FeaturesContext = createContext<FeaturesState | null>(null);
const NONE: TenantFeatures = {};

type Key = number | string | null;
export interface FeaturesSnapshot { key: Key; features: TenantFeatures; loaded: boolean; answered: boolean }
type Snapshot = FeaturesSnapshot;
const EMPTY_SNAPSHOT: Snapshot = { key: null, features: NONE, loaded: false, answered: false };

/** After a failed read: keep this user's last good flags (L7); only a first read fails closed (every flag off). */
export function snapshotAfterError(prev: FeaturesSnapshot, key: Key): FeaturesSnapshot {
  return prev.key === key && prev.answered ? prev : { key, features: NONE, loaded: true, answered: false };
}

/** The theme default the shell may apply (UI-1): only from a real answer, so a failed read never clears the v2 hint. */
export function v2ThemeDefaultFrom(f: Pick<FeaturesState, "answered" | "on">): boolean | null {
  return f.answered ? f.on("ui_v2") : null;
}

/** The answer that belongs to `key` - another user's answer (or none) reads as "not loaded", every flag off. */
export function snapshotFor(snap: FeaturesSnapshot, enabled: boolean, key: Key): FeaturesSnapshot {
  return enabled && snap.key === key ? snap : EMPTY_SNAPSHOT;
}

/** The flags for `key` while `enabled`; a new key starts from "not loaded" without an extra render. */
function useFeaturesFetch(enabled: boolean, key: Key): FeaturesState {
  const [snap, setSnap] = useState<Snapshot>(EMPTY_SNAPSHOT);
  const [nonce, setNonce] = useState(0);
  const lastReadAt = useRef<number | null>(null);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const refreshIfStale = useCallback(() => { if (shouldReread(lastReadAt.current, Date.now())) refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    lastReadAt.current = Date.now();
    api.myFeatures()
      .then((f) => { if (live) setSnap({ key, features: f ?? NONE, loaded: true, answered: true }); })
      .catch(() => { if (live) setSnap((prev) => snapshotAfterError(prev, key)); });
    return () => { live = false; };
  }, [enabled, key, nonce]);
  const current = snapshotFor(snap, enabled, key);
  return useMemo(() => ({
    features: current.features,
    loaded: current.loaded,
    answered: current.answered,
    on: (name: string) => current.loaded && current.features[name] === true,
    refresh,
    refreshIfStale,
  }), [current, refresh, refreshIfStale]);
}

/**
 * Wraps the signed-in app. `userKey` is the signed-in user's id (null while signed out: nothing is read, every flag
 * reads off). `value` replaces the read entirely - for stories and tests that set the flags themselves.
 */
export function FeaturesProvider({ userKey, value, children }: { userKey: Key; value?: TenantFeatures; children: ReactNode }) {
  const fetched = useFeaturesFetch(value === undefined && userKey !== null, userKey);
  const { refreshIfStale } = fetched;
  useEffect(() => {
    if (value !== undefined || userKey === null) return;
    const onShow = () => { if (document.visibilityState === "visible") refreshIfStale(); };
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, [value, userKey, refreshIfStale]);
  const fixed = useMemo<FeaturesState | null>(() => (value === undefined ? null : {
    features: value, loaded: true, answered: true, on: (name: string) => value[name] === true,
    refresh: () => undefined, refreshIfStale: () => undefined,
  }), [value]);
  return <FeaturesContext.Provider value={fixed ?? fetched}>{children}</FeaturesContext.Provider>;
}

/** The organisation's flags: the shell's shared read, or this component's own when it renders outside the shell. */
export function useFeatures(): FeaturesState {
  const shared = useContext(FeaturesContext);
  const own = useFeaturesFetch(shared === null, "standalone");
  return shared ?? own;
}

/** UI2-0: true while `ui_v2` is on for this organisation (the redesigned pages). Off until known. */
export function useUiV2(): boolean {
  return useFeatures().on("ui_v2");
}
