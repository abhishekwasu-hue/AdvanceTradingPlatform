import { useEffect, useMemo, useState } from "react";
import { useLiveLtp } from "../components/chartHelpers";
import { cx } from "../components/primitives/cx";
import { CrosshairLink } from "../charting/crosshairLink";
import type { LightweightEngine } from "../charting/lightweight";
import { RangeLink } from "../charting/rangeLink";
import type { OHLCVBar } from "../types";
import { EMPTY, formatChange, formatPct, formatPrice } from "../ui/format";
import { usePageVisible } from "../utils/usePageVisible";
import type { OverlayId } from "./chartCore";
import { marketChip } from "./chartsPage";
import EmbeddedChartV2 from "./EmbeddedChartV2";
import { headerFigures, shownSides, sidesOf, type OptionRight, type PairView } from "./optionPair";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, type TimeframeId } from "./timeframes";
import { useMarketStatus } from "./useMarketStatus";

/**
 * CEPE-1 (owner 13 Nov): an option's chart sheet shows the CE on top and the PE of the same strike and expiry below,
 * each half the height, so the sheet is filled. The two charts share one timeframe and one control bar (on the top
 * chart shown), and their crosshair and scroll / zoom are linked; each keeps its own price axis. "CE · PE · Both"
 * picks one chart (it then fills the sheet). Each chart's header: its contract, last price and change (colour by
 * meaning). Candles come from the user's broker the same way as any chart; a side without candles says so on its own
 * half and the other side stays. Position lines (avg / stop / target) are not drawn here yet: the contract-chart
 * overlay (#1050) is not on main.
 */
const VIEWS: { value: PairView; label: string }[] = [{ value: "CE", label: "CE" }, { value: "PE", label: "PE" }, { value: "both", label: "Both" }];
const TONE = { up: "text-up", down: "text-down", flat: "text-fg-muted" } as const;
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const NO_CANDLES: readonly OHLCVBar[] = [];

export interface OptionPairChartV2Props {
  /** The contract the sheet was opened for (either side). */
  symbol: string;
  exchange: string;
  timeframe?: TimeframeId;
  onTimeframe?: (tf: TimeframeId) => void;
}

export default function OptionPairChartV2({ symbol, exchange, timeframe: given, onTimeframe }: OptionPairChartV2Props) {
  const sides = sidesOf(symbol, exchange);
  const [view, setView] = useState<PairView>("both");
  const [tf, setTf] = useState<TimeframeId>(given ?? DEFAULT_TIMEFRAME);
  useEffect(() => { if (given) setTf(given); }, [given]);
  const pickTf = (next: TimeframeId) => { setTf(next); onTimeframe?.(next); };
  const [overlays, setOverlays] = useState<OverlayId[]>([]);
  // one crosshair link and one scroll / zoom link for the two charts (index 0 = CE, 1 = PE)
  const cross = useMemo(() => new CrosshairLink((i) => [1 - i]), []);
  const range = useMemo(() => new RangeLink(), []);
  useEffect(() => () => { cross.dispose(); range.dispose(); }, [cross, range]);
  if (!sides) return null;
  const shown = shownSides(view);
  return (
    <div data-option-pair={view} className="flex min-h-0 flex-1 flex-col gap-space-2">
      <div className="flex items-center justify-between gap-space-2">
        <span className="text-t12 font-medium text-fg-muted" id="option-pair-view">Show</span>
        <div role="radiogroup" aria-labelledby="option-pair-view" className="flex gap-space-1 rounded-control bg-surface-2 p-space-1" data-option-pair-switch>
          {VIEWS.map((o) => (
            <button key={o.value} type="button" role="radio" aria-checked={view === o.value} data-option={o.value} onClick={() => setView(o.value)}
              className={cx("inline-flex min-h-11 min-w-12 items-center justify-center rounded-control px-space-3 text-t13 font-semibold md:min-h-8", focus,
                view === o.value ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {shown.map((right, i) => (
        <PairSide key={right} right={right} symbol={sides[right]} exchange={exchange} timeframe={tf} onTimeframe={pickTf}
          toolbar={i === 0} overlays={overlays} onOverlays={setOverlays}
          onEngine={(engine) => { const index = right === "CE" ? 0 : 1; cross.register(index, engine); range.register(index, engine); }} />
      ))}
    </div>
  );
}

function PairSide({ right, symbol, exchange, timeframe, onTimeframe, toolbar, overlays, onOverlays, onEngine }: {
  right: OptionRight; symbol: string; exchange: string; timeframe: TimeframeId; onTimeframe: (tf: TimeframeId) => void;
  toolbar: boolean; overlays: OverlayId[]; onOverlays: (o: OverlayId[]) => void; onEngine: (engine: LightweightEngine | null) => void;
}) {
  const [candles, setCandles] = useState<readonly OHLCVBar[]>(NO_CANDLES);
  const visible = usePageVisible();
  const { status, at } = useMarketStatus();
  const marketOpen = marketChip(status, exchange, at || Date.now())?.open ?? null;
  // one live read per contract, shared by the header and the chart (the chart then makes no read of its own)
  const { ltp } = useLiveLtp(visible && marketOpen !== false && candles.length > 0, symbol, exchange);
  const fig = headerFigures(candles, ltp?.ltp ?? null, DAILY_OR_LONGER.has(timeframe));
  return (
    <div data-option-side={right} className="flex min-h-0 flex-1 flex-col gap-space-1 [&>section]:flex [&>section]:min-h-0 [&>section]:flex-1 [&>section]:flex-col">
      <header className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-space-2 px-space-1" data-option-side-header>
        <h3 className="min-w-0 break-words text-t13 font-semibold text-fg">{symbol}</h3>
        <p className="flex shrink-0 items-baseline gap-space-2 font-tabular">
          <span className="text-t14 font-semibold text-fg" data-option-ltp>{fig.last == null ? EMPTY : formatPrice(fig.last)}</span>
          {fig.change != null && (
            <span className={cx("text-t12 font-semibold", TONE[fig.meaning])} data-option-change={fig.meaning}>
              {formatChange(fig.change)} ({formatPct(fig.pct)})
            </span>
          )}
        </p>
      </header>
      <EmbeddedChartV2 symbol={symbol} exchange={exchange} timeframe={timeframe} onTimeframe={onTimeframe} openLink={false}
        toolbar={toolbar} overlays={overlays} onOverlays={onOverlays} onCandles={setCandles} onEngine={onEngine}
        live={ltp ? { ltp: ltp.ltp, timestamp: ltp.timestamp, fetched_at: ltp.fetched_at } : null}
        emptyNote={`Your broker has no prices for ${symbol} on this timeframe yet.`} className="min-h-[9rem] flex-1" />
    </div>
  );
}
