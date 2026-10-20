import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useLiveEvents } from "../live/useLiveEvents";
import type { AiAction } from "../types";
import { fetchFeed, type FeedSource, type FeedTab, type SignalFeed, type SignalFeedItem } from "./feed";

export interface SignalFeedState {
  items: SignalFeedItem[];
  counts: { waiting: number; today: number } | null;
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

/**
 * UI2-3: one tab of the inbox for a source filter and a symbol - the first page, "Show more" pages after it, and a
 * reload when a proposal changes anywhere (approved on Telegram, in another tab: the `ai_actions` live topic). The
 * waiting proposals' risk pre-check comes from the existing `GET /api/ai/actions?status=PROPOSED`, so the feed itself
 * stays a plain read.
 */
export function useSignalFeed(tab: FeedTab, source: FeedSource, symbol: string | null): SignalFeedState {
  const [feed, setFeed] = useState<SignalFeed | null>(null);
  const [extra, setExtra] = useState<SignalFeedItem[]>([]);
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

  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchFeed({ tab, source, symbol })
      .then((f) => {
        if (!live) return;
        setFeed(f); setExtra([]); setCursor(f.next_cursor); setError(null);
        if (f.counts) setCounts(f.counts);
      })
      .catch((e) => { if (live) setError(String(e)); })
      .finally(() => { if (live) setLoading(false); });
    api.aiActions("PROPOSED")
      .then((list) => { if (live) setProposals(new Map(list.map((a) => [a.id, a]))); })
      .catch(() => { if (live) setProposals(new Map()); });
    return () => { live = false; };
  }, [tab, source, symbol, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  useLiveEvents(["ai_actions"], reload);

  const loadMore = useCallback(() => {
    if (!cursor || loadingMore) return;
    const asked = current.current;
    setLoadingMore(true);
    fetchFeed({ tab, source, symbol, cursor })
      .then((f) => {
        if (current.current !== asked) return;
        setExtra((prev) => [...prev, ...f.items]);
        setCursor(f.next_cursor);
      })
      .catch((e) => { if (current.current === asked) setError(String(e)); })
      .finally(() => setLoadingMore(false));
  }, [cursor, loadingMore, tab, source, symbol]);

  const sameKey = feed !== null && `${feed.tab}|${feed.source}|${feed.symbol ?? ""}` === `${tab}|${source}|${symbol?.trim().toUpperCase() ?? ""}`;
  return {
    items: sameKey ? [...feed.items, ...extra] : [],
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
