import { useFeatures } from "../ui/useFeatures";

/**
 * Settings redesign B3: true while nothing about LIVE may show in the trader's view - `phase0_paper_only` is on, or the
 * flags are not known (fail safe: hidden until a real answer says Phase 0 is over). The components stay; the admin keeps
 * the platform go-live checklist on /admin.
 */
export function liveHiddenFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return !f.answered || f.on("phase0_paper_only");
}

export function useLiveHidden(): boolean {
  return liveHiddenFrom(useFeatures());
}
