import type { Page } from "@playwright/test";

/**
 * SCR-FIX D9 / M49 U6: the Find setups tools (Browse all screens, Advanced, Versions, ...) sit in one "More tools" sheet on
 * the v2 page (and below 640 px on the old one). Clicks the tool `id`, opening the sheet first (unless it is already open).
 */
export async function screenerTool(page: Page, id: string): Promise<void> {
  const more = page.getByTestId("screener-more");
  if (await more.isVisible() && !(await page.getByTestId("screener-more-list").isVisible())) await more.click();
  await page.getByTestId(id).click();
}

/** M49 U6: the list and chart pickers open on "Change" (the hero shows them in words); opens them unless open. */
export async function openListPicker(page: Page): Promise<void> {
  if (!(await page.getByTestId("universe-select").isVisible())) await page.getByTestId("list-change").click();
}

/** M49 U6: picks a way in the one "Start a scan" chooser ("Ready scan", "Template", "Own words"). */
export async function startWith(page: Page, way: "Ready scan" | "Template" | "Own words"): Promise<void> {
  await page.getByRole("tablist", { name: "Start a scan" }).getByRole("tab", { name: way, exact: true }).click();
}

/** Closes the "More" sheet when it is open (a tool such as Advanced keeps it open). */
export async function closeScreenerMore(page: Page): Promise<void> {
  if (await page.getByTestId("screener-more-list").isVisible()) await page.keyboard.press("Escape");
}
