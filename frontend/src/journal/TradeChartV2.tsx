import { ImageDown, LineChart } from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import type { ChartMarker, PriceLineSpec } from "../charting/markers";
import { useToast } from "../components/Toast";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ErrorState } from "../ui/ErrorState";
import { formatPrice } from "../ui/format";
import type { JournalApi } from "./api";
import { saveBlob } from "./journalModel";
import type { TradeChart } from "./types";

/**
 * M88: "Show on chart" - an outside-ATP trade on its own chart, in place: the candles around it with his entry and exit
 * marked at their own times and prices (and his own stop, only when he gave one). Daily candles from the end-of-day
 * files, labelled as such. "Save image" downloads the same chart drawn on the server. Read-only: nothing here can place
 * or change an order.
 */

const ProChart = lazy(() => import("../components/ProChart"));
export type TradeChartApi = Pick<JournalApi, "tradeChart" | "image">;

/** The chart's marks and lines from the server's answer: a mark sits on the candle of its fill. */
export function chartMarks(c: TradeChart): { markers: ChartMarker[]; lines: PriceLineSpec[] } {
  const markers: ChartMarker[] = c.marks.filter((m) => m.bar).map((m) => ({
    timestamp: m.bar as string, position: m.side === "BUY" ? "belowBar" : "aboveBar", color: m.side === "BUY" ? "up" : "down",
    shape: m.side === "BUY" ? "arrowUp" : "arrowDown",
    text: m.on_chart === false ? `${m.label} (premium ${formatPrice(m.price)})` : `${m.label} ${formatPrice(m.price)}`,
  }));
  const lines: PriceLineSpec[] = [
    // a premium is never drawn as a line on the underlying's price scale
    ...c.marks.filter((m) => m.on_chart !== false).map((m) => ({ price: m.price, color: m.side === "BUY" ? "up" : "down", title: m.label })),
    ...c.levels.map((l) => ({ price: l.price, color: "fg", title: l.label })),
  ];
  return { markers, lines };
}

type State = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; chart: TradeChart };

/** Owner 19 Nov: 15 minutes by default, his choice of timeframe (the server answers each from his own broker's
 * history, Daily from the end-of-day files). */
export const TRADE_CHART_TFS: { key: string; label: string; chart: string }[] = [
  { key: "5m", label: "5m", chart: "5min" }, { key: "15m", label: "15m", chart: "15min" },
  { key: "1h", label: "1h", chart: "60min" }, { key: "1d", label: "Daily", chart: "day" },
];
export const DEFAULT_TRADE_TF = "15m";

/** The chart path with the chosen timeframe (the server's own `?tf=`). Pure. */
export function withTf(path: string, tf: string): string {
  const [base, query = ""] = path.split("?");
  const q = new URLSearchParams(query);
  q.set("tf", tf);
  return `${base}?${q.toString()}`;
}

export function TradeChartV2({ api, path, imagePath, fileName }: { api: TradeChartApi; path: string; imagePath?: string; fileName: string }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>({ status: "loading" });
  const [round, setRound] = useState(0);
  const [saving, setSaving] = useState(false);
  const [tf, setTf] = useState(DEFAULT_TRADE_TF);
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    setState({ status: "loading" });
    api.tradeChart(withTf(path, tf)).then((chart) => { if (alive) setState({ status: "ok", chart }); })
      .catch((error) => { if (alive) setState({ status: "error", error }); });
    return () => { alive = false; };
  }, [api, path, open, round, tf]);

  async function saveImage() {
    if (!imagePath) return;
    setSaving(true);
    try { saveBlob(await api.image(withTf(imagePath, tf)), fileName); } catch (e) { toast.error(errorSentence(e)); } finally { setSaving(false); }
  }

  if (!open) {
    return (
      <button type="button" className={BUTTON_SECONDARY} onClick={() => setOpen(true)} data-show-on-chart>
        <LineChart size={16} aria-hidden />Show on chart
      </button>
    );
  }
  const c = state.status === "ok" ? state.chart : null;
  const drawn = c && !c.missing ? chartMarks(c) : null;
  return (
    <div className="space-y-space-2" data-trade-chart>
      <div className="flex flex-wrap items-center gap-space-2">
        <span role="group" aria-label="Timeframe" className="inline-flex flex-wrap gap-space-1" data-trade-chart-tfs>
          {TRADE_CHART_TFS.map((t) => (
            <button key={t.key} type="button" aria-pressed={tf === t.key} onClick={() => setTf(t.key)} data-trade-chart-tf-pick={t.key}
              className={tf === t.key ? chipClass({ tone: "info" }) : `${chipClass({ tone: "neutral" })} opacity-80`}>{t.label}</button>
          ))}
        </span>
        {c && <span className="text-t13 text-fg-muted" data-trade-chart-tf>{c.timeframe_label}</span>}
        <span className="ml-auto flex flex-wrap gap-space-2">
          {c && !c.missing && imagePath && (
            <button type="button" className={BUTTON_SECONDARY} onClick={() => void saveImage()} disabled={saving} data-trade-chart-save>
              <ImageDown size={16} aria-hidden />{saving ? "Saving…" : "Save image"}
            </button>
          )}
          <button type="button" className={BUTTON_SECONDARY} onClick={() => setOpen(false)} data-trade-chart-hide>Hide chart</button>
        </span>
      </div>
      {state.status === "loading" && <LoadingRowsV2 rows={3} />}
      {state.status === "error" && <ErrorState message={state.error} onRetry={() => setRound((r) => r + 1)} variant="inline" />}
      {c && c.missing && <p className="text-t14 text-fg-muted" data-trade-chart-missing>{c.missing_text}</p>}
      {c && drawn && (
        <>
          <div className="min-w-0 overflow-hidden rounded-panel border border-subtle" data-trade-chart-candles={c.candles.length}>
            <Suspense fallback={<LoadingRowsV2 rows={3} />}>
              <ProChart candles={c.candles} symbol={c.symbol} timeframe={TRADE_CHART_TFS.find((t) => t.key === (c.tf ?? "1d"))?.chart ?? "day"} markers={drawn.markers} priceLines={drawn.lines}
                height={300} compact drawingTools={false} defaultIndicators={[]} exchange={c.exchange} />
            </Suspense>
          </div>
          <p className="text-t13 text-fg-muted">{c.note}</p>
        </>
      )}
    </div>
  );
}
