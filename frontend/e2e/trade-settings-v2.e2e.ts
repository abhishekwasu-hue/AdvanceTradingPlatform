import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { choose } from "./combobox";
import { expectPhoneLayout } from "./phoneLayout";
import { openSettings } from "./settingsMocks";

/**
 * M68 PR-0 (owner order M68, TS-2): Settings › Trade settings at /settings/trade-settings (behind `ui_v2`), on the server's
 * own registry replies (e2e/fixtures/trade-settings-*.json are `view({}, admin)` of backend/app/me/trade_settings.py, kept
 * equal by backend/tests/test_m68_trade_settings.py). Every field starts Not set; a trader never sees a field that is not
 * wired yet; a platform admin sees those under Advanced; Save sends only the fields changed in that section.
 */

type View = { sections: { id: string; fields: Record<string, unknown>[] }[]; admin: boolean };
const fixture = (name: string): View => JSON.parse(readFileSync(new URL(`./fixtures/trade-settings-${name}.json`, import.meta.url), "utf8")) as View;

/** The admin reply with the given fields marked wired, as their PR will (a trader then sees them). */
function wired(keys: string[]): View {
  const v = fixture("admin");
  return { admin: false, sections: v.sections.map((s) => ({ ...s, fields: s.fields
    .filter((f) => f.fact || keys.includes(f.key as string)).map((f) => (keys.includes(f.key as string) ? { ...f, wired: true } : f)) })) };
}

async function open(page: Page, reply: View, role = "OWNER"): Promise<Record<string, unknown>[]> {
  const puts: Record<string, unknown>[] = [];
  await openSettings(page, "/settings/trade-settings", { role, routes: async (p) => {
    await p.route(/\/api(?:\/v1)?\/me\/trade-settings$/, async (route: Route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as { values: Record<string, unknown> };
        puts.push(body.values);
        const sections = reply.sections.map((s) => ({ ...s, fields: s.fields.map((f) => (f.key as string) in body.values
          ? { ...f, value: body.values[f.key as string], saved: body.values[f.key as string] !== null } : f) }));
        return route.fulfill({ json: { ...reply, sections } });
      }
      return route.fulfill({ json: reply });
    });
  } });
  await expect(page.locator("[data-testid='trade-settings']")).toBeVisible();
  return puts;
}

const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const sectionAt = (page: Page, id: string) => page.locator(`[data-section='${id}']`);

test.describe("Settings › Trade settings 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a trader sees the seven sections, facts as information and no control that is not wired yet", async ({ page }) => {
    await open(page, fixture("user"));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Trade settings");
    await expect(page.locator("[data-section]")).toHaveCount(7);
    // M68 PR-1: Order defaults is wired - its seven controls, every one Not set; no other section has a control yet.
    await expect(sectionAt(page, "order_defaults").locator("[data-input]")).toHaveCount(7);
    await expect(sectionAt(page, "order_defaults").locator("[data-field] [data-value]")).toHaveText(Array(7).fill("Not set"));
    await expect(page.locator("[data-input]")).toHaveCount(7);
    await expect(page.locator("[data-save]")).toHaveCount(1);
    await expect(page.getByText("Advanced (admin)")).toHaveCount(0);
    await expect(sectionAt(page, "risk_limits").getByRole("link", { name: "Risk limits" })).toHaveAttribute("href", "/settings/risk-limits");
    await expect(page).toHaveScreenshot("trade-settings-v2-1440-light.png", SHOT);
  });

  test("a platform admin sees every field Not set under Advanced", async ({ page }) => {
    await open(page, fixture("admin"), "SUPER_ADMIN");
    // M68 PR-1: Order defaults is wired (no Advanced part left there); Position size is the next section still to wire.
    await expect(sectionAt(page, "order_defaults").locator("summary", { hasText: "Advanced (admin)" })).toHaveCount(0);
    const od = sectionAt(page, "position_size");
    await od.locator("summary", { hasText: "Advanced (admin)" }).click();
    await od.locator("summary", { hasText: "Set by the exchange or your broker" }).click();
    await expect(od.locator("[data-field] [data-value]").first()).toHaveText("Not set");
    await expect(od.locator("[data-combobox]").first()).toHaveAttribute("data-combobox", "");
    await expect(page.locator("[data-field][data-saved='yes']")).toHaveCount(0);
    await expect(od).toHaveScreenshot("trade-settings-admin-v2-1440-light.png", { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
  });

  test("Save sends only what changed in that section; a bad value is marked and nothing is sent", async ({ page }) => {
    const puts = await open(page, wired(["order_defaults.product", "position_size.lots", "position_size.mode"]));
    const size = sectionAt(page, "position_size");
    await size.locator("[data-input='position_size.lots']").fill("0");
    await size.getByRole("button", { name: "Save position size" }).click();
    await expect(page.locator("[data-note='trade-settings-note-position_size']")).toHaveText("Lots: enter at least 1.");
    expect(puts).toHaveLength(0);
    await size.locator("[data-input='position_size.lots']").fill("2");
    await size.getByRole("button", { name: "Save position size" }).click();
    await expect(page.locator("[data-note='trade-settings-note-position_size']")).toHaveText("Saved.");
    expect(puts).toEqual([{ "position_size.lots": 2 }]);
    await expect(size.locator("[data-field='position_size.lots'] [data-value]")).toHaveText("Saved: 2 lots");
    const od = sectionAt(page, "order_defaults");
    await choose(page, od.locator("[data-combobox]"), "NRML");
    await od.getByRole("button", { name: "Save order defaults" }).click();
    await expect(page.locator("[data-note='trade-settings-note-order_defaults']")).toHaveText("Saved.");
    expect(puts[1]).toEqual({ "order_defaults.product": "NRML" });
  });
});

test.describe("Settings › Trade settings 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("no sideways scroll, 44 px targets; the page looks the same", async ({ page }) => {
    await open(page, wired(["order_defaults.product", "position_size.lots"]));
    await expectPhoneLayout(page);
    await open(page, fixture("user"));
    await expectPhoneLayout(page);
    await expect(page).toHaveScreenshot("trade-settings-v2-390-light.png", SHOT);
  });
});
