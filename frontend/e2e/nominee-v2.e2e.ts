import { expect, test, type Route } from "@playwright/test";
import { expectPhoneLayout } from "./phoneLayout";
import { openSettings } from "./settingsMocks";

/**
 * P3-4-B10 (DPDP right to nominate), on made-up replies: Settings -> Account -> "Your nominee" - name one, see it,
 * change it, remove it; the server's answer is what the card shows, and the card is phone-safe.
 */
test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });

test("name, change and remove a nominee on a phone", async ({ page }) => {
  let stored: Record<string, unknown> | null = null;
  const sent: unknown[] = [];
  await openSettings(page, "/settings/account", { routes: async (p) => {
    await p.route(/\/api(?:\/v1)?\/me\/nominee$/, async (r: Route) => {
      const m = r.request().method();
      if (m === "PUT") {
        const body = r.request().postDataJSON() as Record<string, unknown>;
        sent.push(body);
        stored = { ...body, updated_at: "2026-11-07T10:00:00Z" };
      } else if (m === "DELETE") stored = null;
      await r.fulfill({ json: { nominee: stored } });
    });
  } });
  const card = page.locator("[data-nominee]");
  await expect(card).toBeVisible();
  await expect(card.locator("[data-nominee-add]")).toBeVisible();

  await card.locator("[data-nominee-add]").click();
  await card.locator("[data-nominee-name]").fill("Asha Patil");
  await card.locator("[data-nominee-relationship]").fill("Spouse");
  await card.locator("[data-nominee-save]").click();
  await expect(card.getByRole("alert")).toContainText("e-mail or a phone");
  await card.locator("[data-nominee-email]").fill("asha@example.com");
  await card.locator("[data-nominee-save]").click();
  await expect(card.locator("[data-nominee-view]")).toContainText("Asha Patil");
  await expect(card.locator("[data-nominee-view]")).toContainText("asha@example.com");
  expect(sent[0]).toMatchObject({ name: "Asha Patil", relationship: "Spouse", email: "asha@example.com", phone: null });

  await card.locator("[data-nominee-change]").click();
  await expect(card.locator("[data-nominee-name]")).toHaveValue("Asha Patil");
  await card.locator("[data-nominee-phone]").fill("+91 98765 43210");
  await card.locator("[data-nominee-save]").click();
  await expect(card.locator("[data-nominee-view]")).toContainText("+91 98765 43210");

  await expectPhoneLayout(page);

  await card.locator("[data-nominee-remove]").click();
  await expect(card.locator("[data-nominee-add]")).toBeVisible();
  expect(stored).toBeNull();
});
