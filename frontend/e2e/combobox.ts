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

/**
 * UI-28 U3.3: picks a row in a field that is either the shared combobox or (old pages) the browser's <select>, by the
 * row's value or `{ label }` - the drop-in for `selectOption` while the v2 pages move to the combobox.
 */
export async function choose(page: Page, field: Locator, pick: string | { label: string }): Promise<void> {
  if ((await field.evaluate((e) => e.tagName)) === "SELECT") { await field.selectOption(pick); return; }
  await field.click();
  const list = page.locator("[data-combobox-list]");
  const row = typeof pick === "string" ? list.locator(`[role='option'][data-value='${pick}']`)
    : list.getByRole("option", { name: pick.label, exact: true });
  await row.first().click();
  if (typeof pick === "string") await expect(field).toHaveAttribute("data-combobox", pick);
}

/** UI-28 U3.3: the field's picked value, for the shared combobox or a <select>. */
export async function expectChoice(field: Locator, value: string): Promise<void> {
  if ((await field.evaluate((e) => e.tagName)) === "SELECT") await expect(field).toHaveValue(value);
  else await expect(field).toHaveAttribute("data-combobox", value);
}

/** UI-28 U3.3: the field's row labels in order, for the shared combobox (opened, then closed again) or a <select>. */
export async function expectChoiceLabels(page: Page, field: Locator, labels: string[]): Promise<void> {
  if ((await field.evaluate((e) => e.tagName)) === "SELECT") { await expect(field.locator("option")).toHaveText(labels); return; }
  await field.click();
  await expect(page.locator("[data-combobox-list] [role='option']")).toHaveText(labels);
  await page.keyboard.press("Escape");
}
