import { useEffect, useMemo, useState } from "react";
import { eodApi, type EodClose } from "../api/eod";

/**
 * M86 D2: the last end-of-day close of each symbol (NSE's files, `GET /api/market-data/eod/closes`), read once per set of
 * symbols - the closes change only once a day. An empty list reads nothing; a failed read leaves the map empty ("–").
 */
export function useEodCloses(symbols: readonly string[]): ReadonlyMap<string, EodClose> {
  const key = useMemo(() => [...new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))].sort().join(","), [symbols]);
  const [closes, setCloses] = useState<{ key: string; map: ReadonlyMap<string, EodClose> }>({ key: "", map: new Map() });
  useEffect(() => {
    if (!key) return undefined;
    const stop = new AbortController();
    eodApi.closes(key.split(","), stop.signal)
      .then((reply) => setCloses({ key, map: new Map(reply.items.map((r) => [r.symbol, r])) }))
      .catch(() => { if (!stop.signal.aborted) setCloses({ key, map: new Map() }); });
    return () => stop.abort();
  }, [key]);
  return closes.key === key ? closes.map : EMPTY_MAP;
}

const EMPTY_MAP: ReadonlyMap<string, EodClose> = new Map();
