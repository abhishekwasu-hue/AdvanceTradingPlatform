import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { open, placeAndMeasure, ticketToReview, type Flow, type Measured } from "./ticketFlows";

/**
 * M76 MP-0 (docs/design/M76_REVIEW.md section 8, "Metrics"): the BASELINE of today's tickets, before any check card
 * exists. Four ticket flows on the OC1 hand-made chain (e2e/fixtures/option_chain_v2.json), every /api answer made up
 * here (round numbers, not market data):
 *   A. the chain ticket, nothing to warn about;           B. the chain ticket with today's warning texts;
 *   C. the chain ticket refused (5 lots), Back, 4 lots, Place;   D. the basket (a bull put spread from the chain).
 * At the moment Place is pressed the page counts the warning surfaces the user sees (the same selectors the review
 * names: `[data-ticket=warnings] li`, `[data-ticket=refusals] li`, `[data-risk-part=warn]`, the "cannot place" lines,
 * dialogs) and the time from opening the ticket to Place (mocked API, so this is the UI's own cost, not a person's).
 * The ticket's own report (POST /api/ui/ticket-metrics) must carry the same counts - the client and this spec measure
 * the same thing. No screenshot baseline here: `TICKET_METRICS_OUT` (a JSON file) and `TICKET_SHOTS_DIR` (the review
 * step, 390 + 1440, light + dark) are written only when set, for the milestone's proof page.
 */
const RESULTS: Measured[] = [];
const EXPECTED: Record<Flow, { cards: number; lines: number }> = {
  A: { cards: 0, lines: 0 },   // nothing to warn about: no warning surface
  B: { cards: 2, lines: 3 },   // "Before you place" (the event) + the warnings list (two texts)
  C: { cards: 0, lines: 0 },   // at Place (4 lots) nothing is left; the refusal was on the earlier Review
  D: { cards: 0, lines: 0 },   // the basket with every leg clear
};

function check(m: Measured): void {
  expect({ cards: m.cards, lines: m.lines }).toEqual(EXPECTED[m.flow]);
  // The ticket's own report counts the same surfaces (MP-0 client metrics; absent before the client existed).
  if (process.env.TICKET_METRICS_BEFORE !== "1") {
    expect(m.reported).toMatchObject({ cards_shown: m.cards, lines_total: m.lines, lines_visible: m.visible, outcome: "placed", pending_at_place: 0 });
    expect(typeof m.reported?.ms_open_to_place).toBe("number");
  }
  RESULTS.push(m);
}

test.describe("M76 MP-0: ticket warning surfaces and time to Place (baseline)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test.describe.configure({ mode: "serial" });

  test("A - the chain ticket with nothing to warn about", async ({ page }) => {
    const seen = await open(page, "light", "A");
    const ticket = await ticketToReview(page);
    check(await placeAndMeasure(page, "A", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("B - the chain ticket with today's warning texts", async ({ page }) => {
    const seen = await open(page, "light", "B");
    const ticket = await ticketToReview(page);
    await expect(ticket.locator("[data-ticket='warnings'] li")).toHaveCount(2);
    await expect(ticket.locator("[data-risk-part='warn']")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();      // a warning never disables Place
    check(await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("C - refused at 5 lots, Back, 4 lots, Place", async ({ page }) => {
    const seen = await open(page, "light", "C");
    const ticket = await ticketToReview(page, 5);
    await expect(ticket.locator("[data-ticket='refusals'] li")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='place']")).toBeDisabled();     // a refusal (his own limit) is a block, as today
    await ticket.locator("[data-ticket='back']").click();
    await ticket.locator("[data-ticket='lots-less']").click();
    await ticket.locator("[data-ticket='review']").click();
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    check(await placeAndMeasure(page, "C", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("D - the basket (bull put spread)", async ({ page }) => {
    const seen = await open(page, "light", "D");
    await page.locator("[data-strip='basket']").click();
    const panel = page.locator("[data-basket-panel]");
    await expect(panel).toBeVisible();
    await panel.locator("[data-strategy='Bull put spread']").click();
    await expect(panel.locator("[data-leg]")).toHaveCount(2);
    if (await panel.locator("[data-basket='product-unset']").count()) await panel.locator("[data-basket='product'] [role='radio']").first().click();
    await panel.locator("[data-basket='execute']").click();
    await expect(panel.locator("[data-basket='confirm-legs']")).toBeVisible();
    check(await placeAndMeasure(page, "D", panel.locator("[data-basket='place']"), panel.locator("[data-basket='result']"), seen));
  });

  test.afterAll(() => {
    const out = process.env.TICKET_METRICS_OUT;
    if (!out || !RESULTS.length) return;
    // Appends across runs (--repeat-each), so the proof page reads medians.
    let flows: Measured[] = [];
    try { flows = (JSON.parse(readFileSync(out, "utf8")) as { flows: Measured[] }).flows; } catch { flows = []; }
    flows = [...flows, ...RESULTS.splice(0)];
    writeFileSync(out, JSON.stringify({ flows, cards_per_ticket: flows.reduce((s, r) => s + r.cards, 0) / flows.length }, null, 2));
  });
});

// The review step with today's warning texts (flow B) at both sizes and themes, for the proof page only.
for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`M76 MP-0 review step at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of ["light", "dark"] as const) {
      test(`the review step with today's warnings (${theme})`, async ({ page }) => {
        const dir = process.env.TICKET_SHOTS_DIR;
        test.skip(!dir, "proof shots only (TICKET_SHOTS_DIR)");
        await open(page, theme, "B");
        const ticket = await ticketToReview(page);
        await expect(ticket.locator("[data-ticket='warnings'] li")).toHaveCount(2);
        mkdirSync(dir as string, { recursive: true });
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `${dir}/review-${width}-${theme}.png`, animations: "disabled" });
      });
    }
  });
}
