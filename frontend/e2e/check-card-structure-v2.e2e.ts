import { expect, test, type Locator } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { expectPhoneLayout } from "./phoneLayout";
import { open, placeAndMeasure, ticketToReview, type CardOptions, type Measured } from "./ticketFlows";

/**
 * M76 MP-3 (flags trade_warnings + copilot_workspace): the structure lines (W-1) inside the ONE check card of the chain
 * ticket - the made-up check service answers with structure lines (round, made-up numbers; not market data). They sit
 * after the facts (worst first), three lines visible, the rest behind "N more"; still ONE card; Place stays on and
 * nothing extra is asked (information lines). `TICKET_METRICS_OUT` (JSON) and `TICKET_SHOTS_DIR` (390 + 1440, light +
 * dark) are written only when set, for the milestone's proof page.
 */
const COUNTER = { id: "counter_trend", severity: "fact" as const,
  text: "Daily trend: down · last structure event: BOS down at 24380.00 · your setup: bullish · ATP chart readers" };
const AREA = { id: "area_conflict", severity: "fact" as const,
  text: "Last close 24500.00 inside a seller area (daily, 24480.00 - 24560.00) · your setup: bullish · ATP chart readers" };
const ROOM = { id: "room", severity: "fact" as const,
  text: "Next seller area (daily, 24700.00 - 24760.00): 200.00 points above the last close 24500.00 · ATP chart readers" };
const STRUCTURE: CardOptions = { findings: [ROOM, COUNTER, AREA] };
const RESULTS: Measured[] = [];
const card = (t: Locator) => t.locator("[data-check-card]");

test.describe("M76 MP-3: structure lines in the check card", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test.describe.configure({ mode: "serial" });

  test("A+S - only structure lines: one card, three lines, Place as before", async ({ page }) => {
    const seen = await open(page, "light", "A", STRUCTURE);
    const ticket = await ticketToReview(page);
    await expect(ticket.locator("[data-check-card]")).toHaveCount(1);
    await expect(card(ticket).locator("[data-check-line]")).toHaveText([/^Daily trend: down/, /^Last close 24500\.00 inside a seller area/, /^Next seller area/]);
    await expect(card(ticket).locator("[data-check-line] [data-check-accept]")).toHaveCount(0);   // information: no extra click
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    const m = await placeAndMeasure(page, "A", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen);
    expect({ cards: m.cards, lines: m.lines, visible: m.visible }).toEqual({ cards: 1, lines: 3, visible: 3 });
    expect(seen.orders).toHaveLength(1);
    expect((seen.orders[0].accepted_warnings as string[]).sort()).toEqual(["area_conflict", "counter_trend", "room"]);
    expect(seen.checks[0]).not.toHaveProperty("chart_tf");         // no workspace open: the server reads his own timeframes
    RESULTS.push(m);
  });

  test("B+S - with today's facts: structure lines after them, behind 'N more', still one card", async ({ page }) => {
    const seen = await open(page, "light", "B", STRUCTURE);
    const ticket = await ticketToReview(page);
    const c = card(ticket);
    await expect(ticket.locator("[data-check-card]")).toHaveCount(1);
    await expect(c).toHaveAttribute("data-lines", "6");
    await expect(c.locator("[data-check-line]")).toHaveText([/This contract expires today/, /Before the 3 Nov 2026 expiry/, /No live price/]);
    await expect(c.locator("[data-check-more]")).toHaveText("3 more");
    // Folded (three lines): the card ends above the action bar - never over Place or the price fields.
    await c.scrollIntoViewIfNeeded();
    const cardBox = await c.boundingBox();
    const placeBox = await ticket.locator("[data-ticket='place']").boundingBox();
    expect(cardBox && placeBox && (cardBox.y + cardBox.height <= placeBox.y || cardBox.y >= placeBox.y + placeBox.height)).toBe(true);
    await c.locator("[data-check-more]").click();
    await expect(c.locator("[data-check-line]").nth(3)).toHaveAttribute("data-check-line", "counter_trend");
    await expectPhoneLayout(page);
    // Expanded in place: the card scrolls under the sticky action bar, and Place stays on top (nothing covers it).
    const place = ticket.locator("[data-ticket='place']");
    expect(await place.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!top && (top === el || el.contains(top));
    })).toBe(true);
    await c.locator("[data-check-less], [data-check-more]").first().click();
    const m = await placeAndMeasure(page, "B", ticket.locator("[data-ticket='place']"), ticket.locator("[data-ticket='result']"), seen);
    expect({ cards: m.cards, lines: m.lines }).toEqual({ cards: 1, lines: 6 });
    RESULTS.push(m);
  });

  test("Don't show again on a structure line turns that type's switch off", async ({ page }) => {
    const seen = await open(page, "light", "A", STRUCTURE);
    const ticket = await ticketToReview(page);
    await card(ticket).locator("[data-check-mute='room']").click();
    await expect(card(ticket).locator("[data-check-line='room']")).toHaveCount(0);
    await expect.poll(() => seen.saves).toEqual([{ "warnings.room": false }]);
  });

  test.afterAll(() => {
    const out = process.env.TICKET_METRICS_OUT;
    if (!out || !RESULTS.length) return;
    let flows: Measured[] = [];
    try { flows = (JSON.parse(readFileSync(out, "utf8")) as { flows: Measured[] }).flows; } catch { flows = []; }
    flows = [...flows, ...RESULTS.splice(0)];
    writeFileSync(out, JSON.stringify({ flows, cards_per_ticket: flows.reduce((s, r) => s + r.cards, 0) / flows.length }, null, 2));
  });
});

for (const [width, height] of [[390, 844], [1440, 900]] as const) {
  test.describe(`M76 MP-3 structure lines at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of ["light", "dark"] as const) {
      test(`the check card with structure lines (${theme})`, async ({ page }) => {
        const dir = process.env.TICKET_SHOTS_DIR;
        test.skip(!dir, "proof shots only (TICKET_SHOTS_DIR)");
        await open(page, theme, "A", STRUCTURE);
        const ticket = await ticketToReview(page);
        await expect(card(ticket).locator("[data-check-line]")).toHaveCount(3);
        await card(ticket).scrollIntoViewIfNeeded();
        mkdirSync(dir as string, { recursive: true });
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `${dir}/structure-${width}-${theme}.png`, animations: "disabled" });
      });
    }
  });
}
