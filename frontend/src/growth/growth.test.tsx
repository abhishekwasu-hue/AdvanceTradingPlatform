import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ role: "SUPER_ADMIN" as string | null }));
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: auth.role ? { id: 1, email: "ops@example.com", tenant_id: 1, role: auth.role, scopes: [] } : null, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import AdminConsoleV2 from "../admin/AdminConsoleV2";
import adminConsoleSource from "../admin/AdminConsoleV2.tsx?raw";
import accountSource from "../pages/AccountPage.tsx?raw";
import journalSource from "../pages/JournalPage.tsx?raw";
import { FeaturesProvider } from "../ui/useFeatures";
import { growthApi } from "./api";
import { DisciplineCardBodyV2 } from "./DisciplineCardV2";
import cardSource from "./DisciplineCardV2.tsx?raw";
import { DEMO_ACTIVATION, DEMO_CARD, DEMO_PLAN } from "./growth.fixture";
import { cardProblem, cardSvg, cleanCard, meterFill, resolveTokens, shareText, trialLine, wrap } from "./model";
import { PlanUsageBodyV2 } from "./PlanUsageV2";
import planSource from "./PlanUsageV2.tsx?raw";

/** MO4 slice 3: the plan page's usage meter, the discipline share card and the admin activation panel. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const CALLS = /\b(buy|buying|sell|selling|target\w*|stop[- ]?loss|stops?|SL)\b/i;
const MONEY = /₹|\bRs\b|\bINR\b|\bprofit|\bloss(es)?\b|P&L|\bprice\b/i;

afterEach(() => { vi.restoreAllMocks(); auth.role = "SUPER_ADMIN"; });

describe("the reads", () => {
  it("reads GET /api/growth/plan, /api/growth/share/discipline-week and /api/admin/console/activation - reads only", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await growthApi.plan();
    await growthApi.disciplineWeek();
    await growthApi.activation(30);
    expect(String(spy.mock.calls[0][0])).toMatch(/\/growth\/plan$/);
    expect(String(spy.mock.calls[1][0])).toMatch(/\/growth\/share\/discipline-week$/);
    expect(String(spy.mock.calls[2][0])).toMatch(/\/admin\/console\/activation\?days=30$/);
    for (const [, init] of spy.mock.calls) expect(init?.method ?? "GET").toBe("GET");
  });
});

describe("the usage meter", () => {
  const html = renderToStaticMarkup(<MemoryRouter><PlanUsageBodyV2 data={DEMO_PLAN} /></MemoryRouter>);

  it("shows every meter with its count against the plan, a bar only where there is a limit", () => {
    const t = text(html);
    for (const m of DEMO_PLAN.meters) {
      expect(t).toContain(m.label);
      expect(t).toContain(m.text);
    }
    expect(html.match(/role="meter"/g)).toHaveLength(4);                  // watchlists, analyses, backtests, paper bots
    expect(meterFill(DEMO_PLAN.meters[0])).toBe(40);
    expect(meterFill(DEMO_PLAN.meters[1])).toBeNull();
    expect(meterFill({ ...DEMO_PLAN.meters[0], used: 9 })).toBe(100);
  });

  it("says plainly when one is used up, and what the plan includes", () => {
    const t = text(html);
    expect(html).toContain('data-meter="paper_bots" data-state="full"');
    expect(t).toContain("You have used all of this on your plan.");
    for (const row of DEMO_PLAN.includes) expect(t).toContain(row.text);
    expect(t).toContain("Your Pro trial: 4 days left.");
  });

  it("shows no price, no upgrade pop-up and no advice", () => {
    const t = text(html);
    expect(t).not.toMatch(MONEY);
    expect(t).not.toMatch(/upgrade now|limited time|hurry|last chance/i);
    expect(t).not.toMatch(CALLS);
  });

  it("the trial line", () => {
    expect(trialLine(null)).toBeNull();
    expect(trialLine({ plan_name: "Pro", days_left: 1 })).toBe("Your Pro trial: 1 day left.");
    expect(trialLine({ plan_name: "Pro", days_left: 0 })).toBe("Your Pro trial ends today.");
  });
});

describe("the share card", () => {
  it("the guard refuses prices, money, currency and call words", () => {
    expect(cardProblem("I stayed inside my own daily limits on 2 of 3 market days.")).toBe(false);
    expect(cardProblem("Week of 15 Mar")).toBe(false);
    for (const bad of ["Entry near 2,456.75", "Area 23000", "A profit this week", "Booked P&L", "₹5 kept", "Sell the rise",
      "My target came", "The stop-loss held", "I lost twice", "Rs 400 made"]) expect(cardProblem(bad), bad).toBe(true);
  });

  it("a fact or line that fails is left out of the card, its image and its text", () => {
    const dirty = { ...DEMO_CARD, facts: [...DEMO_CARD.facts, { key: "x", label: "Best trade", value: "₹2,450.50" }],
      lines: [...DEMO_CARD.lines, "Bought at 2456.75 and sold at the target."] };
    const clean = cleanCard(dirty);
    expect(clean.facts.map((f) => f.key)).toEqual(DEMO_CARD.facts.map((f) => f.key));
    expect(clean.lines).toEqual(DEMO_CARD.lines);
    for (const out of [cardSvg(dirty), shareText(dirty)]) {
      expect(out).not.toContain("2,450");
      expect(out).not.toContain("2456");
      expect(out).not.toMatch(/\bBought\b|\btarget\b/);
    }
  });

  it("the image holds the title, the week, the facts and the link, in theme tokens only", () => {
    const svg = cardSvg(DEMO_CARD);
    expect(svg).toContain("My discipline this week");
    expect(svg).toContain("Week of 15 Mar");
    const words = text(svg);
    for (const f of DEMO_CARD.facts) { expect(words).toContain(f.value); expect(words).toContain(f.label); }
    expect(DEMO_CARD.lines.every((l) => words.includes(l))).toBe(true);        // all three lines fit, the no-trade line too
    expect(svg).toContain("aialgodesk.tech");
    expect(svg).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(svg.match(/fill="([^"]+)"/g)?.every((m) => m.includes("rgb(var(--"))).toBe(true);
    const resolved = resolveTokens(svg, (t) => (t === "brand" ? "37 99 235" : ""));
    expect(resolved).toContain('fill="rgb(37 99 235)"');
    expect(resolved).toContain("rgb(var(--surface-1))");                 // an unknown token stays as it was
  });

  it("never praises trading more", () => {
    const all = [cardSvg(DEMO_CARD), shareText(DEMO_CARD), cardSource].join("\n");
    expect(all).not.toMatch(/\b(?:more trades|trade more|most active|keep trading|busy week|great week)\b/i);
    expect(shareText(DEMO_CARD)).toContain("A day without a trade is a good day when nothing met my plan.");
  });

  it("long lines wrap on spaces", () => {
    expect(wrap("one two three four", 9)).toEqual(["one two", "three", "four"]);
  });

  it("the card body shows the preview, the hints and the actions - nothing is sent by itself", () => {
    const html = renderToStaticMarkup(<MemoryRouter><DisciplineCardBodyV2 card={{ ...DEMO_CARD, hints: ["Save your own daily limits."] }} /></MemoryRouter>);
    expect(html).toContain("data-share-card");
    expect(html).toContain("<svg");
    expect(text(html)).toContain("Save your own daily limits.");
    expect(html).toContain("data-download");
    expect(html).toContain("data-copy");
    expect(text(html)).not.toMatch(MONEY);
    expect(cardSource).not.toMatch(/fetch\(|request\(|growthApi\.(?!disciplineWeek)/);   // the only read is his own week
  });
});

describe("where it is mounted, and flag off = nothing", () => {
  it("the plan card sits above the billing card only with ui_v2 + growth_v2", () => {
    expect(accountSource).toMatch(/const growth = v2 && features\.on\("growth_v2"\)/);
    expect(accountSource).toContain("{growth && <PlanUsageV2 />}<BillingCard />");
  });

  it("the share card sits on the Journal page only with ui_v2 + growth_v2", () => {
    expect(journalSource).toMatch(/const shareCard = features\.on\("ui_v2"\) && features\.on\("growth_v2"\)/);
    expect(journalSource).toContain("{shareCard && <DisciplineCardV2 />}");
  });

  it("the admin console shows Activation in Users & business only with growth_v2", () => {
    expect(adminConsoleSource).toContain("{growth && <ActivationPanel />}");
    const render = (flags: Record<string, boolean>) => renderToStaticMarkup(
      <FeaturesProvider userKey={1} value={flags}><MemoryRouter initialEntries={["/admin?section=users"]}><AdminConsoleV2 /></MemoryRouter></FeaturesProvider>);
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    expect(render({ ui_v2: true })).not.toContain('data-console-card="activation"');
    expect(render({ ui_v2: true, growth_v2: true })).toContain('data-console-card="activation"');
    auth.role = "OWNER";
    expect(render({ ui_v2: true, growth_v2: true })).not.toContain("Activation");
  });

  it("the components use theme tokens only", () => {
    for (const src of [planSource, cardSource]) expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|text-white|bg-black/);
    expect(DEMO_ACTIVATION.funnel.map((f) => f.key)).toEqual(["signed_up", "broker", "paper_bot", "journal", "activated"]);
  });
});
