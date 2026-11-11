import { useFeatures } from "../ui/useFeatures";

/**
 * LIVE-1 (owner order: ONE live switch): true when the trader may pick LIVE on any order path - the `live_trading`
 * switch is ON (the server answers its global switch only; the former PAPER-only lock and organisation grant follow it).
 * Fail safe: false until a real answer says so. Every Paper / Live mode picker reads this one hook; PAPER stays the
 * default choice everywhere, and the server refuses LIVE on its own whenever the switch is off.
 */
export function liveAvailableFrom(f: { answered: boolean; on: (flag: string) => boolean }): boolean {
  return f.answered && f.on("live_trading");
}

export function useLiveAvailable(): boolean {
  return liveAvailableFrom(useFeatures());
}

/** The plain sentence a mode picker shows when LIVE is not available (the same words as the server's refusal). */
export const LIVE_OFF_TEXT = "Live trading is switched off by the admin, so only paper orders can be placed now.";
