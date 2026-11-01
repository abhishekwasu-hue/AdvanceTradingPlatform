import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { expectPhoneLayout } from "./phoneLayout";
import { open, placeAndMeasure, ticketToReview, type CardOptions, type Flow, type Measured } from "./ticketFlows";

/**
 * M76 MP-2 (flag trade_warnings): the ONE check card on the order ticket and the basket, on the same four flows as the
 * MP-0 baseline (e2e/ticket-metrics-baseline-v2.e2e.ts) - so cards per ticket and time to Place compare one to one - and
 * the card's own behaviour: worst first, three lines + "N more", "Don't show again" (his switch off), "Edit", "ask me"
 * (his own extra tap), a slow check service (Place goes ahead, the card fills after), and never over Place.
 * No screenshot baseline: `TICKET_METRICS_OUT` (JSON) and `TICKET_SHOTS_DIR` (390 + 1440, light + dark) are written
 * only when set, for the milestone's proof page.
 */
const DUPLICATE = { id: "duplicate_order", severity: "mistake" as const, text: "Open: NIFTY 03 NOV 24500 CE buy 1 lot · this order: buy 1 lot" };
const PRE_OPEN = { id: "market_state", severity: "fact" as const, text: "Market: pre-open (09:00-09:15)" };
const FIVE: CardOptions = { findings: [PRE_OPEN, DUPLICATE] };     // + flow B's expiry day, no live price and the event = 5 lines

const RESULTS: Measured[] = [];
// With the card: every warning line is in ONE card (refusals stay blocks; at Place there are none).
const EXPECTED: Record<Flow, { cards: number; lines: number; visible: number }> = {
  A: { cards: 0, lines: 0, visible: 0 },
  B: { cards: 1, lines: 3, visible: 3 },   // expiry day, no live price, the event - one card
  C: { cards: 0, lines: 0, visible: 0 },
  D: { cards: 0, lines: 0, visible: 0 },
};

function check(m: Measured): void {
  expect({ cards: m.cards, lines: m.lines, visible: m.visible }).toEqual(EXPECTED[m.flow]);
  expect(m.reported).toMatchObject({ cards_shown: m.cards, lines_total: m.lines, lines_visible: m.visible, outcome: "placed" });
  RESULTS.push(m);
}

const card = (t: Locator) => t.locator("[data-check-card]");

test.describe("M76 MP-2: the check card on the four ticket flows (after)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test.describe.configure({ mode: "serial" });

  test("A - nothing to warn about: no warning line, Place as before", async ({ page }) => {
    const seen = await open(page, "light", "A", {});
    const ticket = await ticketToReview(page);
    await expect(card(ticket).locator("[data-check-clear]")).toHaveText("No warnings.");
    check(await placeAndMeasure(page, "A", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("B - today's warning texts are ONE card of fact lines; Place stays on", async ({ page }) => {
    const seen = await open(page, "light", "B", {});
    const ticket = await ticketToReview(page);
    await expect(ticket.locator("[data-check-card]")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='warnings']")).toHaveCount(0);            // folded into the card
    await expect(ticket.locator("[data-risk-checks]")).toHaveCount(0);
    await expect(card(ticket).locator("[data-check-line]")).toHaveText([/This contract expires today/, /Before the 3 Nov 2026 expiry/, /No live price/]);
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    check(await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
    // Accept and continue = pressing Place: the types are recorded as accepted today (fire and forget).
    await expect.poll(() => seen.accepts.length).toBe(1);
    expect(seen.accepts[0]).toMatchObject({ underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE" });
    expect((seen.accepts[0].types as string[]).sort()).toEqual(["event", "expiry_day", "price_source"]);
  });

  test("C - a refusal stays a block outside the card (it holds Place back, as today)", async ({ page }) => {
    const seen = await open(page, "light", "C", {});
    const ticket = await ticketToReview(page, 5);
    await expect(ticket.locator("[data-ticket='refusals'] li")).toHaveCount(1);
    await expect(ticket.locator("[data-ticket='place']")).toBeDisabled();
    await ticket.locator("[data-ticket='back']").click();
    await ticket.locator("[data-ticket='lots-less']").click();
    await ticket.locator("[data-ticket='review']").click();
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    check(await placeAndMeasure(page, "C", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen));
  });

  test("D - the basket", async ({ page }) => {
    const seen = await open(page, "light", "D", {});
    await page.locator("[data-strip='basket']").click();
    const panel = page.locator("[data-basket-panel]");
    await panel.locator("[data-strategy='Bull put spread']").click();
    await expect(panel.locator("[data-leg]")).toHaveCount(2);
    if (await panel.locator("[data-basket='product-unset']").count()) await panel.locator("[data-basket='product'] [role='radio']").first().click();
    await panel.locator("[data-basket='execute']").click();
    await expect(panel.locator("[data-check-card='basket-confirm']")).toBeVisible();
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

async function review(page: Page, opts: CardOptions) {
  const seen = await open(page, "light", "B", opts);
  const ticket = await ticketToReview(page);
  return { seen, ticket };
}

test.describe("M76 MP-2: the check card's behaviour", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("worst first, three lines + 'N more'; Don't show again turns his switch off; never over Place", async ({ page }) => {
    const { seen, ticket } = await review(page, FIVE);
    const c = card(ticket);
    await expect(c).toHaveAttribute("data-lines", "5");
    await expect(c.locator("[data-check-line]")).toHaveCount(3);
    await expect(c.locator("[data-check-line]").first()).toHaveAttribute("data-check-line", "duplicate_order");   // the mistake first
    await expect(c.locator("[data-check-more]")).toHaveText("2 more");
    await c.locator("[data-check-more]").click();
    await expect(c.locator("[data-check-line]")).toHaveCount(5);
    await c.locator("[data-check-mute='market_state']").click();
    await expect(c.locator("[data-check-line='market_state']")).toHaveCount(0);
    await expect.poll(() => seen.saves).toEqual([{ "warnings.market_state": false }]);
    // Never over Place: the card ends above the action bar, and the page has no sideways scroll.
    await c.scrollIntoViewIfNeeded();
    const cardBox = await c.boundingBox();
    const placeBox = await ticket.locator("[data-ticket='place']").boundingBox();
    expect(cardBox && placeBox && (cardBox.y + cardBox.height <= placeBox.y || cardBox.y >= placeBox.y + placeBox.height)).toBe(true);
    await expectPhoneLayout(page);
    const m = await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen);
    expect(m.reported).toMatchObject({ cards_shown: 1, lines_total: 4, lines_visible: 4, outcome: "placed" });
    expect(seen.orders).toHaveLength(1);
  });

  test("Edit goes back to the fields; nothing is sent", async ({ page }) => {
    const { seen, ticket } = await review(page, FIVE);
    await card(ticket).locator("[data-check-edit='duplicate_order']").click();
    await expect(ticket.locator("[data-ticket='review']")).toBeVisible();
    expect(seen.orders).toEqual([]);
  });

  test("ask me (his own switch): Place first asks for that line; Accept and continue places it", async ({ page }) => {
    const { seen, ticket } = await review(page, { saved: { "warnings.expiry_day_ask": true } });
    const place = ticket.locator("[data-ticket='place']");
    await place.click();
    await expect(card(ticket).locator("[data-check-line='expiry_day']")).toHaveAttribute("data-asking", "yes");
    await expect(place).toBeEnabled();                                   // never disabled by a warning
    expect(seen.orders).toEqual([]);
    await card(ticket).locator("[data-check-accept='expiry_day']").click();
    await expect(ticket.locator("[data-ticket='result']")).toBeVisible();
    expect(seen.orders).toHaveLength(1);
  });

  test("a switch he turned off: that line does not exist", async ({ page }) => {
    const { ticket } = await review(page, { saved: { "warnings.expiry_day": false, "warnings.event": false } });
    await expect(card(ticket).locator("[data-check-line]")).toHaveText([/No live price/]);
  });

  test("a slow check service: Place goes ahead at once, the card was still checking", async ({ page }) => {
    const { seen, ticket } = await review(page, { findings: [DUPLICATE], delayMs: 3_000 });
    await expect(card(ticket).locator("[data-check-pending]")).toHaveText("Checking…");
    const t0 = Date.now();
    const m = await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen);
    expect(Date.now() - t0).toBeLessThan(2_500);                        // the order did not wait for the checks
    expect(seen.orders).toHaveLength(1);
    expect(m.reported).toMatchObject({ pending_at_place: 1, outcome: "placed" });
  });
});

// The card (five lines, folded to three) at both sizes and themes, for the proof page only.
for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`M76 MP-2 card at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of ["light", "dark"] as const) {
      test(`the review step with the check card (${theme})`, async ({ page }) => {
        const dir = process.env.TICKET_SHOTS_DIR;
        test.skip(!dir, "proof shots only (TICKET_SHOTS_DIR)");
        await open(page, theme, "B", {});
        const ticket = await ticketToReview(page);
        await expect(card(ticket).locator("[data-check-line]")).toHaveCount(3);
        mkdirSync(dir as string, { recursive: true });
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `${dir}/card-${width}-${theme}.png`, animations: "disabled" });
      });
    }
  });
}
