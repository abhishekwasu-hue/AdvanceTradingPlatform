import { useFeatures } from "../ui/useFeatures";

/**
 * Settings redesign B3: true while nothing about LIVE may show in the trader's view - "paper only" is on, or the flags
 * are not known (fail safe: hidden until a real answer says LIVE is in play). LIVE-1 (owner order: ONE live switch):
 * the server derives `phase0_paper_only` from `live_trading` (NOT live_trading, for everyone), so this follows the one
 * switch. The mode pickers read `useLiveAvailable` (src/settings/useLiveAvailable.ts). The components stay; the admin
 * keeps the platform go-live checklist on /admin.
 */
export function liveHiddenFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return !f.answered || f.on("phase0_paper_only");
}

export function useLiveHidden(): boolean {
  return liveHiddenFrom(useFeatures());
}
