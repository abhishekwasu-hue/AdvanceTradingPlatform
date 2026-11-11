import { useFeatures } from "../ui/useFeatures";

/**
 * LIVE-1 (owner order: ONE live switch): true when the trader may pick LIVE on any order path - the `live_trading`
 * switch is ON. The server derives the "paper only" answer from that one switch (GET /api/system/features answers
 * `phase0_paper_only` = NOT `live_trading`, for everyone; the former lock and organisation grant decide nothing), so this
 * hook reads that derived answer - the same one every existing "no LIVE wording in the paper phase" check reads.
 * Fail safe: false until a real answer says so. Every Paper / Live mode picker reads this one hook; PAPER stays the
 * default choice everywhere, and the server refuses LIVE on its own whenever the switch is off.
 */
export function liveAvailableFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return f.answered && !f.on("phase0_paper_only");
}

export function useLiveAvailable(): boolean {
  return liveAvailableFrom(useFeatures());
}

/** The plain sentence a mode picker shows when LIVE is not available (the same words as the server's refusal). */
export const LIVE_OFF_TEXT = "Live trading is switched off by the admin, so only paper orders can be placed now.";
