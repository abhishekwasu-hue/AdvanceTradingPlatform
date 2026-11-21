import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PAGE_TOURS } from "../tours/content";
import { TOUR_IDS, tourIdFor } from "../tours/pages";
import { journalApi } from "./api";
import { GoalsListV2, PlansV2, goalText } from "./PlansV2";
import { LastGoodV2, ProofV2 } from "./ProofV2";
import { TodayTabV2 } from "./TodayTabV2";
import type { GoalProgress, JournalPlan, JournalToday, ProofPage } from "./types";

/**
 * P2-8 J4 + J5 + J6 (`ui_v2` + `discipline_engine`): My proof (facts with dates, pins, his own entries, empty state),
 * his if-then plans and goals (his words; counts, a share only when given), the Today tab, the Journal tour, and the
 * API paths. Made-up data; no DOM.
 */

vi.mock("../utils/useMediaQuery", async (orig) => ({ ...(await orig<typeof import("../utils/useMediaQuery")>()), useMediaQuery: () => false }));

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const PRAISE = /\b(great|well done|congrat\w*|amazing|proud|excellent|good job|keep it up)\b/i;
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const FACT = { key: "stop_kept:trade:2", kind: "stop_kept", text: "Stop was kept on DEMOB.", at: "2032-03-16T05:10:00Z", entry_kind: "trade" as const, entry_id: 2, chart: null };
const PAGE: ProofPage = {
  items: [{ ...FACT, own: false, pinned: true },
    { key: "own:abc", id: 9, kind: "own", text: "Skipped a setup that was not mine", at: "2032-03-15", entry_kind: null, entry_id: null, chart: null, own: true, pinned: false }],
  last_good: FACT, after_loss: "In your last 20 trades you followed your plan in 15.", days: 90,
};
const IF_THEN: JournalPlan = { id: 1, kind: "IF_THEN", if_text: "a trade stops me out", then_text: "I take a short break before the next entry",
  trigger: "STOP_HIT", metric: null, metric_label: null, target_pct: null, period: null, active: true };
const GOAL_PLAN: JournalPlan = { id: 2, kind: "GOAL", if_text: null, then_text: null, trigger: "NONE", metric: "PLAN_FOLLOWED", metric_label: "Plan followed",
  target_pct: 90, period: "MONTH", active: true };
const GOAL: GoalProgress = { id: 2, period: "MONTH", metric: "PLAN_FOLLOWED", label: "Plan followed", target_pct: 90, done: 7, base: 9, pct: null, reached: false, small: true };
const noop = () => undefined;

describe("P2-8 J4 My proof", () => {
  it("lists facts with dates and a trade link, his own entries removable, pinned ones marked", () => {
    const out = html(<ProofV2 state={{ status: "ok", data: PAGE }} onRetry={noop} onPin={noop} onAdd={async () => true} onRemove={async () => undefined} onOpen={noop} />);
    const t = text(out);
    expect(t).toContain("Stop was kept on DEMOB.");
    expect(t).toContain("Open the trade");
    expect(t).toContain("Your entry");
    expect(out.match(/data-proof-remove/g)?.length).toBe(1);                       // only his own entry
    expect(out).toMatch(/aria-pressed="true"[^>]*data-proof-pin/);
    expect(t).toContain("In your last 20 trades you followed your plan in 15.");
    expect(t).not.toMatch(PRAISE);
  });

  it("empty state, and the last-good card hides when there is nothing", () => {
    const t = text(html(<ProofV2 state={{ status: "ok", data: { ...PAGE, items: [], last_good: null, after_loss: null } }} onRetry={noop} onPin={noop}
      onAdd={async () => true} onRemove={async () => undefined} onOpen={noop} />));
    expect(t).toContain("No facts yet from the last 90 days.");
    expect(html(<LastGoodV2 fact={null} afterLoss={null} />)).toBe("");
  });
});

describe("P2-8 J5 plans and goals", () => {
  it("shows his if-then plans in his words and goals as counts", () => {
    const t = text(html(<PlansV2 state={{ status: "ok", data: { plans: [IF_THEN, GOAL_PLAN], goals: [GOAL] } }} onRetry={noop}
      onAdd={async () => true} onToggle={noop} onRemove={async () => undefined} />));
    expect(t).toContain("If a trade stops me out, then I take a short break before the next entry");
    expect(t).toContain("Also after a stop hit");
    expect(t).toContain("Plan followed: 7 of 9 this month; your goal 90%");
    expect(t).toContain("small sample");
    expect(t).not.toMatch(/\b(buy|sell|should|target)\b/i);
  });

  it("a share appears only when the server gives one; reached is marked", () => {
    expect(goalText({ ...GOAL, pct: 92, base: 25, done: 23, small: false })).toBe("Plan followed: 23 of 25 (92%) this month; your goal 90%");
    expect(text(html(<GoalsListV2 goals={[{ ...GOAL, pct: 92, reached: true, small: false }]} />))).toContain("Reached");
  });

  it("empty states say what to do in plain words", () => {
    const t = text(html(<PlansV2 state={{ status: "ok", data: { plans: [], goals: [] } }} onRetry={noop} onAdd={async () => true} onToggle={noop}
      onRemove={async () => undefined} />));
    expect(t).toContain("No plans yet. Write one in your own words.");
    expect(t).toContain("No goals yet. Set one about how you trade, not about profit.");
  });
});

describe("P2-8 J6 Today tab, tour and API", () => {
  it("Today: the plan a stop hit brings back, his plans, goals and his last good decision", () => {
    const d: JournalToday = { plans: [IF_THEN], reminders: [{ ...IF_THEN, because: "Stop hit on DEMOB", entry_kind: "trade", entry_id: 2 }],
      goals: [GOAL], last_good: FACT, after_loss: null };
    const t = text(html(<TodayTabV2 state={{ status: "ok", data: d }} onRetry={noop} onOpen={noop} />));
    expect(t).toContain("Your plan for this moment");
    expect(t).toContain("Stop hit on DEMOB");
    expect(t).toContain("Before the open");
    expect(t).toContain("Your last good decision");
    const empty = text(html(<TodayTabV2 state={{ status: "ok", data: { plans: [], reminders: [], goals: [], last_good: null, after_loss: null } }}
      onRetry={noop} onOpen={noop} />));
    expect(empty).toContain("Your if-then plans show here.");
  });

  it("the Journal has its own page tour with 3 to 5 steps", () => {
    expect(TOUR_IDS).toContain("journal");
    expect(tourIdFor("journal")).toBe("journal");
    const steps = PAGE_TOURS.journal.steps;
    expect(steps.length).toBeGreaterThanOrEqual(3);
    expect(steps.length).toBeLessThanOrEqual(5);
  });

  it("calls the plan and proof routes", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await journalApi.pinProof("stop_kept:trade:2", true);
    await journalApi.changePlan(1, { active: false });
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toContain("/journal/proof/pin");
    expect(calls[0][1].method).toBe("PUT");
    expect(calls[1][0]).toContain("/journal/plans/1");
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ active: false });
  });
});
