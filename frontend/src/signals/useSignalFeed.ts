import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useLiveEvents } from "../live/useLiveEvents";
import type { AiAction } from "../types";
import { fetchFeed, type FeedCounts, type FeedParams, type FeedSource, type FeedTab, type SignalFeed, type SignalFeedItem } from "./feed";

export interface SignalFeedState {
  items: SignalFeedItem[];
  counts: FeedCounts | null;
  asOf: string | null;
  loading: boolean;
  /** A plain sentence when the list could not be read (the page keeps the last good list). */
  error: string | null;
  more: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  reload: () => void;
  /** The waiting proposals as /api/ai/actions gives them (risk pre-check, portfolio line), by id. */
  proposals: Map<number, AiAction>;
}

/** The most pages one read follows: past empty pages (the server stops at its read limit and says "more may follow"),
 * and, on a refresh, back to as many rows as were on screen. */
const MAX_PAGES = 10;

/** Reads from `params` (the first page, or the page after its cursor) until it has at least `want` rows (at least
 * one), the pages run out, or MAX_PAGES. */
export async function readPages(params: FeedParams, want: number, fetch: (p: FeedParams) => Promise<SignalFeed> = fetchFeed):
  Promise<{ first: SignalFeed; items: SignalFeedItem[]; cursor: string | null }> {
  const first = await fetch(params);
  const items = [...first.items];
  let cursor = first.next_cursor;
  for (let pages = 1; cursor && items.length < Math.max(want, 1) && pages < MAX_PAGES; pages++) {
    const next = await fetch({ ...params, cursor });
    items.push(...next.items);
    cursor = next.next_cursor;
  }
  return { first, items, cursor };
}

/**
 * UI2-3: one tab of the inbox for a source filter and a symbol - the first page, "Show more" pages after it, and a
 * refresh in place when a proposal changes anywhere (decided here, approved on Telegram, in another tab: the
 * `ai_actions` live topic) that reads back as many rows as were shown, so the "Show more" pages stay. The waiting
 * proposals' risk pre-check comes from the existing `GET /api/ai/actions?status=PROPOSED`, so the feed itself stays a
 * plain read.
 */
export function useSignalFeed(tab: FeedTab, source: FeedSource, symbol: string | null): SignalFeedState {
  const [feed, setFeed] = useState<SignalFeed | null>(null);
  const [rows, setRows] = useState<SignalFeedItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [counts, setCounts] = useState<SignalFeed["counts"]>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [proposals, setProposals] = useState<Map<number, AiAction>>(new Map());
  const key = `${tab}|${source}|${symbol ?? ""}`;
  const current = useRef(key);
  current.current = key;
  const shown = useRef({ key: "", count: 0 });          // what is on screen, for a refresh in place

  useEffect(() => {
    let live = true;
    setLoading(true);
    const want = shown.current.key === key ? shown.current.count : 0;      // a refresh keeps the "Show more" pages
    readPages({ tab, source, symbol }, want)
      .then(({ first, items, cursor: next }) => {
        if (!live) return;
        setFeed(first); setRows(items); setCursor(next); setError(null);
        if (first.counts) setCounts(first.counts);
      })
      .catch(() => { if (live) setError("The signals could not be read."); })
      .finally(() => { if (live) setLoading(false); });
    api.aiActions("PROPOSED")
      .then((list) => { if (live) setProposals(new Map(list.map((a) => [a.id, a]))); })
      .catch(() => { if (live) setProposals(new Map()); });
    return () => { live = false; };
  }, [key, tab, source, symbol, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  useLiveEvents(["ai_actions"], reload);

  const loadMore = useCallback(() => {
    if (!cursor || loadingMore) return;
    const asked = current.current;
    setLoadingMore(true);
    readPages({ tab, source, symbol, cursor }, 1)
      .then(({ items, cursor: next }) => {
        if (current.current !== asked) return;
        setRows((prev) => [...prev, ...items]);
        setCursor(next);
      })
      .catch(() => { if (current.current === asked) setError("More signals could not be read."); })
      .finally(() => setLoadingMore(false));
  }, [cursor, loadingMore, tab, source, symbol]);

  const sameKey = feed !== null && `${feed.tab}|${feed.source}|${feed.symbol ?? ""}` === `${tab}|${source}|${symbol?.trim().toUpperCase() ?? ""}`;
  const items = sameKey ? rows : [];
  shown.current = { key: sameKey ? key : "", count: items.length };
  return {
    items,
    counts,
    asOf: feed?.as_of ?? null,
    loading: loading && !sameKey,
    error,
    more: !!cursor,
    loadingMore,
    loadMore,
    reload,
    proposals,
  };
}
