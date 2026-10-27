import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Handover 11j "idea -> scan" (flag `screener_copilot`, behind `ui_v2`): "From an idea" on the Screener page. The trader
 * writes an idea; each part comes back with his words, what it checks and what it assumed; words ATP cannot read yet
 * are listed as "Not supported yet"; "Open as blocks" puts the draft into the funnel as editable blocks.
 * - Baselines `screener-idea-v2-<width>-<theme>.png` (360 / 390 / 1440, light / dark): the dialog with a read idea.
 * - Flag off: no button. The answer below is the server's own for this idea (app/screener/idea.py), made-up words only.
 * 11j steps 6-7: an edit by chat ("drop the volume condition") with its plain feedback and versions; "Tell me when it
 * matches" saves the scan, then turns on an ordinary bar-close screen alert on the Universe symbols; the saved scan's
 * versions (LAB-18: kept on the server; e2e/screener-versions-v2.e2e.ts covers the dialog).
 * After an intended change: `npx playwright test --project=phone e2e/screener-idea-v2.e2e.ts --update-snapshots`.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const IDEA = "F&O stocks in a Daily uptrend, at a demand zone on falling volume, RSI near 40, pulling back";
const ANSWER = {
  "ok": true,
  "text": "IsFnO() AND Trend() == \"UPTREND\" AND NearZone(\"demand\") AND volume < SMA(volume, 20) AND RSI() >= 35 AND RSI() <= 45",
  "base_tf": "1d",
  "stages": [
    {
      "text": "IsFnO()",
      "wrapped": "IsFnO()",
      "ast": {
        "k": "Call",
        "name": "IsFnO",
        "args": [],
        "kwargs": {},
        "offset": 0,
        "tf": null
      }
    },
    {
      "text": "Trend() == \"UPTREND\"",
      "wrapped": "Trend() == \"UPTREND\"",
      "ast": {
        "k": "Compare",
        "op": "==",
        "left": {
          "k": "Call",
          "name": "Trend",
          "args": [],
          "kwargs": {},
          "offset": 0,
          "tf": null
        },
        "right": {
          "k": "Str",
          "v": "UPTREND"
        }
      }
    },
    {
      "text": "NearZone(\"demand\")",
      "wrapped": "NearZone(\"demand\")",
      "ast": {
        "k": "Call",
        "name": "NearZone",
        "args": [
          {
            "k": "Str",
            "v": "demand"
          }
        ],
        "kwargs": {},
        "offset": 0,
        "tf": null
      }
    },
    {
      "text": "volume < SMA(volume, 20)",
      "wrapped": "volume < SMA(volume, 20)",
      "ast": {
        "k": "Compare",
        "op": "<",
        "left": {
          "k": "Field",
          "name": "volume",
          "offset": 0,
          "tf": null
        },
        "right": {
          "k": "Call",
          "name": "SMA",
          "args": [
            {
              "k": "Field",
              "name": "volume",
              "offset": 0,
              "tf": null
            },
            {
              "k": "Num",
              "v": 20.0
            }
          ],
          "kwargs": {},
          "offset": 0,
          "tf": null
        }
      }
    },
    {
      "text": "RSI() >= 35",
      "wrapped": "RSI() >= 35",
      "ast": {
        "k": "Compare",
        "op": ">=",
        "left": {
          "k": "Call",
          "name": "RSI",
          "args": [],
          "kwargs": {},
          "offset": 0,
          "tf": null
        },
        "right": {
          "k": "Num",
          "v": 35.0
        }
      }
    },
    {
      "text": "RSI() <= 45",
      "wrapped": "RSI() <= 45",
      "ast": {
        "k": "Compare",
        "op": "<=",
        "left": {
          "k": "Call",
          "name": "RSI",
          "args": [],
          "kwargs": {},
          "offset": 0,
          "tf": null
        },
        "right": {
          "k": "Num",
          "v": 45.0
        }
      }
    }
  ],
  "parts": [
    {
      "rule": "fno",
      "phrase": "F&O",
      "condition": "IsFnO()",
      "reads": "the symbol is in the F&O segment",
      "assumptions": [],
      "ok": true,
      "problems": []
    },
    {
      "rule": "trend_up",
      "phrase": "uptrend",
      "condition": "Trend() == \"UPTREND\"",
      "reads": "the market-structure trend is up",
      "assumptions": [],
      "ok": true,
      "problems": []
    },
    {
      "rule": "demand_zone",
      "phrase": "demand zone",
      "condition": "NearZone(\"demand\")",
      "reads": "the close is at a buyer area (a demand zone)",
      "assumptions": [
        "at the area = within 0.5% of the nearest live zone (the usual setting)"
      ],
      "ok": true,
      "problems": []
    },
    {
      "rule": "volume_low",
      "phrase": "falling volume",
      "condition": "volume < SMA(volume, 20)",
      "reads": "volume is below its 20-bar average",
      "assumptions": [
        "falling volume = this bar's volume below its 20-bar average"
      ],
      "ok": true,
      "problems": []
    },
    {
      "rule": "rsi_level",
      "phrase": "RSI near 40",
      "condition": "RSI() >= 35 AND RSI() <= 45",
      "reads": "RSI is between 35 and 45",
      "assumptions": [
        "near 40 = within 5 points of it",
        "RSI over 14 bars (the usual setting)"
      ],
      "ok": true,
      "problems": []
    }
  ],
  "not_supported": [
    {
      "phrase": "pulling back",
      "why": "not supported yet"
    }
  ],
  "disclaimer": "A draft scan from your idea: check each block, edit it, then run it. Matches are filters passed, not advice."
};
// The server's answer after "drop the volume condition" (app/screener/idea_edits.py), and after an edit that changes nothing.
const DROPPED = {
  ...ANSWER,
  text: "IsFnO() AND Trend() == \"UPTREND\" AND NearZone(\"demand\") AND RSI() >= 35 AND RSI() <= 45",
  stages: ANSWER.stages.filter((s) => !s.text.startsWith("volume")),
  parts: ANSWER.parts.filter((p) => p.rule !== "volume_low"),
  edits: [{ edit: "drop the volume condition", kind: "removed", message: "Removed: volume is below its 20-bar average.", changed: true,
    removed: ["volume is below its 20-bar average"], added: [] }],
};
const BANKS = { ...ANSWER, edits: [{ edit: "only banks", kind: "unchanged", message: "“banks” is not supported yet, so nothing changed.", changed: false,
  removed: [], added: [] }] };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, ai_copilot: true, screener_v2: true, screener_copilot: true }, brokers: CONNECTED };
const OFF: MockOptions = { features: { ui_v2: true, ai_copilot: true, screener_v2: true }, brokers: CONNECTED };
const THEMES = ["dark", "light"] as const;

async function open(page: Page, theme: "dark" | "light", options: MockOptions = ON): Promise<{ ideas: unknown[]; screens: unknown[]; rules: unknown[] }> {
  const calls = { ideas: [] as unknown[], screens: [] as unknown[], rules: [] as unknown[] };
  const versions: unknown[] = [];
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await page.route(/\/api(\/v1)?\/alerts\/rules$/, async (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: [] });
    const b = route.request().postDataJSON() as Record<string, unknown>;
    calls.rules.push(b);
    return route.fulfill({ status: 201, json: { id: 7, name: b.name, kind: "screen", screen_id: b.screen_id, base_tf: "1d", status: "active",
      symbols: b.symbols, fire_on: b.fire_on } });
  });
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    if (/\/copilot\/idea$/.test(url)) {
      const body = route.request().postDataJSON() as { edits?: string[] };
      calls.ideas.push(body);
      const last = body.edits?.[body.edits.length - 1];
      return route.fulfill({ json: last === "only banks" ? BANKS : last ? DROPPED : ANSWER });
    }
    if (/\/screens$/.test(url)) {
      const b = route.request().postDataJSON() as { name: string; source: string; base_tf: string };
      calls.screens.push(b);
      versions.push({ version: 1, number: 1, latest: true, name: b.name, base_tf: b.base_tf, conditions: 6, change: ["First saved version."],
        origin: "save", created_at: "2026-10-22T08:35:00Z" });
      return route.fulfill({ status: 201, json: { id: 41, name: b.name, text: b.source, base_tf: b.base_tf } });
    }
    // LAB-18: the server keeps the versions (what the save above wrote)
    if (/\/screens\/41\/versions$/.test(url)) return route.fulfill({ json: { screen_id: 41, imported: true, max: 50, versions } });
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
  return calls;
}

async function readIdea(page: Page) {
  await page.getByTestId("idea-open-dialog").click();
  const dialog = page.getByRole("dialog", { name: "A scan from your idea" });
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("idea-text").fill(IDEA);
  await dialog.getByTestId("idea-read").click();
  await expect(dialog.getByTestId("idea-status")).toHaveText("5 parts of your idea became blocks. Check each one, then open them in the builder.");
  return dialog;
}

test.describe("From an idea, 1440", () => {
  test.use({ viewport: { width: 1440, height: 1200 } });

  test("flag off: no button", async ({ page }) => {
    await open(page, "light", OFF);
    await expect(page.getByTestId("idea-open-dialog")).toHaveCount(0);
  });

  test("an idea becomes parts, then editable blocks in the funnel", async ({ page }) => {
    const calls = await open(page, "light");
    const dialog = await readIdea(page);
    expect(calls.ideas).toEqual([{ text: IDEA, base_tf: "1d" }]);
    await expect(dialog.getByTestId("idea-parts").locator("li")).toHaveCount(5);
    await expect(dialog.getByTestId("idea-unsupported")).toContainText("“pulling back”");
    await expect(dialog.getByText("Checks: the market-structure trend is up.")).toBeVisible();
    await dialog.getByTestId("idea-open").click();
    await expect(dialog).toBeHidden();
    await expect(page.getByLabel("Scan name")).toHaveValue("Scan from my idea");
    await expect(page.getByRole("list", { name: "Stages - every one must pass" }).locator(":scope > li")).toHaveCount(6);
  });

  test("an edit by chat changes the draft with plain feedback and becomes a version", async ({ page }) => {
    const calls = await open(page, "light");
    const dialog = await readIdea(page);
    await dialog.getByTestId("idea-edit-text").fill("only banks");
    await dialog.getByTestId("idea-edit-apply").click();
    await expect(dialog.getByTestId("idea-edit-feedback")).toHaveText("“banks” is not supported yet, so nothing changed.");
    await expect(dialog.getByTestId("idea-versions")).toHaveCount(0);
    await dialog.getByTestId("idea-edit-text").fill("drop the volume condition");
    await dialog.getByTestId("idea-edit-apply").click();
    await expect(dialog.getByTestId("idea-edit-feedback")).toHaveText("Removed: volume is below its 20-bar average.");
    expect(calls.ideas[2]).toEqual({ text: IDEA, base_tf: "1d", edits: ["drop the volume condition"] });
    await expect(dialog.getByTestId("idea-parts").locator("li")).toHaveCount(4);
    await expect(dialog.getByTestId("idea-versions").getByRole("button")).toHaveText(["Version 1 · your idea", "Version 2 · drop the volume condition"]);
    await expect(dialog.getByTestId("idea-version-change")).toContainText("What changed from version 1: Removed: volume is below its 20-bar average.");
    await dialog.getByRole("button", { name: "Version 1 · your idea" }).click();
    await expect(dialog.getByTestId("idea-parts").locator("li")).toHaveCount(5);
    await dialog.getByRole("button", { name: "Version 2 · drop the volume condition" }).click();
    await dialog.getByTestId("idea-open").click();
    await expect(page.getByRole("list", { name: "Stages - every one must pass" }).locator(":scope > li")).toHaveCount(5);
  });

  test("Tell me when it matches saves the scan, then turns on a bar-close alert on the universe; the save is a version", async ({ page }) => {
    const calls = await open(page, "light");
    const dialog = await readIdea(page);
    await dialog.getByTestId("idea-open").click();
    await expect(page.getByTestId("scan-versions-open")).toHaveCount(0);
    await page.getByTestId("scan-alert-open").click();
    const alert = page.getByRole("dialog", { name: "Tell me when it matches" });
    // owner order 23 Oct: the universe is never empty - NIFTY 50 until the trader picks another list
    await expect(alert).toContainText("for “Scan from my idea” on NIFTY 50 (50 stocks), and tells you when one matches.");
    await page.keyboard.press("Escape");
    await page.getByTestId("universe-select").selectOption("custom");
    await page.getByLabel("Your symbols (up to 500)").fill("RELIANCE, TCS, INFY");
    await page.getByTestId("scan-alert-open").click();
    await expect(alert).toContainText("ATP checks each time a daily bar closes for “Scan from my idea” on your own symbols (3 stocks), and tells you when one matches.");
    await alert.getByTestId("scan-alert-create").click();
    await expect(alert.getByTestId("scan-alert-done")).toBeVisible();
    expect(calls.screens).toHaveLength(1);
    expect(calls.rules).toEqual([{ name: "Scan from my idea", kind: "screen", screen_id: 41, symbols: ["RELIANCE", "TCS", "INFY"], fire_on: "bar_close",
      mode: "instant", priority: "normal" }]);
    await expect(alert.getByTestId("scan-alert-rules")).toContainText("On · checks each time a daily bar closes · 3 symbols");
    await page.keyboard.press("Escape");
    await page.getByTestId("scan-versions-open").click();
    const versions = page.getByRole("dialog", { name: "Versions of this scan" });
    await expect(versions).toContainText("Version 1");
    await expect(versions).toContainText("First saved version.");
  });

  for (const theme of THEMES) {
    test(`the read idea looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await readIdea(page);
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveScreenshot(`screener-idea-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }
});

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`From an idea, ${size.width}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    test("the phone layout rules hold on the page", async ({ page }) => {
      await open(page, "light");
      await expectPhoneLayout(page);
    });

    for (const theme of THEMES) {
      test(`the read idea looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await readIdea(page);
        await page.waitForLoadState("networkidle");
        await expect(page).toHaveScreenshot(`screener-idea-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

/** UX-11 (owner, 27 Oct): numbers first - every <Page> card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`screener-idea text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, "light");
      console.log(`[words] screener-idea ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
