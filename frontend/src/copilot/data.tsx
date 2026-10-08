import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { AiAction, DailyBrief, NewsFeedItem } from "../types";

/**
 * What more than one tab needs, loaded once by the Copilot page: today's briefing (the hero's regime colour and the
 * Market Pulse tab), the open AI proposals (the Watchtower badge) and the high-severity news (the News Radar badge).
 */
const NEWS_SEEN_KEY = "atp_copilot_news_seen";
export const HIGH_SEVERITY = 4;

export interface CopilotData {
  brief: DailyBrief | null;
  briefError: unknown;
  briefLoading: boolean;
  reloadBrief: () => void;
  pending: AiAction[];
  reloadActions: () => void;
  /** High-severity feed items published since the trader last opened the News Radar. */
  newHighNews: number;
  markNewsSeen: (items?: NewsFeedItem[]) => void;
}

const Ctx = createContext<CopilotData | null>(null);

export function lastNewsSeen(): number {
  try { return Number(localStorage.getItem(NEWS_SEEN_KEY) ?? 0) || 0; } catch { return 0; }
}

/** The badge count: high-severity items newer than the last visit. */
export function countNewHigh(items: NewsFeedItem[], seenAt: number): number {
  return items.filter((i) => i.classification.severity >= HIGH_SEVERITY && new Date(i.published_at).getTime() > seenAt).length;
}

export function CopilotDataProvider({ children }: { children: ReactNode }) {
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [briefError, setBriefError] = useState<unknown>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [pending, setPending] = useState<AiAction[]>([]);
  const [newHighNews, setNewHighNews] = useState(0);

  const reloadBrief = useCallback(() => {
    setBriefLoading(true);
    api.aiBrief("en").then((b) => { setBrief(b); setBriefError(null); }).catch(setBriefError).finally(() => setBriefLoading(false));
  }, []);
  const reloadActions = useCallback(() => {
    api.aiActions("PROPOSED").then(setPending).catch(() => setPending([]));
  }, []);
  const markNewsSeen = useCallback((items?: NewsFeedItem[]) => {
    const latest = Math.max(Date.now(), ...(items ?? []).map((i) => new Date(i.published_at).getTime()));
    try { localStorage.setItem(NEWS_SEEN_KEY, String(latest)); } catch { /* storage unavailable */ }
    setNewHighNews(0);
  }, []);

  useEffect(() => {
    reloadBrief();
    reloadActions();
    // The feed may be switched off for this organisation - then there is simply no badge.
    api.newsFeedItems(24, HIGH_SEVERITY).then((items) => setNewHighNews(countNewHigh(items, lastNewsSeen()))).catch(() => setNewHighNews(0));
  }, [reloadBrief, reloadActions]);

  const value = useMemo(() => ({ brief, briefError, briefLoading, reloadBrief, pending, reloadActions, newHighNews, markNewsSeen }),
    [brief, briefError, briefLoading, reloadBrief, pending, reloadActions, newHighNews, markNewsSeen]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCopilotData(): CopilotData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCopilotData outside CopilotDataProvider");
  return v;
}
