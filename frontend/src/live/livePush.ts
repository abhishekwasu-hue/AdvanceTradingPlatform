import { useEffect, useRef, useState } from "react";
import { useFeatures } from "../ui/useFeatures";
import { liveTicks, tickKey, type LiveTick, type LiveTicksStore, type TickItem } from "./liveTicks";

/**
 * AUD-G2.2 (order ATP_ORDER_AUDIT_28OCT G2.2; flag `live_push`, with `ui_v2` + `data_hub`; OFF by default): live prices
 * pushed end to end. The broker's websocket feeds the data hub (app/market_data/stream.py, hub.py), the hub feeds this
 * tab's live stream (`ticks` events, src/live/liveTicks.ts), and with the push on the views that used to poll on a
 * timer follow those ticks instead:
 * - a chart's last price is not polled while its symbol has a fresh tick (chartHelpers.useLiveLtp, charting/ltpHub.ts);
 * - intraday candles are read again when a tick falls in a new bar (plus the server's settle time), not every minute
 *   (chart/useChartCandles.ts); the Today sparklines the same way (dashboard/indexSparks.ts);
 * - the chart's "Live" / "Price delayed" chip reads the age of the symbol's last tick (chart/liveChip.ts).
 * Fallback: whenever the push is not live (flag off, stream down or quiet for two heartbeats) or a symbol has had no
 * tick within the server's tick-age limit, each view keeps its own timer exactly as before. Prices only: nothing here
 * places, blocks or changes an order.
 */
export const LIVE_PUSH_FLAG = "live_push";

/** The push is wanted for this organisation: the flag and the two it needs. Pure. */
export function livePushOn(on: (name: string) => boolean): boolean {
  return on(LIVE_PUSH_FLAG) && on("data_hub") && on("ui_v2");
}

export function useLivePushOn(): boolean {
  const features = useFeatures();
  return livePushOn((name) => features.on(name));
}

/** A tick's own time (the exchange's), else the time this browser got it. Pure. */
export function tickTime(tick: Pick<LiveTick, "ts">, receivedAt: number): number {
  const at = tick.ts ? Date.parse(tick.ts) : NaN;
  return Number.isFinite(at) ? at : receivedAt;
}

/** Says once per bar boundary that a tick fell in a later bar than the newest one shown (the same bucket rule as the
 * chart's live fold, utils/indicators.ts applyLivePrice: buckets counted from the newest bar's own start). */
export class BarCloseWatch {
  private done = -Infinity;

  due(tickMs: number, lastStartMs: number | null, bucketMs: number): boolean {
    if (lastStartMs === null || !Number.isFinite(lastStartMs) || !Number.isFinite(tickMs) || !(bucketMs > 0)) return false;
    if (tickMs < lastStartMs + bucketMs) return false;
    const boundary = lastStartMs + Math.floor((tickMs - lastStartMs) / bucketMs) * bucketMs;
    if (boundary <= this.done) return false;
    this.done = boundary;
    return true;
  }
}

/** While `enabled`: calls `onClose` (after the server's bar settle time) when a tick of one of `items` falls in a bar
 * after the newest one `lastStart` gives. The tick already held when the hook starts is not a close. */
export function useBarCloseReload(enabled: boolean, items: readonly TickItem[], bucketMs: number, lastStart: () => number | null,
                                  onClose: () => void, store?: LiveTicksStore): void {
  const lastRef = useRef(lastStart);
  lastRef.current = lastStart;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const key = items.map((i) => tickKey(i.symbol, i.exchange)).join(",");
  useEffect(() => {
    if (!enabled || !key || !(bucketMs > 0)) return undefined;
    const s = store ?? liveTicks();
    const watch = new BarCloseWatch();
    const timers = new Set<ReturnType<typeof setTimeout>>();
    let armed = false;
    const offs = key.split(",").map((k) => {
      const [exchange, ...rest] = k.split(":");
      return s.onTick(rest.join(":"), exchange, (t) => {
        if (!armed || !watch.due(tickTime(t, Date.now()), lastRef.current(), bucketMs)) return;
        const id = setTimeout(() => { timers.delete(id); closeRef.current(); }, s.push?.barSettleMs ?? 0);
        timers.add(id);
      });
    });
    armed = true;
    return () => { offs.forEach((off) => off()); timers.forEach((id) => clearTimeout(id)); };
  }, [enabled, key, bucketMs, store]);
}

/** When this tab got `symbol`'s last tick, kept current with each tick while `enabled`; null before the first. */
export function useTickSeenAt(enabled: boolean, symbol: string | undefined, exchange: string, store?: LiveTicksStore): number | null {
  const [seen, setSeen] = useState<number | null>(null);
  useEffect(() => {
    setSeen(null);
    if (!enabled || !symbol) return undefined;
    const s = store ?? liveTicks();
    return s.onTick(symbol, exchange, () => setSeen(s.tickSeenAt(symbol, exchange) ?? null));
  }, [enabled, symbol, exchange, store]);
  return seen;
}
