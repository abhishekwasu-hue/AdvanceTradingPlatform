/**
 * N4 CH3: bar-replay controls. Start goes REPLAY_BACK bars back; step back / forward one bar; play moves one bar a
 * second until the newest bar; Exit shows the live chart again. The chart and its layers follow `state` (replay.ts):
 * candles up to the replay bar only, the price-action layer and the zones as of that bar's end.
 */
import { useEffect } from "react";
import { formatDateTimeIST } from "../ui/format";
import { REPLAY_OFF, startReplay, stepReplay, type ReplayState } from "./replay";

const btn = "rounded px-1.5 py-0.5 text-xs font-semibold text-fg-muted hover:text-fg disabled:opacity-40";
export const PLAY_MS = 1000;

export function ReplayBar({ state, count, at, onChange }: { state: ReplayState; count: number; at: string | null; onChange: (s: ReplayState) => void }) {
  useEffect(() => {
    if (!state.active || !state.playing) return;
    const id = setInterval(() => onChange(stepReplay(state, 1, count)), PLAY_MS);
    return () => clearInterval(id);
  }, [state, count, onChange]);

  if (!state.active) {
    return (
      <div className="flex items-center gap-2 text-xs text-fg-muted" data-testid="replay-bar">
        <button type="button" className={btn} disabled={count < 2} onClick={() => onChange(startReplay(count))}
                title="Replay the chart bar by bar; the price-action layer is recomputed as of each bar">Replay</button>
      </div>
    );
  }
  const atText = at ? formatDateTimeIST(new Date(at), { year: false, suffix: false }) : "";
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted" data-testid="replay-bar" role="toolbar" aria-label="Replay">
      <span className="font-semibold text-warn">Replay</span>
      <button type="button" className={btn} disabled={state.index <= 0} onClick={() => onChange({ ...stepReplay(state, -1, count), playing: false })}>◀ Bar</button>
      <button type="button" className={btn} disabled={state.index >= count - 1} onClick={() => onChange(stepReplay(state, 1, count))}>Bar ▶</button>
      <button type="button" className={btn} disabled={state.index >= count - 1} onClick={() => onChange({ ...state, playing: !state.playing })}>
        {state.playing ? "Pause" : "Play"}
      </button>
      <button type="button" className={btn} onClick={() => onChange(REPLAY_OFF)}>Exit replay</button>
      <span data-testid="replay-position">bar {state.index + 1} / {count}{atText ? ` · layers as of ${atText} IST` : ""}</span>
    </div>
  );
}

export default ReplayBar;
