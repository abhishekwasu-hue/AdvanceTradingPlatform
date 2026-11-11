import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import type { LayerBand, LayerData, LayerLabel, LayerSegment } from "../charting/engine";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { PA_COLORS } from "../charting/priceAction";
import { cx } from "../components/primitives/cx";
import type { ChartBar, TimeframeId } from "./timeframes";
import { onCandles } from "./watchScreen";

/**
 * M99 P5 (flag `fib_ew_pack`): the "Fibonacci + Elliott" layer - the server's reading of this chart's bars
 * (GET /api/charts/layers/fib-ew: the same evaluation the "Fibonacci + Elliott" scanners and the L templates read).
 * Drawn: the Fibonacci tool on the latest confirmed swings (retracements, extensions, golden-section projections), the
 * price clusters ahead of the open swing, the current wave count with its invalidation lines, a triangle's thrust
 * projection (dashed, never a target), and each reading as a mark with its "Why?" in the list under the chart.
 * Every count is an interpretation. A description of past bars: it places, proposes and changes nothing.
 */
export const FIB_EW_LAYER_ID = "fib_ew";
export const FIB_EW_LABEL = "Fibonacci + Elliott";
export const FIB_EW_SHOWN = 8;
export const COUNT_NOTE = "Count is an interpretation";

export interface FibEwMark { time: string; origin_time: string; name: string; side: "up" | "down"; short: string; label: string; text: string }
export interface FibLevelRow { kind: string; ratio: number; price: number; label: string; from_time: string; anchor_time: string }
export interface FibCluster { low: number; high: number; count: number; labels: string[]; sources: string[]; side: "up" | "down"; from_time: string }
export interface FibCheck { rule: string; words: string; status: "pass" | "open" | "fail"; kind: string }
export interface FibLine { price: number; words: string; kind: string }
export interface FibCount {
  type: string; type_words: string; state: string; clarity: string; direction: number; labels: string[];
  points: { time: string; price: number }[]; kind: string; checks: FibCheck[]; lines: FibLine[];
  thrust: { price: number; from_time: string; words: string } | null; note: string;
}
export interface FibEwReply {
  available: boolean; reason: string | null; base_tf: string; marks: FibEwMark[]; fib: FibLevelRow[]; clusters: FibCluster[];
  count: FibCount | null; note: string; settings_note: string; as_of?: string | null; data_source?: string;
}
export interface FibEwShow { fib: boolean; clusters: boolean; count: boolean }
export const DEFAULT_FIB_EW_SHOW: FibEwShow = { fib: true, clusters: true, count: true };

export function isFibEwReply(x: unknown): x is FibEwReply {
  const r = x as Partial<FibEwReply> | null;
  return !!r && typeof r.available === "boolean" && Array.isArray(r.marks) && Array.isArray(r.fib) && Array.isArray(r.clusters);
}

/** The server timeframe of a chart timeframe (the layer reads 1m to weekly; null for the others). */
export function fibEwTimeframe(tf: TimeframeId): string | null {
  const map: Partial<Record<TimeframeId, string>> = { "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", day: "1d", week: "1w" };
  return map[tf] ?? null;
}

export const fibEwApi = {
  layer: (q: { symbol: string; exchange: string; timeframe: string }) =>
    request<FibEwReply>(`/charts/layers/fib-ew?${new URLSearchParams({ symbol: q.symbol, exchange: q.exchange, timeframe: q.timeframe }).toString()}`),
};

/** A reading's mark: up readings under the bar (up tone), down readings over it (down tone); a void count is a circle. */
export function fibEwMarker(m: Pick<FibEwMark, "side" | "short" | "name">, timestamp: string): ChartMarker {
  const up = m.side === "up";
  return { timestamp, position: up ? "belowBar" : "aboveBar", color: up ? "up" : "down", shape: m.name === "count_void" ? "circle" : "square", text: m.short };
}

export function fibEwMarkers(reply: FibEwReply | null, candles: readonly Pick<ChartBar, "timestamp">[]): ChartMarker[] {
  if (!reply?.available || !reply.marks.length) return [];
  const at = onCandles(reply.marks.map((m) => m.time), candles, reply.base_tf);
  return reply.marks.map((m, i) => fibEwMarker(m, at[i])).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

const seconds = (iso: string) => Math.floor(Date.parse(iso) / 1000);
const KIND_COLOR: Record<string, string> = { retrace: PA_COLORS.structure, extend: PA_COLORS.trendline, golden_projection: PA_COLORS.high };
const KIND_SHORT: Record<string, string> = { retrace: "R", extend: "E", golden_projection: "G" };

/** The tool's levels, the clusters, the count and its lines as engine layer data (times snapped to the chart's bars). */
export function fibEwLayerData(reply: FibEwReply | null, candles: readonly Pick<ChartBar, "timestamp">[], show: FibEwShow = DEFAULT_FIB_EW_SHOW): LayerData | null {
  if (!reply?.available || !candles.length) return null;
  const snap = (iso: string) => seconds(onCandles([iso], candles, reply.base_tf)[0]);
  const last = seconds(candles[candles.length - 1].timestamp);
  const segments: LayerSegment[] = [];
  const labels: LayerLabel[] = [];
  const bands: LayerBand[] = [];
  if (show.fib) {
    for (const f of reply.fib) {
      const t1 = snap(f.from_time);
      if (!Number.isFinite(t1)) continue;
      segments.push({ t1, p1: f.price, t2: last, p2: f.price, color: KIND_COLOR[f.kind] ?? PA_COLORS.flat, style: "dotted",
        label: `${KIND_SHORT[f.kind] ?? ""} ${f.ratio}` });
    }
  }
  if (show.clusters) {
    for (const c of reply.clusters) {
      bands.push({ low: c.low, high: c.high, role: c.side === "up" ? "demand" : "supply", opacity: 0.12, style: "dashed", label: `Fib cluster (${c.count})` });
    }
  }
  const count = reply.count;
  if (show.count && count && count.points.length > 1) {
    const pts = count.points.map((p) => ({ t: snap(p.time), p: p.price }));
    pts.forEach((p, i) => {
      if (i > 0 && Number.isFinite(p.t) && Number.isFinite(pts[i - 1].t)) {
        segments.push({ t1: pts[i - 1].t, p1: pts[i - 1].p, t2: p.t, p2: p.p, color: PA_COLORS.structure, width: 2,
          style: i === pts.length - 1 ? "dashed" : "solid" });
      }
      if (i > 0 && Number.isFinite(p.t)) {
        const rising = p.p >= pts[i - 1].p;
        labels.push({ time: p.t, price: p.p, text: count.labels[i] ?? "", color: PA_COLORS.structure, position: rising ? "above" : "below" });
      }
    });
    const from = pts[pts.length - 2]?.t;
    for (const l of count.lines) {
      if (Number.isFinite(from)) segments.push({ t1: from, p1: l.price, t2: last, p2: l.price, color: PA_COLORS.resistance, style: "dashed",
        label: l.kind === "count" ? "Count void" : "Rule line" });
    }
    if (count.thrust) {
      const t1 = snap(count.thrust.from_time);
      if (Number.isFinite(t1)) segments.push({ t1, p1: count.thrust.price, t2: last, p2: count.thrust.price, color: PA_COLORS.flat, style: "dashed",
        label: "Thrust projection (not a target)" });
    }
  }
  return segments.length || labels.length || bands.length ? { segments, labels, bands } : null;
}

export interface FibEwState { reply: FibEwReply | null; loading: boolean; failed: boolean; markers: ChartMarker[]; unsupported: boolean }

export function useFibEwLayer(args: { engine: LightweightEngine | null; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; on: boolean; show: FibEwShow }, api: Pick<typeof fibEwApi, "layer"> = fibEwApi): FibEwState {
  const { engine, symbol, exchange, timeframe, candles, on, show } = args;
  const [reply, setReply] = useState<FibEwReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const tf = fibEwTimeframe(timeframe);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (!on || !symbol || !tf) { setReply(null); return undefined; }          // nothing is read while the layer is off
    let live = true;
    setLoading(true);
    api.layer({ symbol, exchange: exchange || "NSE", timeframe: tf }).then(
      (r) => { if (live) { setReply(isFibEwReply(r) ? r : null); setFailed(!isFibEwReply(r)); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, on, symbol, exchange, tf, newest]);
  const markers = useMemo(() => (on ? fibEwMarkers(reply, candles) : []), [on, reply, candles]);
  const data = useMemo(() => (on ? fibEwLayerData(reply, candles, show) : null), [on, reply, candles, show]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(FIB_EW_LAYER_ID, data);
    return () => engine.setLayer(FIB_EW_LAYER_ID, null);
  }, [engine, data]);
  return { reply, loading, failed, markers, unsupported: on && !tf };
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const STATUS: Record<FibCheck["status"], { sign: string; tone: string; word: string }> = {
  pass: { sign: "✓", tone: "text-up", word: "holds" },
  open: { sign: "…", tone: "text-fg-muted", word: "open" },
  fail: { sign: "✗", tone: "text-down", word: "broken" },
};
const WAVE_WORD: Record<string, string> = { IMP_1: "wave 1", IMP_2: "wave 2", IMP_3: "wave 3", IMP_4: "wave 4", IMP_5: "wave 5",
  COR_A: "wave A", COR_B: "wave B", COR_C: "wave C", COR_D: "wave D", COR_E: "wave E" };

function markTime(iso: string, tf: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
  if (tf === "1d" || tf === "1w") return day;
  return `${day} ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })}`;
}

export function FibEwBar({ state, show, onShow }: { state: FibEwState; show: FibEwShow; onShow: (next: FibEwShow) => void }) {
  const { reply, loading, failed, unsupported } = state;
  const marks = reply?.available ? [...reply.marks].reverse().slice(0, FIB_EW_SHOWN) : [];
  const count = reply?.available ? reply.count : null;
  let status: string | null = null;
  if (unsupported) status = "Fibonacci + Elliott reads 1m to Weekly charts.";
  else if (loading && !reply) status = "Reading the bars…";
  else if (failed) status = "The Fibonacci + Elliott reading could not be loaded just now.";
  else if (reply && !reply.available) status = reply.reason;
  else if (reply && !count && !reply.fib.length) status = "Not enough confirmed swings in these bars yet.";
  const toggles: { key: keyof FibEwShow; label: string }[] = [{ key: "fib", label: "Fib levels" }, { key: "clusters", label: "Clusters" }, { key: "count", label: "Count" }];
  return (
    <section aria-label={`${FIB_EW_LABEL} reading`} data-testid="fib-ew-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{FIB_EW_LABEL}</h3>
        {count && (
          <span data-fib-ew-count={count.type} className={cx("rounded-pill px-space-2 text-t12 font-semibold",
            count.direction > 0 ? "bg-up-tint text-up" : "bg-down-tint text-down")}>
            {count.type_words} · {WAVE_WORD[count.state] ?? count.state} running
          </span>
        )}
        <span className="text-t12 text-fg-muted">{COUNT_NOTE}</span>
        <div className="ml-auto flex flex-wrap gap-space-1" role="group" aria-label="What to draw">
          {toggles.map((t) => (
            <button key={t.key} type="button" aria-pressed={show[t.key]} onClick={() => onShow({ ...show, [t.key]: !show[t.key] })}
              className={cx("min-h-11 rounded-pill border px-space-2 text-t12 md:min-h-8", focus,
                show[t.key] ? "border-brand bg-surface-2 text-fg" : "border-subtle text-fg-muted")}>{t.label}</button>
          ))}
        </div>
      </div>
      {status && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{status}</p>}
      {count && count.checks.length > 0 && (
        <ul className="mt-space-1 grid gap-x-space-3 gap-y-space-1 sm:grid-cols-2" data-testid="fib-ew-checks" aria-label="The count's rules">
          {count.checks.map((c) => (
            <li key={c.rule} data-fib-ew-check={c.rule} data-status={c.status} className="flex items-start gap-space-1 text-t12">
              <span aria-hidden className={cx("w-4 shrink-0 text-center font-semibold", STATUS[c.status].tone)}>{STATUS[c.status].sign}</span>
              <span className="text-fg">{c.words}</span>
              <span className={cx("ml-auto shrink-0", STATUS[c.status].tone)}>{STATUS[c.status].word}</span>
            </li>
          ))}
        </ul>
      )}
      {count && count.thrust && <p className="mt-space-1 text-t12 text-fg-muted">{count.thrust.words}.</p>}
      {marks.length > 0 && (
        <ul className="mt-space-1 divide-y divide-subtle" data-testid="fib-ew-marks">
          {marks.map((m) => (
            <li key={`${m.time}-${m.name}-${m.side}`} data-fib-ew-mark={m.name} data-fib-ew-time={m.time} className="py-space-1">
              <details>
                <summary className={cx("flex min-h-11 cursor-pointer select-none flex-wrap items-center gap-x-space-2 md:min-h-8", focus)}>
                  <span className={cx("inline-flex min-w-9 justify-center rounded-pill px-space-1 text-t12 font-semibold",
                    m.side === "up" ? "bg-up-tint text-up" : "bg-down-tint text-down")}>{m.short}</span>
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
          Dotted lines are Fibonacci levels of the latest confirmed swings: R retracements of the last swing, E the swing
          before laid from the latest turn, G golden-section projections. A shaded band is a cluster - several levels from
          different swings close together. The purple path is the current wave count (the last leg dashed while it runs);
          red dashed lines are where the count would be wrong. A thrust projection after a triangle is a minimum estimate,
          never a target. {COUNT_NOTE} - a description of past bars, not advice. Closed bars only.
          {reply?.settings_note ? " Settings are reference values - edit before use (in the Fibonacci + Elliott screens)." : ""}
        </p>
      </details>
    </section>
  );
}
