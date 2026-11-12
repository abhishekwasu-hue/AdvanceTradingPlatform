import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { formatDateIST, formatDateTimeIST, formatNumber, formatPrice } from "../ui/format";
import type { ChartBar, TimeframeId } from "./timeframes";
import { onCandles } from "./watchScreen";

/**
 * M99 P6 (flag `pa_pack`): the "Price patterns" layer - the server's price pattern readings on this chart's bars
 * (GET /api/charts/layers/price-patterns: the same evaluation the "Price patterns" scanners and the M templates read), one
 * mark per reading with its short label, and under the chart the newest readings, each with its "Why?" in plain words.
 * Optional indicator evidence (all off): each adds a row to every Why? - it never adds or removes a mark. The nearest
 * swing areas below and above the close with their touches are information only.
 * A description of past bars: it places, proposes and changes nothing.
 */
export const PA_LAYER_ID = "price_patterns";
export const PA_LABEL = "Price patterns";
export const PA_SHOWN = 8;
export const EVIDENCE_LAYERS = ["bollinger", "adx", "macd", "zone_exit", "ma_stack", "osc_divergence"] as const;
export type EvidenceLayer = (typeof EVIDENCE_LAYERS)[number];
export const EVIDENCE_LABELS: Record<EvidenceLayer, string> = {
  bollinger: "Bollinger", adx: "ADX / DMI", macd: "MACD", zone_exit: "Stochastic / RSI", ma_stack: "Average stack", osc_divergence: "Second oscillator",
};

export interface PaEvidence { layer: string; title: string; text: string; agrees: boolean | null; agree_word: string }

export interface PaMark {
  time: string;
  origin_time: string;
  name: string;
  label: string;
  short: string;
  side: "up" | "down" | "";
  variant: string;
  text: string;
  evidence: PaEvidence[];
}

export interface PaLevel { kind: "support" | "resistance"; low: number; high: number; touches: number; bars_since: number;
  role_reversal: boolean; score: number; three_touches: boolean }

export interface PaReply {
  available: boolean;
  reason: string | null;
  base_tf: string;
  marks: PaMark[];
  levels: PaLevel[];
  note: string;
  settings_note: string;
  data_source?: string;
}

export interface PaOptions { evidence: EvidenceLayer[] }
export const DEFAULT_PA_OPTIONS: PaOptions = { evidence: [] };

/** The screener's timeframe for a chart timeframe (null: the layer does not read it). */
const SCREEN_TF: Partial<Record<TimeframeId, string>> = {
  "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", day: "1d", week: "1w",
};
export function paTimeframe(tf: TimeframeId): string | null {
  return SCREEN_TF[tf] ?? null;
}

export function isPaReply(x: unknown): x is PaReply {
  const r = x as Partial<PaReply> | null;
  return !!r && typeof r.available === "boolean" && Array.isArray(r.marks);
}

export const paApi = {
  layer: (q: { symbol: string; exchange: string; timeframe: string } & PaOptions) =>
    request<PaReply>(`/charts/layers/price-patterns?${new URLSearchParams({ symbol: q.symbol, exchange: q.exchange, timeframe: q.timeframe,
      evidence: q.evidence.join(",") }).toString()}`),
};

/** A reading's mark: up below the bar (up tone), down above it (down tone), a reading with no side above it (neutral). */
export function paMarker(m: Pick<PaMark, "side" | "short">, timestamp: string): ChartMarker {
  if (m.side === "up") return { timestamp, position: "belowBar", color: "up", shape: "arrowUp", text: m.short };
  if (m.side === "down") return { timestamp, position: "aboveBar", color: "down", shape: "arrowDown", text: m.short };
  return { timestamp, position: "aboveBar", color: "fg", shape: "circle", text: m.short };
}

export function paMarkers(reply: PaReply | null, candles: readonly Pick<ChartBar, "timestamp">[]): ChartMarker[] {
  if (!reply?.available || !reply.marks.length) return [];
  const at = onCandles(reply.marks.map((m) => m.time), candles, reply.base_tf);
  return reply.marks.map((m, i) => paMarker(m, at[i])).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

export interface PaState { reply: PaReply | null; loading: boolean; failed: boolean; markers: ChartMarker[]; unsupported: boolean }

export function usePaLayer(args: { symbol: string; exchange: string; timeframe: TimeframeId; candles: readonly ChartBar[]; on: boolean;
  options: PaOptions }, api: Pick<typeof paApi, "layer"> = paApi): PaState {
  const { symbol, exchange, timeframe, candles, on, options } = args;
  const [reply, setReply] = useState<PaReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const tf = paTimeframe(timeframe);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  const evidenceKey = options.evidence.join(",");
  useEffect(() => {
    if (!on || !symbol || !tf) { setReply(null); return undefined; }          // nothing is read while the layer is off
    let live = true;
    setLoading(true);
    api.layer({ symbol, exchange: exchange || "NSE", timeframe: tf, evidence: options.evidence }).then(
      (r) => { if (live) { setReply(isPaReply(r) ? r : null); setFailed(!isPaReply(r)); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, on, symbol, exchange, tf, evidenceKey, newest]); // eslint-disable-line react-hooks/exhaustive-deps
  const markers = useMemo(() => (on ? paMarkers(reply, candles) : []), [on, reply, candles]);
  return { reply, loading, failed, markers, unsupported: on && !tf };
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/** "12 Nov, 10:15" (intraday) or "12 Nov 2026" (daily and above), in India time - the shared IST formatters. */
export function paMarkTime(iso: string, baseTf: string): string {
  const daily = baseTf === "1d" || baseTf === "1w";
  return daily ? formatDateIST(iso) : formatDateTimeIST(iso, { year: false, suffix: false });
}

function tone(side: PaMark["side"]): string {
  return side === "up" ? "bg-up-tint text-up" : side === "down" ? "bg-down-tint text-down" : "bg-surface-2 text-fg-muted";
}

export function PaBar({ state, options, onChange }: { state: PaState; options: PaOptions; onChange: (o: PaOptions) => void }) {
  const { reply, loading, failed, unsupported } = state;
  const marks = reply?.available ? [...reply.marks].reverse().slice(0, PA_SHOWN) : [];
  const levels = reply?.available ? reply.levels ?? [] : [];
  let status: string | null = null;
  if (unsupported) status = "Price patterns read 1m to Weekly charts.";
  else if (loading && !reply) status = "Reading the bars…";
  else if (failed) status = "The price pattern readings could not be loaded just now.";
  else if (reply && !reply.available) status = reply.reason;
  else if (reply && !reply.marks.length) status = "No pattern in the bars shown.";
  const flip = (layer: EvidenceLayer) => onChange({ evidence: options.evidence.includes(layer)
    ? options.evidence.filter((x) => x !== layer) : [...options.evidence, layer] });
  return (
    <section aria-label={`${PA_LABEL} readings`} data-testid="pa-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{PA_LABEL}</h3>
        <details className="text-t12 text-fg">
          <summary className={cx("flex min-h-11 cursor-pointer select-none items-center md:min-h-8", focus)}>
            Evidence{options.evidence.length ? ` (${options.evidence.length})` : ""}
          </summary>
          <div className="flex flex-wrap gap-x-space-3">
            {EVIDENCE_LAYERS.map((layer) => (
              <label key={layer} className="flex min-h-11 items-center gap-space-1 md:min-h-8">
                <input type="checkbox" checked={options.evidence.includes(layer)} data-pa-evidence={layer}
                  onChange={() => flip(layer)} className={cx("h-4 w-4", focus)} />
                {EVIDENCE_LABELS[layer]}
              </label>
            ))}
          </div>
        </details>
      </div>
      {status && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{status}</p>}
      {marks.length > 0 && (
        <ul className="mt-space-1 divide-y divide-subtle" data-testid="pa-marks">
          {marks.map((m) => (
            <li key={`${m.time}-${m.name}-${m.side}`} data-pa-mark={m.name} data-pa-time={m.time} data-pa-side={m.side} className="py-space-1">
              <details>
                <summary className={cx("flex min-h-11 cursor-pointer select-none flex-wrap items-center gap-x-space-2 md:min-h-8", focus)}>
                  <span className={cx("inline-flex min-w-9 justify-center rounded-pill px-space-1 text-t12 font-semibold", tone(m.side))}>{m.short}</span>
                  <span className="font-medium text-fg">{m.label}</span>
                  {m.variant && <span className="text-t12 text-fg-muted">{m.variant}</span>}
                  <span className="text-t12 text-fg-muted tabular-nums">{paMarkTime(m.time, reply?.base_tf ?? "1d")}</span>
                  <span className="text-t12 text-brand">Why?</span>
                </summary>
                <p className="mt-space-1 text-t12 text-fg-muted">{m.text}</p>
                {m.evidence.length > 0 && (
                  <ul className="mt-space-1 space-y-space-1" data-testid="pa-evidence">
                    {m.evidence.map((x) => (
                      <li key={x.layer} className="text-t12">
                        <span className={cx("font-medium", x.agrees === true ? "text-up" : x.agrees === false ? "text-down" : "text-fg-muted")}>
                          {x.agrees === true ? "Agrees" : x.agrees === false ? "Does not agree" : "Neutral"}
                        </span>
                        <span className="text-fg-muted"> · {x.text}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
      {levels.length > 0 && (
        <details className="mt-space-1 text-t12" data-testid="pa-levels">
          <summary className={cx("cursor-pointer select-none text-fg", focus)}>Nearest swing areas</summary>
          <table className="mt-space-1 w-full text-left tabular-nums">
            <thead className="text-fg-muted">
              <tr><th className="font-medium">Area</th><th className="font-medium">Range</th><th className="font-medium">Touches</th>
                <th className="font-medium">Strength</th></tr>
            </thead>
            <tbody>
              {levels.map((lv) => (
                <tr key={`${lv.kind}-${lv.low}`}>
                  <td className={lv.kind === "support" ? "text-up" : "text-down"}>{lv.kind === "support" ? "Below" : "Above"}</td>
                  <td>{formatPrice(lv.low)} – {formatPrice(lv.high)}</td>
                  <td>{lv.touches}{lv.three_touches ? " ✓" : ""}</td>
                  <td>{formatNumber(lv.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <details className="mt-space-1 text-t12 text-fg-muted">
        <summary className={cx("cursor-pointer select-none", focus)}>Details</summary>
        <p className="mt-space-1">
          Each mark is a pattern of the swings or bars, read on the bar it became known: green arrows under a bar point up,
          red arrows over a bar point down - a description of past bars, not advice. Evidence rows (off unless chosen) only
          say whether an indicator agrees; they never add or remove a mark. Swing areas are information only (three touches
          or more are ticked). Closed bars only. {reply?.settings_note ? "Settings are reference values - edit before use (in the Price patterns screens)." : ""}
        </p>
      </details>
    </section>
  );
}
