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
  const tag = await field.evaluate((e) => e.tagName);
  if (tag === "SELECT") { await field.selectOption(pick); return; }
  // CT-1 C6: the one SymbolPicker (a text field): type the symbol and press Enter, as a user does.
  if (tag === "INPUT" && typeof pick === "string") { await field.fill(pick); await field.press("Enter"); await expect(field).toHaveValue(pick); return; }
  await field.click();
  const list = page.locator("[data-combobox-list]");
  const row = typeof pick === "string" ? list.locator(`[role='option'][data-value='${pick}']`)
    : list.getByRole("option", { name: pick.label, exact: true });
  await row.first().click();
  if (typeof pick === "string") await expect(field).toHaveAttribute("data-combobox", pick);
}

/** UI-28 U3.3: the field's picked value, for the shared combobox or a <select>. */
export async function expectChoice(field: Locator, value: string): Promise<void> {
  const tag = await field.evaluate((e) => e.tagName);
  if (tag === "SELECT" || tag === "INPUT") await expect(field).toHaveValue(value);       // CT-1 C6: the SymbolPicker is a field
  else await expect(field).toHaveAttribute("data-combobox", value);
}

/** UI-28 U3.3: the field's row labels in order, for the shared combobox (opened, then closed again) or a <select>. */
export async function expectChoiceLabels(page: Page, field: Locator, labels: string[]): Promise<void> {
  if ((await field.evaluate((e) => e.tagName)) === "SELECT") { await expect(field.locator("option")).toHaveText(labels); return; }
  const button = field.and(page.locator("[data-combobox]"));   // the open list carries the same label
  await button.click();
  await expect(page.locator("[data-combobox-list] [role='option']")).toHaveText(labels);
  await button.click();                   // closes the list (Escape would also close a sheet around it)
  await expect(page.locator("[data-combobox-list]")).toHaveCount(0);
}

/** UI-28 U3.3: the labels of one group's rows ("Strategy templates") in the shared combobox, opened then closed again. */
export async function expectGroupLabels(page: Page, field: Locator, group: string, labels: string[]): Promise<void> {
  const button = field.and(page.locator("[data-combobox]"));
  await button.click();
  const rows = await page.locator("[data-combobox-list] li").evaluateAll((lis, g) => {
    const out: string[] = [];
    let inGroup = false;
    for (const li of lis) {
      if (li.getAttribute("role") === "presentation") inGroup = li.textContent?.trim() === g;
      else if (inGroup && li.getAttribute("role") === "option") out.push(li.textContent?.trim() ?? "");
    }
    return out;
  }, group);
  expect(rows).toEqual(labels);
  await button.click();
  await expect(page.locator("[data-combobox-list]")).toHaveCount(0);
}

/** M68 PR-1: the bot forms pre-select no entry style (Trade settings > Order defaults > Order type); where a form asks
 * for it, pick Market as the user would. Does nothing where the form does not ask. */
export async function pickEntryStyle(page: Page, scope: Locator | Page): Promise<void> {
  const combo = scope.locator("[data-entry-input='style']");
  const select = scope.locator("#deploy-entry-style");
  if (await combo.count()) await pickOption(page, combo.first(), "MARKET");
  else if (await select.count() && !(await select.inputValue())) await select.selectOption("MARKET");
  await pickEntryMode(page, scope);
}

/** M68 PR-6: the bot forms pre-select no "New entries" (Trade settings > Automation); where a form asks for it, pick
 * "Placed automatically" as the user would. Does nothing where the form does not ask. */
export async function pickEntryMode(page: Page, scope: Locator | Page): Promise<void> {
  const combo = scope.locator("[data-entry-input='mode']");
  if (await combo.count()) { await pickOption(page, combo.first(), "AUTO"); return; }
  const select = scope.locator("#deploy-entry-mode");
  if (await select.count() && !(await select.inputValue())) await select.selectOption("AUTO");
}
