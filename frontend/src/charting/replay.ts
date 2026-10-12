/**
 * N4 CH3: bar replay. The chart shows the candles up to the replay bar only, and the price-action layer is asked for
 * as of that bar's end (`barEndIso`), so the backend recomputes it from the bars closed by then - replay never shows
 * future data. The window start is fixed at the first step (`start`), so every step reads the same history and only
 * adds bars. Pure functions; the controls are `ReplayBar.tsx`.
 */
import { barEndIso, type LayerTf } from "./priceAction";

export interface ReplayState { active: boolean; index: number; playing: boolean; start: string | null }
export const REPLAY_OFF: ReplayState = { active: false, index: 0, playing: false, start: null };
/** How many bars back from the newest a replay starts. */
export const REPLAY_BACK = 100;

export function startReplay(count: number, back = REPLAY_BACK): ReplayState {
  return { active: count > 0, index: Math.max(0, count - 1 - back), playing: false, start: null };
}

/** Move by `delta` bars, kept inside the loaded candles; reaching the newest bar stops playing. */
export function stepReplay(state: ReplayState, delta: number, count: number): ReplayState {
  if (!state.active || count === 0) return state;
  const index = Math.min(count - 1, Math.max(0, state.index + delta));
  return { ...state, index, playing: state.playing && index < count - 1 };
}

/** The candles the chart may show: all of them outside replay, else those up to the replay bar (nothing later). */
export function visibleCandles<T>(candles: T[], state: ReplayState): T[] {
  return state.active ? candles.slice(0, Math.min(candles.length, state.index + 1)) : candles;
}

/** The as-of time of the replay bar (its end), or null outside replay (the layer then reads as of now). */
export function replayAt(candles: readonly { timestamp: string }[], state: ReplayState, tf: LayerTf | null): string | null {
  if (!state.active || !tf || candles.length === 0) return null;
  const bar = candles[Math.min(candles.length - 1, state.index)];
  const startSec = Math.floor(Date.parse(bar.timestamp) / 1000);
  return Number.isFinite(startSec) ? barEndIso(startSec, tf) : null;
}
