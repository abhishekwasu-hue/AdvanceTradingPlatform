import { useFeatures } from "../ui/useFeatures";

/**
 * LIVE-1 (owner order: ONE live switch): true when the trader may pick LIVE on an order path - the `live_trading`
 * switch is ON. The server answers its global switch only and derives "paper only" from it (`phase0_paper_only` = NOT
 * `live_trading`; the former lock and organisation grant decide nothing); both must agree here, so a picker never shows
 * Live on a partial answer. Fail safe: false until a real answer says so. Every Paper / Live mode picker reads this one
 * hook; PAPER stays the default choice everywhere, and the server refuses LIVE on its own whenever the switch is off.
 */
export function liveAvailableFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return f.answered && f.on("live_trading") && !f.on("phase0_paper_only");
}

export function useLiveAvailable(): boolean {
  return liveAvailableFrom(useFeatures());
}

/** The plain sentence a mode picker shows when LIVE is not available (the same words as the server's refusal). */
export const LIVE_OFF_TEXT = "Live trading is switched off by the admin, so only paper orders can be placed now.";
