import { useCallback, useEffect, useSyncExternalStore } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { BrokerConnection } from "../types";

/**
 * P2b: this organisation's broker connections (GET /api/broker/connections), read once and shared by everything that
 * shows them - the sidebar dot, the phone's More dot, the sign-in prompt, the Broker page and the Dashboard banner -
 * so they always agree and the server is asked once, not once per component. Re-read on `refresh()` (after a connect,
 * renew or remove), when the user changes, and when this tab is shown again (a login finished in another tab).
 *
 * `rows` is null while not known (loading, or the read failed): callers then show nothing rather than a guess. A
 * failed re-read keeps the last good answer. Another user's answer is never returned, not even for one render.
 */

interface Snapshot { userId: number | null; rows: BrokerConnection[] | null; failed: boolean }

let snapshot: Snapshot = { userId: null, rows: null, failed: false };
let requestedFor: number | null = null;
let sequence = 0;
const listeners = new Set<() => void>();

function set(next: Snapshot) {
  snapshot = next;
  listeners.forEach((l) => l());
}

function load(userId: number) {
  requestedFor = userId;
  const mine = ++sequence;
  api.brokerConnections()
    .then((rows) => {
      if (mine !== sequence) return;
      set({ userId, rows: Array.isArray(rows) ? rows : [], failed: false });
    })
    .catch(() => {
      if (mine !== sequence) return;
      set({ userId, rows: snapshot.userId === userId ? snapshot.rows : null, failed: true });
    });
}

let watching = false;
function watchVisibility() {
  if (watching || typeof document === "undefined") return;
  watching = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && requestedFor !== null) load(requestedFor);
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  watchVisibility();
  return () => { listeners.delete(listener); };
}

const getSnapshot = () => snapshot;

/** Tests only: start from nothing. */
export function resetBrokerConnectionsForTests(): void {
  snapshot = { userId: null, rows: null, failed: false };
  requestedFor = null;
  sequence += 1;
}

export interface BrokerConnections {
  /** null while not known. */
  rows: BrokerConnection[] | null;
  /** True when the last read failed (rows keep the previous good answer, if any). */
  failed: boolean;
  refresh: () => void;
}

export function useBrokerConnections(): BrokerConnections {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (userId === null) { requestedFor = null; return; }
    if (requestedFor !== userId) load(userId);
  }, [userId]);

  const refresh = useCallback(() => { if (userId !== null) load(userId); }, [userId]);
  const mine = userId !== null && snap.userId === userId;
  return { rows: mine ? snap.rows : null, failed: mine ? snap.failed : false, refresh };
}
