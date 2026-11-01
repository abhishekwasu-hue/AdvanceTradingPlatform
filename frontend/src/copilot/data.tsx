import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { AiAction, DailyBrief, MarketMemory, NewsFeedItem } from "../types";

/**
 * What more than one tab needs, loaded once by the Copilot page: today's briefing (the hero's regime colour and the
 * Market Pulse tab), the open AI proposals (the Watchtower badge) and the high-severity news (the News Radar badge).
 * M83 A2 / A3: also the ONE market store of the Market Pulse tab - the market memory and the tab's single "Read now":
 * one press reads the market (POST /ai/market-memory/refresh, the worker's own read) and then reloads the briefing and
 * the memory, and bumps `readVersion` so every other card (the thesis) reloads too - no page reload.
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
  /** The open proposals or the news count (the tab badges) are still loading. */
  badgesLoading: boolean;
  /** M83: the market memory (one copy for every card), loaded on first use. */
  memory: MarketMemory | null;
  memoryError: unknown;
  loadMemory: () => Promise<void>;
  /** M83: the tab's one "Read now" and its outcome. */
  readNow: () => Promise<void>;
  reading: boolean;
  /** What the last press stored and why anything was not stored (null before a press). */
  readReport: ReadOutcome | null;
  /** Bumped after every press: cards with their own data reload on it. */
  readVersion: number;
}

export interface ReadOutcome {
  at: string;
  symbols: number;
  cues: number;
  globals: number;
  /** The exact reasons a part of the read stored nothing, in plain words. */
  reasons: string[];
  /** The whole press failed (no read at all): the error, shown in plain words by the tab. */
  error: unknown;
}

/** A read report's errors in plain words: "NIFTY 50: BrokerAPIError: token invalid" -> "NIFTY 50: token invalid". */
export function plainReasons(errors: readonly string[] | undefined): string[] {
  return (errors ?? []).map((e) => e.replace(/^broker:\s*/i, "").replace(/\b[A-Z][A-Za-z]*(Error|Exception):\s*/g, "").trim()).filter(Boolean);
}

/** Exported for the tests, which render the cards from a given store. */
export const CopilotDataContext = createContext<CopilotData | null>(null);
const Ctx = CopilotDataContext;

export interface ReadNowSteps {
  /** POST /ai/market-memory/refresh - the worker's own read, through this user's own broker session. */
  refresh: () => Promise<MarketMemory>;
  /** Reload the briefing (Today's read, sentiment, the dial and the checklist come from it). */
  reloadBrief: () => Promise<unknown>;
  /** Reload the memory when the read itself failed (what is stored is still shown). */
  reloadMemory: () => Promise<unknown>;
  now?: () => string;
}

/** M83 A2 / A3: one press = one read, then every store the tab's cards render from is reloaded - never a page reload. */
export async function readEverything(steps: ReadNowSteps): Promise<{ memory: MarketMemory | null; outcome: ReadOutcome }> {
  const at = (steps.now ?? (() => new Date().toISOString()))();
  let memory: MarketMemory | null = null;
  let outcome: ReadOutcome;
  try {
    memory = await steps.refresh();
    const r = memory.report;
    outcome = { at, symbols: r?.symbols ?? 0, cues: r?.cues ?? 0, globals: r?.globals ?? 0, reasons: plainReasons(r?.errors), error: null };
  } catch (e) {
    outcome = { at, symbols: 0, cues: 0, globals: 0, reasons: [], error: e };
    await steps.reloadMemory();
  }
  await steps.reloadBrief();
  return { memory, outcome };
}

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
  const [actionsLoading, setActionsLoading] = useState(true);
  const [newsLoading, setNewsLoading] = useState(true);

  const [memory, setMemory] = useState<MarketMemory | null>(null);
  const [memoryError, setMemoryError] = useState<unknown>(null);
  const [reading, setReading] = useState(false);
  const [readReport, setReadReport] = useState<ReadOutcome | null>(null);
  const [readVersion, setReadVersion] = useState(0);
  const memoryAsked = useRef(false);

  const loadBrief = useCallback(() => {
    setBriefLoading(true);
    return api.aiBrief("en").then((b) => { setBrief(b); setBriefError(null); }).catch(setBriefError).finally(() => setBriefLoading(false));
  }, []);
  const reloadBrief = useCallback(() => { void loadBrief(); }, [loadBrief]);
  const loadMemory = useCallback(() => {
    memoryAsked.current = true;
    return api.aiMarketMemory().then((m) => { setMemory(m); setMemoryError(null); }).catch(setMemoryError);
  }, []);
  const readNow = useCallback(async () => {
    setReading(true);
    try {
      const { memory: m, outcome } = await readEverything({
        refresh: () => api.aiMarketMemoryRefresh(), reloadBrief: loadBrief,
        reloadMemory: () => (memoryAsked.current ? loadMemory() : Promise.resolve()),
      });
      if (m) { setMemory(m); setMemoryError(null); }
      setReadReport(outcome);
    } finally {
      setReadVersion((v) => v + 1);         // every card with its own data (the thesis) reloads too
      setReading(false);
    }
  }, [loadBrief, loadMemory]);
  const reloadActions = useCallback(() => {
    setActionsLoading(true);
    api.aiActions("PROPOSED").then(setPending).catch(() => setPending([])).finally(() => setActionsLoading(false));
  }, []);
  const markNewsSeen = useCallback((items?: NewsFeedItem[]) => {
    const latest = Math.max(Date.now(), ...(items ?? []).map((i) => new Date(i.published_at).getTime()));
    try { localStorage.setItem(NEWS_SEEN_KEY, String(latest)); } catch { /* intentional: storage unavailable */ }
    setNewHighNews(0);
  }, []);

  useEffect(() => {
    reloadBrief();
    reloadActions();
    // The feed may be switched off for this organisation - then there is simply no badge.
    api.newsFeedItems(24, HIGH_SEVERITY).then((items) => setNewHighNews(countNewHigh(items, lastNewsSeen()))).catch(() => setNewHighNews(0))
      .finally(() => setNewsLoading(false));
  }, [reloadBrief, reloadActions]);

  const badgesLoading = actionsLoading || newsLoading;
  const value = useMemo(() => ({ brief, briefError, briefLoading, reloadBrief, pending, reloadActions, newHighNews, markNewsSeen, badgesLoading,
                                 memory, memoryError, loadMemory, readNow, reading, readReport, readVersion }),
    [brief, briefError, briefLoading, reloadBrief, pending, reloadActions, newHighNews, markNewsSeen, badgesLoading,
     memory, memoryError, loadMemory, readNow, reading, readReport, readVersion]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCopilotData(): CopilotData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCopilotData outside CopilotDataProvider");
  return v;
}
