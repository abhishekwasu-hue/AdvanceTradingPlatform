import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import type { ChartBar, TimeframeId } from "./timeframes";
import { onCandles } from "./watchScreen";

/**
 * M99 P3 (flag `vsa_pack`): the "Volume spread" layer - the server's volume spread readings on this chart's bars
 * (GET /api/charts/layers/vsa: the same evaluation the "Volume spread" scanners and the J templates read), one mark per
 * reading with its short label, and under the chart the newest readings, each with its "Why?" (spread, volume, close
 * position, the structure before) in plain words. Two switches: Background (on) and Next-bar confirmation (off).
 * Bars without volume data (an index whose future's volume is not there) show "No volume data" and no marks.
 * A description of past bars: it places, proposes and changes nothing.
 */
export const VSA_LAYER_ID = "vsa";
export const VSA_LABEL = "Volume spread";
export const VSA_SHOWN = 8;

export interface VsaMark {
  time: string;
  origin_time: string;
  name: string;
  label: string;
  short: string;
  side: "strength" | "weakness";
  variant: string;
  text: string;
}

export interface VsaReply {
  available: boolean;
  reason: string | null;
  base_tf: string;
  marks: VsaMark[];
  note: string;
  settings_note: string;
  data_source?: string;
}

export interface VsaOptions { background: boolean; confirm: boolean }
export const DEFAULT_VSA_OPTIONS: VsaOptions = { background: true, confirm: false };

/** The screener's timeframe for a chart timeframe (null: the layer does not read it). */
const SCREEN_TF: Partial<Record<TimeframeId, string>> = {
  "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", day: "1d", week: "1w",
};
export function vsaTimeframe(tf: TimeframeId): string | null {
  return SCREEN_TF[tf] ?? null;
}

export function isVsaReply(x: unknown): x is VsaReply {
  const r = x as Partial<VsaReply> | null;
  return !!r && typeof r.available === "boolean" && Array.isArray(r.marks);
}

export const vsaApi = {
  layer: (q: { symbol: string; exchange: string; timeframe: string } & VsaOptions) =>
    request<VsaReply>(`/charts/layers/vsa?${new URLSearchParams({ symbol: q.symbol, exchange: q.exchange, timeframe: q.timeframe,
      background: String(q.background), confirm: String(q.confirm) }).toString()}`),
};

/** A reading's mark: strength below the bar (up tone), weakness above it (down tone), its short label as the text. */
export function vsaMarker(m: Pick<VsaMark, "side" | "short">, timestamp: string): ChartMarker {
  const strong = m.side === "strength";
  return { timestamp, position: strong ? "belowBar" : "aboveBar", color: strong ? "up" : "down", shape: "circle", text: m.short };
}

export function vsaMarkers(reply: VsaReply | null, candles: readonly Pick<ChartBar, "timestamp">[]): ChartMarker[] {
  if (!reply?.available || !reply.marks.length) return [];
  const at = onCandles(reply.marks.map((m) => m.time), candles, reply.base_tf);
  return reply.marks.map((m, i) => vsaMarker(m, at[i])).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

export interface VsaState { reply: VsaReply | null; loading: boolean; failed: boolean; markers: ChartMarker[]; unsupported: boolean }

export function useVsaLayer(args: { symbol: string; exchange: string; timeframe: TimeframeId; candles: readonly ChartBar[]; on: boolean;
  options: VsaOptions }, api: Pick<typeof vsaApi, "layer"> = vsaApi): VsaState {
  const { symbol, exchange, timeframe, candles, on, options } = args;
  const [reply, setReply] = useState<VsaReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const tf = vsaTimeframe(timeframe);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (!on || !symbol || !tf) { setReply(null); return undefined; }          // nothing is read while the layer is off
    let live = true;
    setLoading(true);
    api.layer({ symbol, exchange: exchange || "NSE", timeframe: tf, ...options }).then(
      (r) => { if (live) { setReply(isVsaReply(r) ? r : null); setFailed(!isVsaReply(r)); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, on, symbol, exchange, tf, options.background, options.confirm, newest]); // eslint-disable-line react-hooks/exhaustive-deps
  const markers = useMemo(() => (on ? vsaMarkers(reply, candles) : []), [on, reply, candles]);
  return { reply, loading, failed, markers, unsupported: on && !tf };
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/** "12 Nov 10:15" (intraday) or "12 Nov 2026" (daily and above), in India time. */
export function markTime(iso: string, baseTf: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const daily = baseTf === "1d" || baseTf === "1w";
  return d.toLocaleString("en-IN", daily ? { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" }
    : { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

export function VsaBar({ state, options, onChange }: { state: VsaState; options: VsaOptions; onChange: (o: VsaOptions) => void }) {
  const { reply, loading, failed, unsupported } = state;
  const marks = reply?.available ? [...reply.marks].reverse().slice(0, VSA_SHOWN) : [];
  let status: string | null = null;
  if (unsupported) status = "Volume spread reads 1m to Weekly charts.";
  else if (loading && !reply) status = "Reading the bars…";
  else if (failed) status = "The volume spread readings could not be loaded just now.";
  else if (reply && !reply.available) status = reply.reason === "no volume data" ? "No volume data for these bars - nothing is read." : reply.reason;
  else if (reply && !reply.marks.length) status = "No reading in the bars shown.";
  return (
    <section aria-label={`${VSA_LABEL} readings`} data-testid="vsa-bar" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1">
        <h3 className="text-t13 font-semibold text-fg">{VSA_LABEL}</h3>
        <label className="flex min-h-11 items-center gap-space-1 text-t12 text-fg md:min-h-8">
          <input type="checkbox" checked={options.background} data-vsa-background
            onChange={(e) => onChange({ ...options, background: e.target.checked })} className={cx("h-4 w-4", focus)} />
          Background
        </label>
        <label className="flex min-h-11 items-center gap-space-1 text-t12 text-fg md:min-h-8">
          <input type="checkbox" checked={options.confirm} data-vsa-confirm
            onChange={(e) => onChange({ ...options, confirm: e.target.checked })} className={cx("h-4 w-4", focus)} />
          Next-bar confirmation
        </label>
      </div>
      {status && <p role="status" className="mt-space-1 text-t12 text-fg-muted">{status}</p>}
      {marks.length > 0 && (
        <ul className="mt-space-1 divide-y divide-subtle" data-testid="vsa-marks">
          {marks.map((m) => (
            <li key={`${m.time}-${m.name}`} data-vsa-mark={m.name} data-vsa-time={m.time} className="py-space-1">
              <details>
                <summary className={cx("flex min-h-11 cursor-pointer select-none flex-wrap items-center gap-x-space-2 md:min-h-8", focus)}>
                  <span className={cx("inline-flex min-w-9 justify-center rounded-pill px-space-1 text-t12 font-semibold",
                    m.side === "strength" ? "bg-up-tint text-up" : "bg-down-tint text-down")}>{m.short}</span>
                  <span className="font-medium text-fg">{m.label}</span>
                  <span className="text-t12 text-fg-muted tabular-nums">{markTime(m.time, reply?.base_tf ?? "1d")}</span>
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
          Each bar&apos;s spread and volume are read against the bars before it, with where it closed and the structure before it.
          Green marks under a bar describe strength, red marks over a bar weakness - a description of past bars, not advice.
          Background: count a reading only with the structure it needs. Next-bar confirmation: show it on the next bar, only
          when that bar closes its way. An index uses its near-month future&apos;s volume. Closed bars only. {reply?.settings_note
            ? `Settings are reference values - edit before use (in the Volume spread screens).` : ""}
        </p>
      </details>
    </section>
  );
}
