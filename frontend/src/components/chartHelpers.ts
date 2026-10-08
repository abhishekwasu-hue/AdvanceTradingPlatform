import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LtpResponse } from "../types";

/** Chart helpers without the chart engine: importing them never loads the `charts` chunk (P1.1). */
export function chartWindowUrl(symbol: string, timeframe = "5min", exchange = "NSE", broker?: string): string {
  const q = new URLSearchParams({ chart: symbol, tf: timeframe, exchange });
  if (broker) q.set("broker_name", broker);
  return `${window.location.pathname}?${q.toString()}`;
}

export function useLiveLtp(enabled: boolean, symbol: string | undefined, exchange = "NSE", broker?: string, intervalMs = 5000) {
  const [ltp, setLtp] = useState<LtpResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setLtp(null); setError(null);
    if (!enabled || !symbol) return;
    let cancelled = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try { const r = await api.marketDataLtp(symbol, exchange, broker); if (!cancelled) { setLtp(r); setError(null); } }
      catch (e) { if (!cancelled) setError(String(e)); }
    };
    void tick();
    const id = window.setInterval(tick, intervalMs);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [enabled, symbol, exchange, broker, intervalMs]);
  return { ltp, error };
}
