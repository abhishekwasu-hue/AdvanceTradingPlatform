import { useEffect, useMemo, useState } from "react";
import { useBrokerChart } from "./chartHistory";
import ProChart, { chartWindowUrl, directionMarker, useLiveLtp, type PriceLineSpec } from "./ProChart";
import { useCandleSource } from "./DataSource";
import { Card, signClass } from "./ui";
import type { TradeRecord } from "../types";

const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min", "day"];

/**
 * Phase AN: the chart behind an open position. Pick a position; the card fetches broker candles
 * for what the strategy watches (the underlying for an option or future, the instrument itself
 * otherwise), draws entry, stop and targets, follows the live price and shows the running P&L on
 * the instrument actually held. Needs a logged-in broker; says so otherwise.
 */
export default function PositionChartCard({ positions }: { positions: TradeRecord[] }) {
  const source = useCandleSource(2);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [timeframe, setTimeframe] = useState("5min");

  useEffect(() => {
    if (positions.length === 0) { setSelectedId(null); return; }
    if (selectedId == null || !positions.some((p) => p.id === selectedId)) setSelectedId(positions[0].id);
  }, [positions, selectedId]);

  const p = positions.find((x) => x.id === selectedId) ?? null;
  const derivative = !!p && !!p.instrument_kind && p.instrument_kind !== "UNDERLYING" && !!p.underlying_symbol;
  const chartSymbol = p ? (derivative ? (p.underlying_symbol as string) : p.symbol) : undefined;
  const chartExchange = derivative ? "NSE" : (p?.exchange ?? "NSE");
  const broker = source.broker || undefined;
  const usable = source.usable && !!source.broker;

  // Each timeframe loads its own history; scrolling back loads older pages.
  const chart = useBrokerChart({ enabled: usable && !!chartSymbol, symbol: chartSymbol, timeframe, exchange: chartExchange, broker });
  const { candles, loading, error } = chart;

  const live = useLiveLtp(usable && !!chartSymbol, chartSymbol, chartExchange, broker);
  const contractLive = useLiveLtp(usable && derivative && !!p, p?.symbol, p?.exchange ?? "NFO", broker);

  const priceLines: PriceLineSpec[] = useMemo(() => {
    if (!p) return [];
    const lines: PriceLineSpec[] = [];
    if (derivative) {
      if (p.underlying_stop_loss != null) lines.push({ price: p.underlying_stop_loss, color: "down", title: "Underlying stop" });
      if (p.underlying_target1 != null) lines.push({ price: p.underlying_target1, color: "up", title: "Underlying T1" });
      if (p.underlying_target2 != null) lines.push({ price: p.underlying_target2, color: "up", title: "Underlying T2" });
    } else {
      lines.push({ price: p.entry_price, color: "fg", title: `Entry ${p.direction}` });
      lines.push({ price: p.stop_loss, color: "down", title: "Stop" });
      if (p.target1 != null) lines.push({ price: p.target1, color: "up", title: "Target 1" });
      if (p.target2 != null) lines.push({ price: p.target2, color: "up", title: "Target 2" });
    }
    return lines;
  }, [p, derivative]);

  const markers = useMemo(() => (p && !derivative ? [directionMarker(p.entry_time, p.direction as "LONG" | "SHORT", `Entry ${p.quantity}`)] : []), [p, derivative]);
  const held = derivative ? contractLive.ltp?.ltp ?? null : live.ltp?.ltp ?? null;
  const pnl = p && held != null ? (held - p.entry_price) * p.quantity * (p.direction === "LONG" ? 1 : -1) : null;

  if (positions.length === 0) return null;

  return (
    <Card title="Position chart">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <select value={selectedId ?? ""} onChange={(e) => setSelectedId(Number(e.target.value))} className="rounded bg-surface-2 border border-border px-2 py-1 text-fg">
          {positions.map((x) => <option key={x.id} value={x.id}>{x.symbol} · {x.direction} {x.quantity} @ {x.entry_price} · {x.mode}</option>)}
        </select>
        {p && derivative && <span className="text-fg-muted">charting the underlying <b className="text-fg">{chartSymbol}</b>; the position is in <b className="text-fg">{p.symbol}</b></span>}
        {p && (
          <span className="ml-auto flex items-center gap-3 font-tabular">
            {held != null && <span className="text-fg-muted">{derivative ? "premium" : "LTP"} <b className="text-fg">{held.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</b></span>}
            {pnl != null && <span className={`font-bold ${signClass(pnl, 0)}`}>{Math.round(pnl) > 0 ? "+" : Math.round(pnl) < 0 ? "-" : ""}₹{Math.abs(pnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })} unrealised</span>}
            {source.sources.filter((s) => s.usable).length > 1 && (
              <select value={source.broker} onChange={(e) => source.setBroker(e.target.value)} className="rounded bg-surface-2 border border-border px-1 py-0.5 text-[11px] text-fg">
                {source.sources.filter((s) => s.usable).map((s) => <option key={`${s.broker}:${s.account_label}`} value={s.broker}>{s.broker}</option>)}
              </select>
            )}
          </span>
        )}
      </div>
      {!usable ? (
        <div className="rounded-lg border border-warn/30 bg-warn/[0.06] px-3 py-2 text-xs text-warn">
          Live chart needs a broker with a valid session. Add the API key under Settings &gt; Brokers (stored encrypted, never in chat or the environment) and log in; the chart, live price and P&amp;L then appear here.
        </div>
      ) : (
        <>
          {error && <div className="mb-2 text-xs text-down">{error}</div>}
          {loading && candles.length === 0 && <div className="text-xs text-fg-muted">Fetching candles…</div>}
          <ProChart candles={candles} symbol={chartSymbol} timeframe={timeframe} timeframes={TIMEFRAMES} onTimeframeChange={setTimeframe}
                    priceLines={priceLines} markers={markers} live={timeframe === "day" ? null : live.ltp} liveError={live.error} height={340}
                    defaultIndicators={["ema_fast", "ema_slow", "vwap", "volume"]}
                    openUrl={chartSymbol ? chartWindowUrl(chartSymbol, timeframe, chartExchange, broker) : undefined}
                    deployable={!!chartSymbol} exchange={chartExchange}
                    onLoadOlder={chart.loadOlder} loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} />
        </>
      )}
    </Card>
  );
}
