import { useEffect, useMemo, useState } from "react";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { chartTimeframe } from "../screener/evidence";
import { templatesApi, type OverlayPart, type TemplateOverlay } from "../strategyTemplates/api";
import type { LevelLine } from "./chartCore";
import { timeframeLabel, type ChartBar, type TimeframeId } from "./timeframes";
import { markOf, onCandles } from "./watchScreen";

/**
 * M94 F16 (flag `intraday_pack`): a strategy template's conditions on a stock's chart - where its setup, its trigger and
 * "the idea is wrong" (the close beyond the invalidation) held, bar by bar, each read by the server through the ONE
 * shared screen evaluation (M91 C2), and the level where the newest signal's idea is wrong. Drawn only on demand (the
 * toolbar's "Template overlay"), on the template's own timeframe; the legend says what each mark means and whether the
 * part held on the last closed bar. It places, proposes and changes nothing.
 */
export interface TemplateOverlayState {
  reply: TemplateOverlay | null;
  loading: boolean;
  failed: boolean;
  sameTf: boolean;
  overlayTf: TimeframeId | null;
  markers: ChartMarker[];
  levels: LevelLine[];
}

const NO_MARKS: ChartMarker[] = [];
const NO_LEVELS: LevelLine[] = [];

/** The mark of a part on the chart: its shape, its side of the bar, its tone and its letter. */
export function partMark(p: Pick<OverlayPart, "part" | "side">): Omit<ChartMarker, "timestamp"> {
  const long = p.side === "long";
  if (p.part === "setup") return { shape: "circle", position: long ? "belowBar" : "aboveBar", color: "fg", text: "S" };
  if (p.part === "trigger") return { shape: long ? "arrowUp" : "arrowDown", position: long ? "belowBar" : "aboveBar", color: long ? "up" : "down", text: "T" };
  return { shape: "square", position: long ? "belowBar" : "aboveBar", color: long ? "down" : "up", text: "W" };
}

/** The legend's sign for a part (the chart's own mark in text). */
export function partSign(p: Pick<OverlayPart, "part" | "side">): string {
  if (p.part === "setup") return "● S";
  if (p.part === "trigger") return p.side === "long" ? "▲ T" : "▼ T";
  return "■ W";
}

/** The markers and the level line from a reply, on the chart's own candles (nothing while another timeframe shows). */
export function templateLayers(reply: TemplateOverlay, candles: readonly Pick<ChartBar, "timestamp">[], sameTf: boolean) {
  if (!reply.available || !sameTf) return { markers: NO_MARKS, levels: NO_LEVELS };
  const markers: ChartMarker[] = [];
  for (const p of reply.parts ?? []) {
    if (p.kept) continue;
    const mark = partMark(p);
    for (const ts of onCandles(p.hits, candles, reply.base_tf)) markers.push({ timestamp: ts, ...mark });
  }
  markers.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const newest = [...(reply.signals ?? [])].reverse().find((s) => s.invalidation !== null);
  const levels: LevelLine[] = newest && newest.invalidation !== null
    ? [{ price: newest.invalidation, title: "Wrong beyond", tone: "neutral" }] : NO_LEVELS;
  return { markers, levels };
}

export function useTemplateOverlay(args: { template: string | null; templateTf: string | null; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; on: boolean }, api: Pick<typeof templatesApi, "overlay"> = templatesApi): TemplateOverlayState {
  const { template, templateTf, symbol, exchange, timeframe, candles, on } = args;
  const [reply, setReply] = useState<TemplateOverlay | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (!on || !template || !symbol) { setReply(null); return undefined; }       // only on demand: nothing is read while off
    let live = true;
    setLoading(true);
    api.overlay(template, { symbol, exchange: exchange || "NSE", ...(templateTf ? { timeframe: templateTf } : {}) }).then(
      (r) => { if (live) { setReply(r); setFailed(false); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, on, template, templateTf, symbol, exchange, newest]);
  const overlayTf = reply ? chartTimeframe(reply.base_tf) : null;
  const sameTf = overlayTf === timeframe;
  const drawn = useMemo(() => (on && reply ? templateLayers(reply, candles, sameTf) : null), [on, reply, candles, sameTf]);
  return { reply, loading, failed, sameTf, overlayTf, markers: drawn?.markers ?? NO_MARKS, levels: drawn?.levels ?? NO_LEVELS };
}

const TONE = { up: "text-up", down: "text-down", muted: "text-fg-muted" } as const;

export function TemplateLegend({ state, onShowTf }: { state: TemplateOverlayState; onShowTf?: (tf: TimeframeId) => void }) {
  const { reply, loading, failed, sameTf, overlayTf } = state;
  if (!reply) {
    if (loading) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Reading the template…</p>;
    if (failed) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">The template could not be read just now.</p>;
    return null;
  }
  return (
    <section aria-label={`Template: ${reply.template.name}`} data-testid="template-legend" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
        <h3 className="min-w-0 text-t13 font-semibold text-fg">{reply.template.name}</h3>
        <span className="text-t12 text-fg-muted">{overlayTf ? timeframeLabel(overlayTf) : reply.base_tf} · last closed bar</span>
        {reply.available && !sameTf && overlayTf && onShowTf && (
          <button type="button" onClick={() => onShowTf(overlayTf)} data-testid="template-legend-tf"
            className="min-h-11 rounded-control px-space-1 text-t12 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
            Show the marks on {timeframeLabel(overlayTf)}
          </button>
        )}
      </div>
      {!reply.available && <p className="text-t12 text-fg-muted">{reply.reason || "No closed bars for this stock yet."}</p>}
      {reply.available && (
        <ul className="mt-space-1 grid gap-x-space-4 gap-y-px md:grid-cols-2" aria-label="Template conditions">
          {(reply.parts ?? []).map((p) => {
            const m = p.kept ? null : markOf(p.matched ? "met" : p.reason ? "unknown" : "failed");
            return (
              <li key={`${p.part}-${p.side}`} data-part={p.part} data-side={p.side} data-state={p.kept ? "kept" : p.matched ? "met" : "failed"}
                className="flex min-w-0 items-baseline gap-space-2">
                <span aria-hidden className="w-8 shrink-0 font-semibold text-fg-muted">{partSign(p)}</span>
                <span className="min-w-0 flex-1 break-words text-t12 text-fg">
                  {p.label}{p.kept ? ": kept from the setup bar (the line at each signal)" : ` · ${p.hits.length} ${p.hits.length === 1 ? "bar" : "bars"} in view`}
                </span>
                {m && <span aria-label={m.label} title={m.label} className={cx("w-4 shrink-0 text-center font-semibold", TONE[m.tone])}>{m.sign}</span>}
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-space-1 text-t12 text-fg-muted">{sameTf ? "✓ / ✗: met on the last closed bar. " : ""}{reply.footer}</p>
    </section>
  );
}
