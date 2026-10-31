import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError, serverReason } from "../api/errors";
import { usePageVisible } from "../utils/usePageVisible";
import { chooseExpiry, pollEvery } from "./model";
import type { ChainExpiries, LiveChain } from "./types";

/**
 * OC1: the page's data, with no choice left to the trader but the symbol and the expiry.
 * 1. The symbol's expiry list (`GET /option-chain/{symbol}/expiries`): the server's default is selected at once (the
 *    nearest; on an expiry day the next one). A failed list still shows the chain: the broker's nearest expiry.
 * 2. The chain (`GET /option-chain/{symbol}/live`), read again every 15 s in market hours (every 5 min when the market
 *    is closed) while the browser tab is shown; nothing is read while it is hidden, and a tab shown again reads at once
 *    when its last answer is older than one interval.
 * A new symbol or expiry drops the old chain, so its numbers are never shown under the new name.
 */
export type ChainProblem = "no-session" | "plan" | "failed";

/** Step 1, pure: the list and the expiry it selects. A failed read selects nothing (the broker's nearest is read). */
export async function loadExpiryChoice(symbol: string, read: (symbol: string) => Promise<ChainExpiries>, wanted: string | null = null):
  Promise<{ list: ChainExpiries | null; expiry: string | null }> {
  try {
    const list = await read(symbol);
    return { list, expiry: chooseExpiry(list, wanted) };
  } catch {
    return { list: null, expiry: null };
  }
}

/** Step 2, pure: read after `firstDelay`, then again `delay()` after each answer, until stopped. */
export function startChainPolling(o: {
  read: () => Promise<LiveChain>;
  firstDelay: number;
  delay: () => number;
  onData: (chain: LiveChain) => void;
  onError: (e: unknown) => void;
  onRead?: () => void;
}): () => void {
  let alive = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const run = () => {
    o.onRead?.();
    o.read()
      .then((chain) => { if (alive) o.onData(chain); })
      .catch((e: unknown) => { if (alive) o.onError(e); })
      .finally(() => { if (alive) timer = setTimeout(run, o.delay()); });
  };
  timer = setTimeout(run, o.firstDelay);
  return () => { alive = false; if (timer !== undefined) clearTimeout(timer); };
}

/** REV-1 M2: a tier refusal whose reason is about the user's broker session (none linked, none logged in today), not
 * about the plan. With byob on, every 409 carries `X-Data-Tier`, so the header alone cannot tell the two apart. */
const NO_SESSION_REASON = /\blog in\b|no broker linked|account linked|no live session|no valid (session )?token|session has ended/i;

/** A 409 with `X-Data-Tier` is the bring-your-own-broker plan refusing a live read of this symbol - unless its reason is
 * a missing or lapsed session; a plain 409 is no usable broker session; anything else failed. */
export function problemOf(e: unknown): ChainProblem {
  if (!(e instanceof ApiError) || e.status !== 409) return "failed";
  if (!e.dataTier) return "no-session";
  return NO_SESSION_REASON.test(e.detail) ? "no-session" : "plan";
}

/** REV-1 M2: the server's own readable reason for a failed chain read ("" when it sent none). */
export function problemReasonOf(e: unknown): string {
  return e instanceof ApiError ? serverReason(e.detail) : "";
}

export interface OptionChainData {
  expiries: ChainExpiries | null;
  expiry: string | null;
  setExpiry: (expiry: string) => void;
  live: LiveChain | null;
  /** True until the first chain of this symbol and expiry has answered (or failed). */
  loading: boolean;
  problem: ChainProblem | null;
  /** REV-1 M2: the server's own reason for `problem` ("" when it sent none a trader can read). */
  problemReason?: string;
}

/** FNO-3: `wanted` is the expiry the address carried (the symbol hub's tabs keep it); used for the first list only,
 * while that list holds it. */
export function useOptionChain(symbol: string, marketOpen: boolean | null, wanted: string | null = null): OptionChainData {
  const visible = usePageVisible();
  const [expiries, setExpiries] = useState<ChainExpiries | null>(null);
  // The symbol whose expiry list has answered: the chain is read only for that symbol (never the old expiry under a
  // new symbol in the render before the list effect has run).
  const [listFor, setListFor] = useState<string | null>(null);
  const listReady = listFor === symbol;
  const [expiry, setExpiryState] = useState<string | null>(null);
  const [live, setLive] = useState<LiveChain | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<ChainProblem | null>(null);
  const [problemReason, setProblemReason] = useState("");
  const lastRead = useRef(0);
  // A page left open past an expiry: the server refuses the passed date (422) and the list is read again.
  const [listRound, setListRound] = useState(0);
  const retriedFor = useRef<string | null>(null);
  const wantedRef = useRef<string | null>(wanted);

  // 1. The expiry list for the symbol; the default expiry is chosen as soon as it answers.
  useEffect(() => {
    let alive = true;
    lastRead.current = 0;
    setExpiries(null);
    setListFor(null);
    setExpiryState(null);
    setLive(null);
    setLoading(true);
    setProblem(null);
    const carried = wantedRef.current;
    wantedRef.current = null;
    void loadExpiryChoice(symbol, api.optionChainExpiries, carried).then(({ list, expiry: chosen }) => {
      if (!alive) return;
      setExpiries(list);
      setExpiryState(chosen);
      setListFor(symbol);
    });
    return () => { alive = false; };
  }, [symbol, listRound]);

  const expiryRef = useRef<string | null>(null);
  expiryRef.current = expiry;
  const setExpiry = useCallback((next: string) => {
    if (expiryRef.current === next) return;
    lastRead.current = 0;
    setLive(null);
    setLoading(true);
    setProblem(null);
    setExpiryState(next);
  }, []);

  // 2. The chain, now and then on a timer while the tab is shown. The interval follows the market's state at each read.
  const openRef = useRef<boolean | null>(marketOpen);
  // The server's own answer first (its exchange's session), the system status until the first answer.
  openRef.current = live?.market_open ?? marketOpen;
  const hasLive = useRef(false);
  hasLive.current = live !== null;
  useEffect(() => {
    if (!listReady || !visible) return undefined;
    const since = Date.now() - lastRead.current;
    return startChainPolling({
      read: () => api.optionChainLive(symbol, expiry),
      // Nothing on screen yet, or never read: at once; else what is left of the interval (a tab shown again).
      firstDelay: lastRead.current === 0 || !hasLive.current ? 0 : Math.max(0, pollEvery(openRef.current) - since),
      delay: () => pollEvery(openRef.current),
      onRead: () => { lastRead.current = Date.now(); },
      onData: (chain) => {
        setLive(chain);
        setProblem(null);
        setLoading(false);
        // Opened without a list (the broker's nearest expiry): the answer names it.
        if (!expiry && chain.expiry) setExpiryState(chain.expiry);
      },
      onError: (e) => {
        // Once per expiry, so a list that still names it shows "could not load" instead of reading in a loop.
        if (e instanceof ApiError && e.status === 422 && expiry && retriedFor.current !== `${symbol}|${expiry}`) {
          retriedFor.current = `${symbol}|${expiry}`;
          setListRound((n) => n + 1);
          return;
        }
        setProblem(problemOf(e));
        setProblemReason(problemReasonOf(e));
        setLoading(false);
      },
    });
  }, [symbol, expiry, listReady, visible]);

  return { expiries, expiry, setExpiry, live, loading, problem, problemReason };
}
