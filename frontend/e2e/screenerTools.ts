import type { Page } from "@playwright/test";

/**
 * SCR-FIX D9: below 640 px the Find setups header's tools (Browse screens, From an idea, Advanced, Versions, ...) sit in
 * one "More" sheet. Clicks the tool `id` either way: opens the sheet first on a phone (unless it is already open).
 */
export async function screenerTool(page: Page, id: string): Promise<void> {
  const more = page.getByTestId("screener-more");
  if (await more.isVisible() && !(await page.getByTestId("screener-more-list").isVisible())) await more.click();
  await page.getByTestId(id).click();
}

/** Closes the "More" sheet when it is open (a tool such as Advanced keeps it open). */
export async function closeScreenerMore(page: Page): Promise<void> {
  if (await page.getByTestId("screener-more-list").isVisible()) await page.keyboard.press("Escape");
}
