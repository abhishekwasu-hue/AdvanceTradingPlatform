import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { withTick } from "../charting/ltpHub";
import { liveTicks } from "../live/liveTicks";
import type { LtpResponse } from "../types";

/** Chart helpers without the chart engine: importing them never loads the `charts` chunk (P1.1). */
export function chartWindowUrl(symbol: string, timeframe = "5min", exchange = "NSE", broker?: string): string {
  const q = new URLSearchParams({ chart: symbol, tf: timeframe, exchange });
  if (broker) q.set("broker_name", broker);
  return `${window.location.pathname}?${q.toString()}`;
}

/** The last price of one symbol, polled every `intervalMs` while the tab is visible.
 * AUD-G2.2 (`livePush`, flag `live_push`): the symbol is registered on the live stream and each pushed tick moves the
 * polled price; a timer tick makes no request while the symbol has a fresh tick (the push carries it), so the poll is
 * only the first read and the fallback (stream down or quiet, no tick within the server's limit). `livePush` false:
 * exactly as before. */
export function useLiveLtp(enabled: boolean, symbol: string | undefined, exchange = "NSE", broker?: string, intervalMs = 5000, livePush = false) {
  const [ltp, setLtp] = useState<LtpResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setLtp(null); setError(null);
    if (!enabled || !symbol) return;
    let cancelled = false;
    const store = livePush ? liveTicks() : null;
    let polled = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      if (store && polled && store.tickFresh(symbol, exchange)) return;
      try { const r = await api.marketDataLtp(symbol, exchange, broker); if (!cancelled) { polled = true; setLtp(r); setError(null); } }
      catch (e) { if (!cancelled) setError(errorSentence(e)); }
    };
    const pushed = store ? [
      store.register([{ symbol, exchange }]).release,
      store.onTick(symbol, exchange, (t) => {
        if (!cancelled) setLtp((prev) => (prev ? withTick(prev, t, new Date().toISOString()) : prev));   // a tick moves a polled price
      }),
    ] : [];
    void tick();
    const id = window.setInterval(tick, intervalMs);
    return () => { cancelled = true; window.clearInterval(id); pushed.forEach((off) => off()); };
  }, [enabled, symbol, exchange, broker, intervalMs, livePush]);
  return { ltp, error };
}
