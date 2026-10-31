import { useCallback, useEffect, useState } from "react";
import { istDay } from "./model";

/**
 * Settings redesign D: "Hide for today" on a Needs attention row. The hidden row ids are kept per signed-in user on this
 * device (localStorage, every access in try/catch) with the IST day they were hidden; on the next day the list is empty
 * again, so the row comes back while it still needs the trader. Nothing is sent to the server.
 */

const PREFIX = "atp_attention_hidden:";

interface Hidden { day: string; ids: string[] }

export function storageKey(userKey: number | string): string {
  return PREFIX + userKey;
}

/** The ids hidden today (IST), from a stored value; anything unreadable or from another day is nothing hidden. */
export function hiddenToday(raw: string | null, nowMs: number): Set<string> {
  if (!raw) return new Set();
  try {
    const v = JSON.parse(raw) as Partial<Hidden>;
    if (v.day !== istDay(nowMs) || !Array.isArray(v.ids)) return new Set();
    return new Set(v.ids.filter((id): id is string => typeof id === "string"));
  } catch {
    return new Set();
  }
}

/** The stored value after hiding `id` today. */
export function withHidden(raw: string | null, id: string, nowMs: number): string {
  const ids = hiddenToday(raw, nowMs);
  ids.add(id);
  return JSON.stringify({ day: istDay(nowMs), ids: [...ids] } satisfies Hidden);
}

function read(key: string | null): string | null {
  if (!key) return null;
  try { return localStorage.getItem(key); } catch { return null; }
}

/** The rows the trader hid today and the function that hides one. Signed out (`userKey` null): nothing is hidden. */
export function useHiddenToday(userKey: number | string | null, nowMs: number): { hidden: ReadonlySet<string>; hide: (id: string) => void } {
  const key = userKey === null ? null : storageKey(userKey);
  const [raw, setRaw] = useState<string | null>(() => read(key));
  useEffect(() => { setRaw(read(key)); }, [key]);
  const hide = useCallback((id: string) => {
    const next = withHidden(read(key) ?? raw, id, Date.now());
    setRaw(next);
    if (key) { try { localStorage.setItem(key, next); } catch { /* intentional: storage unavailable: hidden for this visit only */ } }
  }, [key, raw]);
  return { hidden: hiddenToday(raw, nowMs), hide };
}
