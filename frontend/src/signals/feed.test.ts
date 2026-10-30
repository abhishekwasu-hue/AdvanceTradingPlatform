import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiAction, ProposalRiskCheck } from "../types";
import { feedItem } from "./signals.fixture";
import { signalsLinkFor, watchtowerSplit } from "./watchtower";
import {
  canDecide, canTradeRole, countLabel, decisionFailure, decisionMessage, defaultTab, effectiveStatus, feedQuery, fetchFeed, itemForProposal,
  levelDelta, missingProposalMessage, riskSentences, sizeAndLoss, sourceCaption, statusLabel, timeLeftLabel, type SignalFeed,
} from "./feed";
import { readPages } from "./useSignalFeed";

const NOW = new Date("2026-10-08T05:00:00Z");                 // 10:30 IST
const riskCheck = (extra: Partial<ProposalRiskCheck["sizing"]> = {}, more: Partial<ProposalRiskCheck> = {}): ProposalRiskCheck => ({
  sizing: { capital_basis: 100000, risk_per_trade_pct: 1, risk_per_trade_amount: 1000, user_cap: null, org_cap: 2000, binding: "user", size_multiplier: 1,
    cap: 6000, override_cap: 6000, lot_size: 75, risk_per_unit: 70, suggested_quantity: 75, suggested_lots: 1, quantity: 75, lots: 1, risk_amount: 5250,
    risk_pct_of_capital: 5.25, gauge: 0.8, within_cap: true, override: false, gauge_zone: "ok", ...extra },
  costs: { total: 64.5, net_reward_risk: 1.9 } as ProposalRiskCheck["costs"], refusals: [], warnings: [], errors: [], reward_risk: 2, min_reward_risk: 1.2, ...more,
});

afterEach(() => { vi.restoreAllMocks(); });

describe("UI2-3 feed request", () => {
  it("asks for one tab with only the filters that are set", () => {
    expect(feedQuery({ tab: "waiting" })).toBe("tab=waiting");
    expect(feedQuery({ tab: "history", source: "scan", symbol: " nifty 50 ", cursor: "abc", limit: 20 }))
      .toBe("tab=history&source=scan&symbol=NIFTY+50&cursor=abc&limit=20");
    expect(feedQuery({ tab: "today", source: "all", symbol: "" })).toBe("tab=today");
  });

  it("reads GET /api/signals/feed - a read, never a write", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200 }));
    await fetchFeed({ tab: "today", source: "copilot" });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/signals\/feed\?tab=today&source=copilot$/);
    expect(init?.method ?? "GET").toBe("GET");
  });
});

describe("UI2-3 statuses and labels", () => {
  it("phone opens on Waiting for you, desktop on Today", () => {
    expect(defaultTab(false)).toBe("waiting");
    expect(defaultTab(true)).toBe("today");
  });

  it("a waiting signal shows the minutes left; once its expiry passes on screen it is expired and cannot be decided", () => {
    const item = feedItem();
    expect(statusLabel(item, NOW)).toBe("Waiting · 8 min");
    expect(canDecide(item, NOW)).toBe(true);
    const later = new Date("2026-10-08T05:09:00Z");
    expect(effectiveStatus(item, later)).toBe("expired");
    expect(statusLabel(item, later)).toBe("Expired");
    expect(canDecide(item, later)).toBe(false);
  });

  it("only a waiting entry proposal can be decided", () => {
    for (const status of ["taken", "skipped", "expired", "not_placed", "info", "snoozed"] as const) {
      expect(canDecide(feedItem({ status }), NOW), status).toBe(false);
    }
    expect(canDecide(feedItem({ action_id: null }), NOW)).toBe(false);
  });

  it("words for every status, and time left in hours", () => {
    expect(statusLabel(feedItem({ status: "taken" }), NOW)).toBe("Taken");
    expect(statusLabel(feedItem({ status: "skipped" }), NOW)).toBe("Skipped");
    expect(statusLabel(feedItem({ status: "not_placed" }), NOW)).toBe("Not placed");
    expect(statusLabel(feedItem({ status: "info" }), NOW)).toBe("For info");
    expect(statusLabel(feedItem({ status: "snoozed" }), NOW)).toBe("Snoozed");
    expect(timeLeftLabel("2026-10-08T06:05:00Z", NOW)).toBe("1 h 5 min");
    expect(timeLeftLabel("2026-10-08T06:00:00Z", NOW)).toBe("1 h");
    expect(timeLeftLabel("2026-10-08T05:00:30Z", NOW)).toBe("<1 min");
    expect(timeLeftLabel(null, NOW)).toBeNull();
  });

  it("the caption names the source kind and its name once", () => {
    expect(sourceCaption(feedItem())).toBe("Trading bot");                       // UX3: "Deployment #4" is never shown
    expect(sourceCaption(feedItem({ source: { kind: "autopilot", name: "deployment 12" } }))).toBe("Trading bot");
    expect(sourceCaption(feedItem({ source: { kind: "autopilot", name: "Morning ORB" } }))).toBe("Trading bot · Morning ORB");
    expect(sourceCaption(feedItem({ source: { kind: "scan", name: "Gap scan" } }))).toBe("Scan · Gap scan");
    expect(sourceCaption(feedItem({ source: { kind: "library", name: "Signal generator" } }))).toBe("Signal generator");
    expect(sourceCaption(feedItem({ source: { kind: "scan", name: "Scan setup" } }))).toBe("Scan");
  });

  it("level distances from the entry", () => {
    expect(levelDelta(98, 100)).toBe(-2);
    expect(levelDelta(104.35, 100)).toBe(4.35);
    expect(levelDelta(null, 100)).toBeNull();
  });
});

describe("UI2-3 risk sentences from the existing pre-check", () => {
  it("within the cap: plain sentences, no warning", () => {
    const lines = riskSentences({ risk_check: riskCheck(), portfolio_impact: null });
    expect(lines[0]).toEqual({ text: "Most you can lose (₹5,250) is within your ₹6,000 per-trade limit.", warn: false });
    expect(lines[1].text).toBe("Costs about ₹65; reward to risk after costs is 1.90.");
    expect(lines.every((l) => !l.warn)).toBe(true);
    expect(sizeAndLoss({ risk_check: riskCheck() })).toEqual({ quantity: 75, lotSize: 75, maxLoss: 5250 });
  });

  it("over the cap or refused: a warning line each", () => {
    const lines = riskSentences({ risk_check: riskCheck({ within_cap: false }, { refusals: ["Daily loss limit reached"] }), portfolio_impact: null });
    expect(lines[0].warn).toBe(true);
    expect(lines).toContainEqual({ text: "Daily loss limit reached", warn: true });
  });

  it("no pre-check (its flag off): nothing invented", () => {
    expect(riskSentences(undefined)).toEqual([]);
    expect(riskSentences({ risk_check: null, portfolio_impact: null })).toEqual([]);
    expect(sizeAndLoss(undefined)).toBeNull();
  });
});

describe("UI2-3 links and messages", () => {
  it("?proposal=<id> picks the row of that proposal only", () => {
    const items = [feedItem({ id: "history:3", action_id: null }), feedItem({ id: "action:11", action_id: 11 })];
    expect(itemForProposal(items, "11")?.id).toBe("action:11");
    expect(itemForProposal(items, "12")).toBeNull();
    expect(itemForProposal(items, "x")).toBeNull();
    expect(itemForProposal(items, null)).toBeNull();
  });

  it("says what happened after a decision", () => {
    const item = feedItem();
    expect(decisionMessage("approve", item, { status: "EXECUTED", result: "PAPER order #1 placed", snoozed_until: null }))
      .toBe("Approved. A paper trade for DEMO 50 was placed at the price then.");
    expect(decisionMessage("approve", item, { status: "EXPIRED", result: "The price moved 1.2% from the proposal", snoozed_until: null }))
      .toBe("Not placed for DEMO 50: The price moved 1.2% from the proposal. Nothing was bought or sold.");
    expect(decisionMessage("skip", item)).toBe("Skipped DEMO 50. Nothing was placed.");
    expect(decisionMessage("snooze", item)).toMatch(/^Snoozed DEMO 50/);
  });

  it("a LIVE approval is told in the server's words, never as a paper trade", () => {
    expect(decisionMessage("approve", feedItem({ mode: "LIVE" }), { status: "EXECUTED", result: "Order #9 placed", snoozed_until: null }))
      .toBe("Approved DEMO 50. Order #9 placed");
  });

  it("a failed decision is a plain sentence: stale on 409 / 404, the role on 403, never raw error text", () => {
    expect(decisionFailure(409)).toEqual({ text: "This signal was already decided or has expired. Nothing was placed.", stale: true });
    expect(decisionFailure(404).stale).toBe(true);
    expect(decisionFailure(403, "Requires one of: OWNER, USER")).toEqual({
      text: "You can't decide this signal: your role can view signals but not trade, or it is another member's. Nothing was placed.", stale: false });
    expect(decisionFailure(422, "Note is too long.").text).toBe("Not done: Note is too long. Nothing was placed.");
    expect(decisionFailure(null).text).toBe("That didn't go through. Nothing was placed. Try again in a moment.");
  });

  it("only the trading roles may decide", () => {
    for (const role of ["OWNER", "USER", "STRATEGY_CREATOR", "SUPER_ADMIN"]) expect(canTradeRole(role), role).toBe(true);   // SUPER_ADMIN passes require_role
    for (const role of ["VIEWER", "SUPPORT", undefined]) expect(canTradeRole(role), String(role)).toBe(false);
  });

  it("a ?proposal= link that is not in the list says so", () => {
    expect(missingProposalMessage("902", false)).toBe("Signal #902 is not waiting for you any more: it was decided, snoozed or has expired. Find it under Today or History.");
    expect(missingProposalMessage("902", true)).toBe("Signal #902 is another member's, so it is not in your Signals. It stays on the Watchtower.");
  });

  it("a count the server stopped at its read limit shows as 2000+", () => {
    expect(countLabel({ waiting: 3, today: 9 }, "waiting")).toBe("3");
    expect(countLabel({ waiting: 2000, today: 2000, capped: { waiting: true, today: false } }, "waiting")).toBe("2000+");
    expect(countLabel({ waiting: 2000, today: 2000, capped: { waiting: true, today: false } }, "today")).toBe("2000");
    expect(countLabel({ waiting: 1, today: 1 }, "history")).toBeUndefined();
    expect(countLabel(null, "today")).toBeUndefined();
  });

  it("M7: with ui_v2 on the Watchtower keeps the other proposals and other members' entries; off, nothing moves", () => {
    const me = 7;
    const later = "2026-10-08T05:10:00Z";
    const rows = [
      { id: 1, action: "ENTRY", owner_user_id: me, expires_at: later },
      { id: 2, action: "PAUSE_DEPLOYMENT", owner_user_id: null, expires_at: later },
      { id: 3, action: "EXIT_POSITION", owner_user_id: me, expires_at: later },
      { id: 4, action: "ENTRY", owner_user_id: 8, expires_at: later },          // another member's: the OWNER decides it here
      { id: 5, action: "ENTRY", owner_user_id: null, expires_at: later },       // organisation-wide: in Signals
      { id: 6, action: "ENTRY", owner_user_id: me, expires_at: "2026-10-08T04:59:00Z" },   // overdue: not counted
    ] as (Pick<AiAction, "id" | "action" | "expires_at"> & { owner_user_id: number | null })[];
    expect(watchtowerSplit(rows, false, me, NOW)).toEqual({ entries: [], others: rows });
    const on = watchtowerSplit(rows, true, me, NOW);
    expect(on.entries.map((a) => a.id)).toEqual([1, 5]);              // agrees with the Signals "Waiting" count
    expect(on.others.map((a) => a.id)).toEqual([2, 3, 4]);           // exits and another member's entry stay
    expect(signalsLinkFor(on.entries)).toBe("/signals?proposal=1");
    expect(signalsLinkFor([])).toBe("/signals");
  });
});

describe("UI2-3 paging", () => {
  const page = (ids: string[], next: string | null): SignalFeed => ({
    tab: "history", source: "all", symbol: null, items: ids.map((id) => feedItem({ id })), next_cursor: next, counts: null,
    as_of: "2026-10-08T05:00:00Z", since: null, sources: {},
  });

  it("reads past empty pages the server stopped early, and back to as many rows as were shown", async () => {
    const pages: Record<string, SignalFeed> = { first: page([], "a"), a: page(["x:1", "x:2"], "b"), b: page(["x:3"], "c"), c: page(["x:4"], null) };
    const fetch = vi.fn(async (p: { cursor?: string | null }) => pages[p.cursor ?? "first"]);
    const once = await readPages({ tab: "history" }, 0, fetch);
    expect(once.items.map((i) => i.id)).toEqual(["x:1", "x:2"]);    // the empty first page does not end the list
    expect(once.cursor).toBe("b");
    const refresh = await readPages({ tab: "history" }, 3, fetch);
    expect(refresh.items.map((i) => i.id)).toEqual(["x:1", "x:2", "x:3"]);   // "Show more" pages come back
    expect(refresh.cursor).toBe("c");
  });

  it("stops after ten pages whatever the server says", async () => {
    const fetch = vi.fn(async () => page([], "again"));
    const out = await readPages({ tab: "history" }, 5, fetch);
    expect(fetch).toHaveBeenCalledTimes(10);
    expect(out.items).toEqual([]);
    expect(out.cursor).toBe("again");
  });
});
