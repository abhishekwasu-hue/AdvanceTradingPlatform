import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * UX-11: the replies the My trades cards (Experience level, Portfolio risk, Decision tools, Plan before trade) read, for
 * the e2e specs (hand-written, made-up data in fixtures/mytrades_cards_v2.json). Register after mockApi() and before page.goto().
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/mytrades_cards_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);

export async function mockMyTradesCards(page: Page): Promise<void> {
  const gets: [RegExp, unknown][] = [
    [api("/paper/experience(\\?|$)"), F.experience],
    [api("/portfolio/risk(\\?|$)"), F.portfolio_risk],
    [api("/decision/positions(\\?|$)"), F.decision_positions],
    [api("/decision/exposure(\\?|$)"), F.decision_exposure],
    [api("/paper/risk(\\?|$)"), F.paper_risk],
    [api("/paper/plans(\\?|$)"), F.paper_plans],
  ];
  await page.route(/\/api(?:\/v1)?\//, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, "");
    const hit = gets.find(([re]) => re.test(path));
    return hit ? route.fulfill({ json: hit[1] }) : route.fallback();
  });
}
