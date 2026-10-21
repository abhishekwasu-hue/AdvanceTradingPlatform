import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerCatalogEntry } from "../types";

/**
 * P2b: the broker catalog (GET /api/broker/catalog) - which brokers can be connected, the 1-2 fields each needs and
 * the 3 steps to find them. The same for everyone and it rarely changes, so it is read once per page load and shared;
 * a failed read is tried again on the next use (or `retry`).
 */

let cached: BrokerCatalogEntry[] | null = null;
let pending: Promise<BrokerCatalogEntry[]> | null = null;

export function loadBrokerCatalog(): Promise<BrokerCatalogEntry[]> {
  if (cached) return Promise.resolve(cached);
  if (!pending) {
    pending = api.brokerCatalog()
      .then((rows) => { cached = Array.isArray(rows) ? rows : []; return cached; })
      .finally(() => { pending = null; });
  }
  return pending;
}

/** Tests only. */
export function resetBrokerCatalogForTests(): void {
  cached = null;
  pending = null;
}

export function useBrokerCatalog(): { entries: BrokerCatalogEntry[] | null; failed: boolean; retry: () => void } {
  const [entries, setEntries] = useState<BrokerCatalogEntry[] | null>(cached);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    loadBrokerCatalog()
      .then((rows) => { if (live) { setEntries(rows); setFailed(false); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [attempt]);
  const retry = useCallback(() => { setFailed(false); setAttempt((n) => n + 1); }, []);
  return { entries, failed, retry };
}

/** The catalog entry for a broker id, or null. */
export function entryFor(entries: readonly BrokerCatalogEntry[] | null, broker: string): BrokerCatalogEntry | null {
  return entries?.find((e) => e.broker === broker) ?? null;
}
