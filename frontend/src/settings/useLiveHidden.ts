import { useFeatures } from "../ui/useFeatures";
import { liveAvailableFrom } from "./useLiveAvailable";

/**
 * Settings redesign B3: true while nothing about LIVE may show in the trader's view - LIVE-1: the one live switch
 * (`live_trading`) is off, or the flags are not known (fail safe: hidden until a real answer says LIVE is available).
 * The components stay; the admin keeps the platform go-live checklist on /admin.
 */
export function liveHiddenFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return !liveAvailableFrom(f);
}

export function useLiveHidden(): boolean {
  return liveHiddenFrom(useFeatures());
}
