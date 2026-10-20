import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { TenantFeatures } from "../types";

/**
 * UI2-0 (UI v2 proposal §2, §12): the organisation's feature flags, fetched ONCE for the signed-in shell
 * (GET /api/system/features) and shared with every component through `useFeatures()`, instead of each page and card
 * fetching the same list on mount.
 *
 * - `loaded` is false until the answer is in, so a component can tell "not known yet" from "off".
 * - A failed read means every flag is off (as each component did on its own before): a feature never shows by mistake.
 * - The provider refetches when the signed-in user changes (sign-in, sign-out, another account).
 * - Outside a provider (a test or a story renders one component alone), the hook fetches for itself once.
 */
export interface FeaturesState {
  features: TenantFeatures;
  loaded: boolean;
  /** True only when the flag is known and on. */
  on: (name: string) => boolean;
}

const FeaturesContext = createContext<FeaturesState | null>(null);

function stateOf(features: TenantFeatures, loaded: boolean): FeaturesState {
  return { features, loaded, on: (name: string) => loaded && features[name] === true };
}

/** One read of /system/features while `enabled`; resets to "not loaded" when `key` changes. */
function useFeaturesFetch(enabled: boolean, key: unknown): FeaturesState {
  const [features, setFeatures] = useState<TenantFeatures>({});
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    setFeatures({});
    setLoaded(false);
    if (!enabled) return;
    let live = true;
    api.myFeatures()
      .then((f) => { if (live) { setFeatures(f ?? {}); setLoaded(true); } })
      .catch(() => { if (live) { setFeatures({}); setLoaded(true); } });
    return () => { live = false; };
  }, [enabled, key]);
  return useMemo(() => stateOf(features, loaded), [features, loaded]);
}

/**
 * Wraps the signed-in app. `userKey` is the signed-in user's id (null while signed out: nothing is fetched, every flag
 * reads off). `value` replaces the fetch entirely - for stories and tests that set the flags themselves.
 */
export function FeaturesProvider({ userKey, value, children }: { userKey: number | string | null; value?: TenantFeatures; children: ReactNode }) {
  const fetched = useFeaturesFetch(value === undefined && userKey !== null, userKey);
  const fixed = useMemo(() => (value === undefined ? null : stateOf(value, true)), [value]);
  return <FeaturesContext.Provider value={fixed ?? fetched}>{children}</FeaturesContext.Provider>;
}

/** The organisation's flags: the shell's single read, or this component's own when it renders outside the shell. */
export function useFeatures(): FeaturesState {
  const shared = useContext(FeaturesContext);
  const own = useFeaturesFetch(shared === null, "standalone");
  return shared ?? own;
}

/** UI2-0: true while `ui_v2` is on for this organisation (the redesigned pages). Off until known. */
export function useUiV2(): boolean {
  return useFeatures().on("ui_v2");
}
