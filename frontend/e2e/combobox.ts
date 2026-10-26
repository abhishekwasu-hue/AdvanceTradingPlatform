import { expect, type Locator, type Page } from "@playwright/test";

/**
 * UX-5: the shared combobox (src/ui/Combobox.tsx) replaces the browser's <select> on the v2 pages. These helpers drive it
 * the way a user does: open it, then pick the row. `data-combobox` on the button carries the current value.
 */
export async function pickOption(page: Page, combobox: Locator, value: string): Promise<void> {
  await combobox.click();
  await page.locator(`[data-combobox-list] [role='option'][data-value='${value}']`).first().click();
  await expect(combobox).toHaveAttribute("data-combobox", value);
}

export async function expectComboValue(combobox: Locator, value: string): Promise<void> {
  await expect(combobox).toHaveAttribute("data-combobox", value);
}

/** Opens the combobox and returns its option rows (close it again with Escape). */
export async function comboOptions(page: Page, combobox: Locator): Promise<Locator> {
  await combobox.click();
  return page.locator("[data-combobox-list] [role='option']");
}
