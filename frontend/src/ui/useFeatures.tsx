import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { TenantFeatures } from "../types";

/**
 * UI2-0 (UI v2 proposal §2, §12): the organisation's feature flags, read by the signed-in shell
 * (GET /api/system/features) and shared with every component through `useFeatures()`, instead of each page and card
 * fetching the same list on its own.
 *
 * Freshness - at least as fresh as the per-component reads it replaces:
 * - the shell re-reads on every route change (a page used to read on mount), when the signed-in user changes or is
 *   re-read (the top bar and charts used to read on `user`), and when this tab is shown again;
 * - `refresh()` re-reads at once (the Admin console calls it after changing a flag).
 * A re-read keeps the flags on screen until the answer is in, so nothing flickers.
 *
 * Safety:
 * - `loaded` is false until the first answer for this user, so "not known yet" is never "on".
 * - A failed read means every flag is off (as each component did on its own before): a feature never shows by mistake.
 * - The answer is keyed by user: another account never sees the previous account's flags, not even for one render.
 * - Outside a provider (a test or a story renders one component alone), the hook reads for itself once.
 */
export interface FeaturesState {
  features: TenantFeatures;
  loaded: boolean;
  /** True only when the flag is known and on. */
  on: (name: string) => boolean;
  /** Re-reads the flags now (keeps the current ones until the answer is in). */
  refresh: () => void;
}

const FeaturesContext = createContext<FeaturesState | null>(null);
const NONE: TenantFeatures = {};

type Key = number | string | null;
export interface FeaturesSnapshot { key: Key; features: TenantFeatures; loaded: boolean }
type Snapshot = FeaturesSnapshot;
const EMPTY_SNAPSHOT: Snapshot = { key: null, features: NONE, loaded: false };

/** The answer that belongs to `key` - another user's answer (or none) reads as "not loaded", every flag off. */
export function snapshotFor(snap: FeaturesSnapshot, enabled: boolean, key: Key): FeaturesSnapshot {
  return enabled && snap.key === key ? snap : EMPTY_SNAPSHOT;
}

/** The flags for `key` while `enabled`; a new key starts from "not loaded" without an extra render. */
function useFeaturesFetch(enabled: boolean, key: Key): FeaturesState {
  const [snap, setSnap] = useState<Snapshot>(EMPTY_SNAPSHOT);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    api.myFeatures()
      .then((f) => { if (live) setSnap({ key, features: f ?? NONE, loaded: true }); })
      .catch(() => { if (live) setSnap({ key, features: NONE, loaded: true }); });
    return () => { live = false; };
  }, [enabled, key, nonce]);
  const current = snapshotFor(snap, enabled, key);
  return useMemo(() => ({
    features: current.features,
    loaded: current.loaded,
    on: (name: string) => current.loaded && current.features[name] === true,
    refresh,
  }), [current, refresh]);
}

/**
 * Wraps the signed-in app. `userKey` is the signed-in user's id (null while signed out: nothing is read, every flag
 * reads off). `value` replaces the read entirely - for stories and tests that set the flags themselves.
 */
export function FeaturesProvider({ userKey, value, children }: { userKey: Key; value?: TenantFeatures; children: ReactNode }) {
  const fetched = useFeaturesFetch(value === undefined && userKey !== null, userKey);
  const { refresh } = fetched;
  useEffect(() => {
    if (value !== undefined || userKey === null) return;
    const onShow = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, [value, userKey, refresh]);
  const fixed = useMemo<FeaturesState | null>(() => (value === undefined ? null : {
    features: value, loaded: true, on: (name: string) => value[name] === true, refresh: () => undefined,
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
