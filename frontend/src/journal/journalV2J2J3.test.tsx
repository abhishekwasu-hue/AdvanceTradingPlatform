import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { journalApi } from "./api";
import { CheckinFormV2, mistakeLabel } from "./CheckinV2";
import { istToday } from "./JournalPageV2";
import { DailyCardV2, MonthlyV2, WeeklyExtrasV2, planText, shiftDay, shiftMonth } from "./ReviewsV2";
import type { Checkin, DailyCard, MonthlyReview, WeeklyExtras } from "./types";
import { WaitingCheckinsV2 } from "./WaitingCheckinsV2";

/**
 * P2-8 J2 + J3 (`ui_v2` + `discipline_engine`): the check-in form (optional fields, the after-half only once closed,
 * the mistake only when the plan was not followed), the waiting prompt card, the daily / weekly / monthly reviews (the
 * first-20 note, counts not shares on small samples, empty states) and the API paths. Made-up data; no DOM.
 */

vi.mock("../utils/useMediaQuery", async (orig) => ({ ...(await orig<typeof import("../utils/useMediaQuery")>()), useMediaQuery: () => false }));

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const ADVICE = /\b(buy|sell|should|target)\b/i;
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const CHECKIN: Checkin = {
  feeling_before: "calm", confidence: 4, feeling_after: null, plan_followed: null, mistake: null, lesson: null, setup_name: null,
  setup_name_suggested: "Buyer area", notes: [{ at: "2032-03-17T05:00:00Z", text: "unease after entry" }],
  feelings: ["calm", "confident", "unsure", "anxious", "excited", "fearful", "FOMO"], mistakes: ["early_exit", "other", "chased"],
  plan_answers: ["YES", "PARTLY", "NO"], answered: true,
};
const form = (data: Checkin, closed: boolean) => html(<CheckinFormV2 data={data} closed={closed} busy={false} onSave={() => undefined} onNote={() => undefined} />);

describe("P2-8 J2 check-in", () => {
  it("shows the before-half and notes while open, with his picks pressed", () => {
    const out = form(CHECKIN, false);
    const t = text(out);
    expect(t).toContain("Before: how did you feel?");
    expect(t).toContain("FOMO");
    expect(t).toContain("unease after entry");
    expect(out).toMatch(/aria-pressed="true"[^>]*>calm</);
    expect(out).toMatch(/aria-pressed="true"[^>]*>4</);
    expect(t).not.toContain("Did I follow my plan?");
    expect(t).toContain("Only you see this. Skip any field.");
    expect(out).toContain('value="Buyer area"');                                  // the setup name suggested from his strategy
  });

  it("asks the after-half once closed, and the mistake only when the plan was not followed", () => {
    expect(text(form(CHECKIN, true))).toContain("Did I follow my plan?");
    expect(form(CHECKIN, true)).not.toContain('data-checkin-field="mistake"');
    expect(form({ ...CHECKIN, plan_followed: "YES" }, true)).not.toContain('data-checkin-field="mistake"');
    expect(form({ ...CHECKIN, plan_followed: "PARTLY" }, true)).toContain('data-checkin-field="mistake"');
    expect(mistakeLabel("other")).toBe("Other");
  });

  it("never asks for a score of his state, and has no advice words", () => {
    const t = text(form({ ...CHECKIN, plan_followed: "NO" }, true));
    expect(t).not.toMatch(/mood score|emotional state|stress level/i);
    expect(t).not.toMatch(ADVICE);
  });

  it("lists entries waiting for the check-in, and nothing when none wait", () => {
    expect(html(<WaitingCheckinsV2 items={[]} onOpen={() => undefined} onDismiss={() => undefined} />)).toBe("");
    const t = text(html(<WaitingCheckinsV2 items={[{ kind: "trade", id: 2, symbol: "DEMOB", moment: "close", mode: "PAPER", at: "2032-03-17T05:00:00Z" }]}
      onOpen={() => undefined} onDismiss={() => undefined} />));
    expect(t).toContain("DEMOB");
    expect(t).toContain("Closed");
    expect(t).toContain("Check in");
    expect(t).toContain("Not now");
  });

  it("calls PUT /checkin with only the fields he changed", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(CHECKIN), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await journalApi.saveCheckin("trade", 2, { plan_followed: "PARTLY" });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/journal/trades/2/checkin");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ plan_followed: "PARTLY" });
  });
});

const GATE = { ready: false, closed_total: 7, needed: 20, note: "Not enough trades yet (7 of 20)." };
const PLAN = { yes: 2, partly: 1, no: 0, answered: 3, unanswered: 1, rate: null };

describe("P2-8 J3 reviews", () => {
  it("daily card: the first-20 note, tiles, the fact done well, and an empty day", () => {
    const d: DailyCard = { day: "2032-03-17", trades: 3, closed: 2, net_pnl: 80, plan: PLAN, feelings: [{ feeling: "calm", count: 2 }],
      confirmed_tags: [{ tag: "early_exit", label: "Early exit", count: 1 }], done_well: "Stop kept on all 2 trades.", lines: [], gate: GATE,
      entries: [{ kind: "trade", id: 1, symbol: "DEMOA", pnl: 120 }] };
    const t = text(html(<DailyCardV2 state={{ status: "ok", data: d }} day="2032-03-17" onStep={() => undefined} onRetry={() => undefined} onOpen={() => undefined} />));
    expect(t).toContain("Not enough trades yet (7 of 20).");
    expect(t).toContain("2 of 3");
    expect(t).not.toContain("%");                                                    // no share below the setting
    expect(t).toContain("Done well: Stop kept on all 2 trades.");
    const empty = text(html(<DailyCardV2 state={{ status: "ok", data: { ...d, trades: 0, entries: [] } }} day="2032-03-17" onStep={() => undefined}
      onRetry={() => undefined} onOpen={() => undefined} />));
    expect(empty).toContain("No trades on this day.");
  });

  it("weekly extras: small samples labelled, a share only when given, the reflection question", () => {
    const w: WeeklyExtras = { week: "2032-W11", gate: { ...GATE, ready: true, note: undefined }, plan: { ...PLAN, rate: 66.7 },
      feelings: [{ value: "calm", trades: 3, wins: 2, losses: 1, net_pnl: 140, avg_pnl: 46.67, win_rate: null, small: true },
        { value: "anxious", trades: 25, wins: 10, losses: 15, net_pnl: -300, avg_pnl: -12, win_rate: 40, small: false }],
      feelings_after: [], confidence: [], mistake: null, lines: ["Plan followed: 2 of 3 answered (66.7%)."],
      notable: [{ kind: "trade", id: 1, symbol: "DEMOA", pnl: 120, chart: null }], done_well: null,
      question: "What is the one thing to repeat next week?", answer: "keep the stop" };
    const out = html(<WeeklyExtrasV2 state={{ status: "ok", data: w }} onRetry={() => undefined} onOpen={() => undefined} onAnswer={async () => undefined} />);
    const t = text(out);
    expect(t).toContain("small sample");
    expect(t).toContain("10 (40%)");
    expect(t).not.toContain("2 (");                                                   // the small group shows a count only
    expect(t).toContain("What is the one thing to repeat next week?");
    expect(out).toContain('value="keep the stop"');
    const empty = text(html(<WeeklyExtrasV2 state={{ status: "ok", data: { ...w, lines: [], feelings: [], notable: [] } }} onRetry={() => undefined}
      onOpen={() => undefined} onAnswer={async () => undefined} />));
    expect(empty).toContain("Check in on a trade to see this part.");
  });

  it("monthly: week by week, by setup, and a Print button", () => {
    const m: MonthlyReview = { month: "2032-03", gate: GATE, trades: 3, closed: 3, net_pnl: 250, plan: PLAN,
      weeks: [{ week: "2032-W10", score: 80, plan: PLAN, trades: 3 }], setups: [{ setup: "Buyer area", trades: 2, net_pnl: 350, win_rate: null, small: true }],
      best_setup: null, worst_setup: null, lines: ["3 trades, 3 closed; result +₹250."], done_well: null, question: "Which one habit from this month do you want to keep?", answer: null };
    const out = html(<MonthlyV2 state={{ status: "ok", data: m }} month="2032-03" onStep={() => undefined} onRetry={() => undefined} onAnswer={async () => undefined} />);
    const t = text(out);
    expect(out).toContain("data-month-print");
    expect(t).toContain("80 / 100");
    expect(t).toContain("Buyer area");
    expect(t).not.toMatch(ADVICE);
  });

  it("plain helpers", () => {
    expect(planText(PLAN)).toBe("2 of 3");
    expect(planText({ ...PLAN, rate: 75 })).toBe("2 of 3 (75%)");
    expect(planText({ ...PLAN, answered: 0 })).toBe("Not answered yet");
    expect(shiftDay("2032-03-01", -1)).toBe("2032-02-29");
    expect(shiftMonth("2032-01", -1)).toBe("2031-12");
    expect(shiftMonth("2032-12", 1)).toBe("2033-01");
    expect(istToday(new Date("2032-03-17T20:00:00Z"))).toBe("2032-03-18");          // past IST midnight
  });
});
