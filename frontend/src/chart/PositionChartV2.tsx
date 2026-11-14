import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useLiveLtp } from "../components/chartHelpers";
import { cx } from "../components/primitives/cx";
import { areasByTrade, useStrikeAreas } from "../fno/strikeArea";
import { expiryLabel, instrumentName, symbolExpiryLabel } from "../trading/model";
import type { TradeRecord } from "../types";
import { Chip } from "../ui/Chip";
import { DirectionChip } from "../ui/DirectionChip";
import { formatPrice } from "../ui/format";
import { SubTabs } from "../ui/SubTabs";
import { useFeatures } from "../ui/useFeatures";
import ChartTradeBar from "./ChartTradeBar";
import EmbeddedChartV2 from "./EmbeddedChartV2";
import {
  contractWord, fallsBack, hasUnderlying, noCandlesText, type CandleRead, positionChartAreas, positionChartLevels, positionChartTarget, type PositionChartView,
} from "./positionChart";
import type { TimeframeId } from "./timeframes";

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export interface PositionChartChange { symbol: string; exchange: string; view: PositionChartView; noCandles: boolean }

/**
 * Owner bug report 13 Nov: a position's own chart (its Chart button, in place). The contract held - an option's premium
 * candles, a future's price, a stock - with its average and the stop / targets he set on that price; one tap shows the
 * underlying with its own stop / targets and the short strike's area. A contract with no candles shows its underlying
 * with a plain line saying so (never silently). Read only: nothing here places or changes an order (ADR-0006).
 */
export default function PositionChartV2({ positionId, view, noCandles, symbol, exchange, timeframe, onTimeframe, onChange, className = "h-96" }: {
  positionId: number;
  view: PositionChartView;
  noCandles: boolean;
  /** The address's symbol and exchange: the chart reads them at once, before the position has loaded. */
  symbol: string;
  exchange: string;
  timeframe?: TimeframeId;
  onTimeframe?: (tf: TimeframeId) => void;
  onChange: (next: PositionChartChange) => void;
  className?: string;
}) {
  const [position, setPosition] = useState<TradeRecord | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    setPosition(undefined);
    // A failed read keeps the address's chart without the position's lines (never "closed" for a read that failed).
    api.listPositions().then((list) => { if (live) setPosition(list.find((p) => p.id === positionId) ?? null); }, () => undefined);
    return () => { live = false; };
  }, [positionId]);

  const p = position ?? null;
  const target = p ? positionChartTarget(p, view) : { symbol: symbol.trim().toUpperCase(), exchange: exchange.toUpperCase(), view };
  const contract = p ? positionChartTarget(p, "contract") : null;
  const key = `${target.exchange}:${target.symbol}`;

  // The held contract's own last price (the strip, and the contract chart's live bar - one read, not two).
  const contractLive = useLiveLtp(!!contract, contract?.symbol, contract?.exchange);
  const { user } = useAuth();
  const features = useFeatures();
  const areaOn = view === "underlying" && !!p && hasUnderlying(p) && !!user
    && features.on("ui_v2") && features.on("terminal") && features.on("term_strike_area");
  const strikeAreas = useStrikeAreas(areaOn);
  const area = p ? areasByTrade(strikeAreas).get(p.id) ?? null : null;

  const levels = useMemo(() => (p ? positionChartLevels(p, target.view) : []), [p, target.view]);
  const areas = useMemo(() => positionChartAreas(area, target.view), [area, target.view]);

  // A contract the broker has no candles for: its underlying, labelled so (each chart's read is its own: `key`).
  const [read, setRead] = useState<CandleRead | null>(null);
  useEffect(() => {
    if (!p || !fallsBack(p, view, read, key)) return;
    const u = positionChartTarget(p, "underlying");
    onChange({ symbol: u.symbol, exchange: u.exchange, view: "underlying", noCandles: true });
  }, [p, view, read, key, onChange]);

  const pick = (next: PositionChartView) => {
    if (!p || next === target.view) return;
    const t = positionChartTarget(p, next);
    onChange({ symbol: t.symbol, exchange: t.exchange, view: next, noCandles: false });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-space-2 [&>section]:flex [&>section]:min-h-0 [&>section]:flex-1 [&>section]:flex-col" data-position-chart={positionId} data-view={target.view}>
      {p && <PositionChartStrip position={p} view={target.view} noCandles={noCandles} ltp={contractLive.ltp?.ltp ?? null} onPick={pick} />}
      {/* CTB-1 (owner 14 Nov): Buy / Sell / Exit on the contract held (the strip above already shows its side and avg) */}
      {contract && <ChartTradeBar symbol={contract.symbol} exchange={contract.exchange} ltp={contractLive.ltp?.ltp ?? null} heldChip={false} />}
      {position === null && <p className="text-t13 text-fg-muted" data-position-gone>This position is no longer open.</p>}
      <EmbeddedChartV2 key={key} symbol={target.symbol} exchange={target.exchange} timeframe={timeframe} onTimeframe={onTimeframe} openLink={false}
        levels={levels} areas={areas} live={p && target.view === "contract" ? contractLive.ltp : undefined}
        onRead={(s) => setRead({ ...s, key })} className={className} />
    </div>
  );
}

/** The position over its chart: the contract, side, average and last price, the Option / underlying switch, and what an
 * underlying chart is (his position's underlying, or - the contract had no candles - said so plainly). */
export function PositionChartStrip({ position: p, view, noCandles, ltp, onPick }: {
  position: TradeRecord; view: PositionChartView; noCandles: boolean; ltp: number | null; onPick: (view: PositionChartView) => void;
}) {
  const expiry = expiryLabel(p.expiry) ?? symbolExpiryLabel(p.symbol);
  const underlying = hasUnderlying(p) ? (p.underlying_symbol as string).trim() : null;
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-2" data-position-strip>
        <div className="flex min-w-0 flex-wrap items-center gap-space-2">
          <span className="text-t15 font-semibold text-fg" data-instrument>{instrumentName(p.symbol, p.expiry)}</span>
          {expiry && <Chip tone="neutral" variant="meaning" name="expiry">{expiry}</Chip>}
          <DirectionChip side={p.direction === "SHORT" ? "short" : "long"} />
          <span className="font-tabular text-t13"><span className="text-fg-muted">Avg. </span><span className="text-fg">{formatPrice(p.entry_price)}</span></span>
          {ltp !== null && <span className="font-tabular text-t13" data-ltp><span className="text-fg-muted">LTP </span><span className="text-fg">{formatPrice(ltp)}</span></span>}
        </div>
        {underlying && (
          <SubTabs label="Which chart" kind="toggle" size="sm" className="w-full sm:ml-auto sm:w-auto" value={view} onChange={onPick}
            items={[{ key: "contract", label: contractWord(p) }, { key: "underlying", label: underlying }] as const} />
        )}
      </div>
      {underlying && view === "underlying" && (noCandles ? (
        <p role="status" data-no-candles className="flex flex-wrap items-center gap-x-space-2 rounded-control border border-warn/40 bg-warn-tint px-space-3 py-space-1 text-t13 text-fg">
          <span>{noCandlesText(p)}</span>
          <button type="button" onClick={() => onPick("contract")} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", focus)}>
            Try the {contractWord(p).toLowerCase()} chart again
          </button>
        </p>
      ) : (
        <p className="text-t13 text-fg-muted" data-underlying-note>{underlying}, the underlying of your {instrumentName(p.symbol, p.expiry)} position.</p>
      ))}
    </>
  );
}
