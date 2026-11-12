import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import type { LayerData, LayerLabel, LayerSegment } from "../charting/engine";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { PA_COLORS } from "../charting/priceAction";
import { cx } from "../components/primitives/cx";
import type { ChartBar, TimeframeId } from "./timeframes";
import { markTime, vsaTimeframe } from "./vsaLayer";
import { onCandles } from "./watchScreen";

/**
 * M99 P4 (flag `wyckoff_pack`): the "Wyckoff" layer - the server's trading-range reading on this chart's bars
 * (GET /api/charts/layers/wyckoff: the same evaluation the "Wyckoff" scanners and the K templates read). Each range is a
 * box (its top and bottom from the climax to its last bar), each phase is labelled where it began, and each event is a
 * mark on the bar it was known, with its "Why?" (structure words) in the list under the chart.
 * Bars without volume data (an index whose future's volume is not there) show "No volume data" and draw nothing.
 * A description of past bars: it places, proposes and changes nothing.
 */
export const WYCKOFF_LAYER_ID = "wyckoff";
export const WYCKOFF_LABEL = "Wyckoff";
export const WYCKOFF_SHOWN = 8;

export interface WyckoffMark {
  time: string;
  origin_time: string;
  name: string;
  label: string;
  short: string;
  side: "strength" | "weakness";
  variant: string;
  phase: string;
  text: string;
}

export interface WyckoffRange {
  start_time: string;
  end_time: string;
  known_time: string;
  top: number;
  bottom: number;
  after: string;
  last_phase: string;
  lean: string;
  open: boolean;
}

export interface WyckoffPhase { time: string; phase: string; words: string; lean: string }

export interface WyckoffReply {
  available: boolean;
  reason: string | null;
  base_tf: string;
  marks: WyckoffMark[];
  ranges: WyckoffRange[];
  phases: WyckoffPhase[];
  bias?: string;
  note: string;
  settings_note: string;
  data_source?: string;
}

export function isWyckoffReply(x: unknown): x is WyckoffReply {
  const r = x as Partial<WyckoffReply> | null;
  return !!r && typeof r.available === "boolean" && Array.isArray(r.marks) && Array.isArray(r.ranges) && Array.isArray(r.phases);
}

export const wyckoffApi = {
  layer: (q: { symbol: string; exchange: string; timeframe: string }) =>
    request<WyckoffReply>(`/charts/layers/wyckoff?${new URLSearchParams({ symbol: q.symbol, exchange: q.exchange, timeframe: q.timeframe }).toString()}`),
};

/** An event's mark: strength below the bar (up tone), weakness above it (down tone), its short label as the text. */
export function wyckoffMarker(m: Pick<WyckoffMark, "side" | "short">, timestamp: string): ChartMarker {
  const strong = m.side === "strength";
  return { timestamp, position: strong ? "belowBar" : "aboveBar", color: strong ? "up" : "down", shape: "square", text: m.short };
}

export function wyckoffMarkers(reply: WyckoffReply | null, candles: readonly Pick<ChartBar, "timestamp">[]): ChartMarker[] {
  if (!reply?.available || !reply.marks.length) return [];
  const at = onCandles(reply.marks.map((m) => m.time), candles, reply.base_tf);
  return reply.marks.map((m, i) => wyckoffMarker(m, at[i])).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

const seconds = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** The boxes and phase labels as engine layer data (times snapped to the chart's bars); null when there is nothing. */
export function wyckoffLayerData(reply: WyckoffReply | null, candles: readonly Pick<ChartBar, "timestamp">[]): LayerData | null {
  if (!reply?.available || (!reply.ranges.length && !reply.phases.length)) return null;
  const segments: LayerSegment[] = [];
  const labels: LayerLabel[] = [];
  for (const r of reply.ranges) {
    const [a, b] = onCandles([r.start_time, r.end_time], candles, reply.base_tf).map(seconds);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    const style = r.open ? "dashed" : "solid";
    segments.push({ t1: a, p1: r.top, t2: b, p2: r.top, color: PA_COLORS.resistance, style });
    segments.push({ t1: a, p1: r.bottom, t2: b, p2: r.bottom, color: PA_COLORS.support, style });
    segments.push({ t1: a, p1: r.bottom, t2: a, p2: r.top, color: PA_COLORS.structure, style: "dotted" });
    segments.push({ t1: b, p1: r.bottom, t2: b, p2: r.top, color: PA_COLORS.structure, style: "dotted" });
    for (const p of reply.phases) {
      const t = seconds(onCandles([p.time], candles, reply.base_tf)[0]);
      if (t >= a && t <= b) labels.push({ time: t, price: r.top, text: p.phase, color: PA_COLORS.structure, position: "above" });
    }
  }
  return segments.length || labels.length ? { segments, labels } : null;
}

export interface WyckoffState { reply: WyckoffReply | null; loading: boolean; failed: boolean; markers: ChartMarker[]; unsupported: boolean }

export function useWyckoffLayer(args: { engine: LightweightEngine | null; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; on: boolean }, api: Pick<typeof wyckoffApi, "layer"> = wyckoffApi): WyckoffState {
  const { engine, symbol, exchange, timeframe, candles, on } = args;
  const [reply, setReply] = useState<WyckoffReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const tf = vsaTimeframe(timeframe);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (!on || !symbol || !tf) { setReply(null); return undefined; }          // nothing is read while the layer is off
    let live = true;
    setLoading(true);
    api.layer({ symbol, exchange: exchange || "NSE", timeframe: tf }).then(
      (r) => { if (live) { setReply(isWyckoffReply(r) ? r : null); setFailed(!isWyckoffReply(r)); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, on, symbol, exchange, tf, newest]);
  const markers = useMemo(() => (on ? wyckoffMarkers(reply, candles) : []), [on, reply, candles]);
  const data = useMemo(() => (on ? wyckoffLayerData(reply, candles) : null), [on, reply, candles]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(WYCKOFF_LAYER_ID, data);
    return () => engine.setLayer(WYCKOFF_LAYER_ID, null);
  }, [engine, data]);
  return { reply, loading, failed, markers, unsupported: on && !tf };
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const LEAN_WORD: Record<string, string> = { strength: "leans up", weakness: "leans down" };

export function WyckoffBar({ state }: { state: WyckoffState }) {
  const { reply, loading, failed, unsupported } = state;
  const marks = reply?.available ? [...reply.marks].reverse().slice(0, WYCKOFF_SHOWN) : [];
  const current = reply?.available ? reply.ranges.find((r) => r.open) : undefined;
  const phaseNow = current ? reply?.phases.filter((p) => Date.parse(p.time) >= Date.parse(current.start_time)).slice(-1)[0] : undefined;
  let status: string | null = null;
  if (unsupported) status = "Wyckoff reads 1m to Weekly charts.";
  else if (loading && !reply) status = "Reading the bars…";
  else if (failed) status = "The Wyckoff reading could not be loaded just now.";
  else if (reply && !reply.available) status = reply.reason === "no volume data" ? "No volume data for these bars - nothing is read." : reply.reason;
  else if (reply && !reply.marks.length && !reply.ranges.length) status = "No range in the bars shown.";
  return (
    <section aria-label={`${WYCKOFF_LABEL} reading`} data-testid="wyckoff-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{WYCKOFF_LABEL}</h3>
        {phaseNow && (
          <span data-wyckoff-phase={phaseNow.phase} className={cx("rounded-pill px-space-2 text-t12 font-semibold",
            current?.lean === "strength" ? "bg-up-tint text-up" : current?.lean === "weakness" ? "bg-down-tint text-down" : "bg-surface-2 text-fg")}>
            {phaseNow.words}{current?.lean ? ` · ${LEAN_WORD[current.lean] ?? ""}` : ""}
          </span>
        )}
      </div>
      {status && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{status}</p>}
      {marks.length > 0 && (
        <ul className="mt-space-1 divide-y divide-subtle" data-testid="wyckoff-marks">
          {marks.map((m) => (
            <li key={`${m.time}-${m.name}`} data-wyckoff-mark={m.name} data-wyckoff-time={m.time} className="py-space-1">
              <details>
                <summary className={cx("flex min-h-11 cursor-pointer select-none flex-wrap items-center gap-x-space-2 md:min-h-8", focus)}>
                  <span className={cx("inline-flex min-w-9 justify-center rounded-pill px-space-1 text-t12 font-semibold",
                    m.side === "strength" ? "bg-up-tint text-up" : "bg-down-tint text-down")}>{m.short}</span>
                  <span className="font-medium text-fg">{m.label}</span>
                  <span className="text-t12 text-fg-muted tabular-nums">{markTime(m.time, reply?.base_tf ?? "1h")}</span>
                  <span className="text-t12 text-brand">Why?</span>
                </summary>
                <p className="mt-space-1 text-t12 text-fg-muted">{m.text}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
      <details className="mt-space-1 text-t12 text-fg-muted">
        <summary className={cx("cursor-pointer select-none", focus)}>Details</summary>
        <p className="mt-space-1">
          A range starts with a climax at the end of a move, is set by the swing back and the secondary test, and is read in
          phases A to E. The box shows its top and bottom (dashed while it is still open); letters mark where each phase began.
          Green marks under a bar describe strength, red marks over a bar weakness, each on the bar it became known - a
          description of past bars, not advice. An index uses its near-month future&apos;s volume. Closed bars only.
          {reply?.settings_note ? " Settings are reference values - edit before use (in the Wyckoff screens)." : ""}
        </p>
      </details>
    </section>
  );
}
