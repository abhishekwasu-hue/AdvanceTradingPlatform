import { expect, test, type Page } from "@playwright/test";
import { api, mockCharts } from "./chartsMocks";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * M97 PR-4: the "Supply & Demand zones" layer on the v2 Charts page (flag `sd_pack`).
 * - Turned on from Indicators -> Layers, it reads `/charts/layers/sd-zones` for the chart's own timeframe; a higher
 *   timeframe chip adds that timeframe to the read; "Show violated" asks for violated zones; a hovered / tapped zone
 *   opens its tooltip with the formation, state, lines and leg / base numbers.
 * - Proof images (one chart per formation, DBR / RBR / RBD / DBD, phone 390 and desktop 1440, dark) are written to
 *   docs/design/m97/ only with M97_PROOF=1 (no baselines: the zones are made-up data, e2e/chartsMocks.ts bars).
 * Every reply is made-up data: no real account, price or id.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const PROOF = process.env.M97_PROOF === "1";
const FORMATIONS = ["DBR", "RBR", "RBD", "DBD"] as const;
type Formation = (typeof FORMATIONS)[number];

/** The real detector's output on hand-built bars that form each formation (backend/scripts/m97_proof_fixture.py):
 * the candles shown and the sd-zones answer are the same made-up bars, so the zone, the highlighted candles and the
 * tooltip numbers agree with what is on screen. */
const PROOF_DATA = JSON.parse(readFileSync(new URL("./fixtures/sd-zones-proof.json", import.meta.url), "utf8")) as { formations: Record<Formation, { bars: Record<string, unknown>[]; rows: Record<string, unknown> }> };

async function open(page: Page, formation: Formation, width: number) {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, sd_pack: true }, brokers: CONNECTED });
  await mockCharts(page, { market: "closed", prefs: { theme: "dark", indicators: { volume: false, rsi: false } } });   // no live price folded into the bars
  const fx = PROOF_DATA.formations[formation];
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number; before: string | null };
    const bars = body.before || body.timeframe !== "15min" ? [] : fx.bars;
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: body.lookback_days, before: body.before, fetched_at: "2026-10-19T05:30:00Z", warnings: [], note: "",
      symbols: { [body.symbols[0]]: { bars, count: bars.length, first: null, last: null, error: null, volume_source: null } } } });
  });
  const asks: URLSearchParams[] = [];
  await page.route(api("/charts/layers/sd-zones"), (r) => {
    const q = new URL(r.request().url()).searchParams;
    asks.push(q);
    const rows = (q.get("timeframes") ?? "").split(",").map((tf) => (tf === "15m" ? fx.rows
      : { timeframe: tf, bars: 0, as_of_bar: null, zones: [], more: 0, hidden_violated: 0, source: null, reason: "no closed bars for this instrument and timeframe yet" }));
    return r.fulfill({ json: { schema: "sd-layer/1", symbol: q.get("symbol"), at: "2026-10-19T05:30:00Z", note: "", include_violated: q.get("include_violated") === "1",
      settings: {}, settings_note: "reference - edit before use", timeframes: rows } });
  });
  await page.goto("/charts?tf=15min");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
  return asks;
}

async function turnOn(page: Page) {
  await page.getByRole("button", { name: /^Indicators/ }).click();
  await page.getByRole("switch", { name: /Supply & Demand zones/ }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("sd-zones-bar")).toBeVisible();
}

/** Moves the pointer down the price pane (left of the price axis) at a few widths until the tooltip of a zone whose
 * label holds `want` opens. M98 F3: a phone-width chart opens a zone's details on a tap (a click), not on a hover. */
async function hoverZone(page: Page, want = "·") {
  const tap = (page.viewportSize()?.width ?? 1440) < 600;
  const box = await page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first().boundingBox();
  if (!box) throw new Error("no chart");
  const plot = box.width - 90;                                   // the price axis takes the right edge
  for (const frac of [0.97, 0.9, 0.8, 0.7]) {
    // bottom up: the first hit is the zone's lower edge, so the tooltip opens below the zone and its candles stay in view
    for (let y = box.y + box.height * 0.85; y > box.y + 30; y -= tap ? 6 : 3) {
      if (tap) await page.mouse.click(box.x + plot * frac, y); else await page.mouse.move(box.x + plot * frac, y);
      const tip = page.locator("[data-band-tip], [data-band-sheet]");    // M98 F3: a phone-width chart opens a bottom sheet
      if (await tip.locator("[data-band-rows]").count() && (await tip.locator("p").first().textContent())?.includes(want)) return true;
    }
  }
  return false;
}

test.describe("M97 Supply & Demand zones layer", () => {
  test("reads the chart's own timeframe, adds a higher one, asks for violated zones, and a zone's tooltip names its parts", async ({ page }) => {
    const asks = await open(page, "DBR", 1440);
    await turnOn(page);
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("15m");
    expect(asks.at(-1)?.get("symbol")).toBe("NSE:NIFTY 50");
    await page.getByTestId("sd-zones-bar").getByRole("button", { name: "D", exact: true }).click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("15m,1d");
    await expect(page.getByTestId("sd-zones-bar")).toContainText("no closed bars yet on D");
    await page.getByLabel("Show violated").check();
    await expect.poll(() => asks.at(-1)?.get("include_violated")).toBe("1");
    await page.getByLabel("Show violated").uncheck();
    expect(await hoverZone(page)).toBe(true);
    const tip = page.locator("[data-band-tip]");
    await expect(tip).toContainText("Formation");
    await expect(tip).toContainText("Proximal");
    await expect(tip).toContainText("Leg-out");
    await expect(tip).not.toContainText(/buy|sell|target|stop/i);
  });

  test("M98 T1: the zones follow the chart's timeframe; a higher chip is added on top and kept", async ({ page }) => {
    const asks = await open(page, "DBR", 1440);
    await turnOn(page);
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("15m");
    const bar = page.getByTestId("sd-zones-bar");
    await expect(bar.locator("[data-sd-own]")).toHaveText("15m");
    await expect(bar.locator('[data-sd-tf="5m"]')).toHaveCount(0);                 // only higher timeframes are chips
    await bar.getByRole("button", { name: "1H", exact: true }).click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("15m,1h");
    const toolbar = page.locator("[data-page='charts-v2']");
    await toolbar.getByRole("button", { name: "5 min", exact: true }).first().click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("5m,1h");       // the chart's own follows; the 1H chip stays
    await expect(bar.locator("[data-sd-own]")).toHaveText("5m");
    await toolbar.getByRole("button", { name: "1 hour", exact: true }).first().click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("1h");           // 1H is now the chart's own
  });

  test("M98 F3: on a phone a zone's details open in a small bottom sheet (closed by default) with a close button", async ({ page }) => {
    await open(page, "DBR", 390);
    await turnOn(page);
    await expect(page.locator("[data-band-sheet]")).toHaveCount(0);
    expect(await hoverZone(page)).toBe(true);
    const sheet = page.locator("[data-band-sheet]");
    await expect(sheet).toBeVisible();
    await expect(page.locator("[data-band-tip]")).toHaveCount(0);
    await expect(sheet).toContainText("Proximal");
    const box = await sheet.boundingBox();
    expect(box && box.height).toBeLessThanOrEqual(844 * 0.36);
    expect(box && box.y + box.height).toBeGreaterThanOrEqual(843);                  // at the bottom of the screen
    await sheet.getByRole("button", { name: "Close zone details" }).click();
    await expect(sheet).toHaveCount(0);
  });

  for (const formation of FORMATIONS) {
    for (const width of [390, 1440]) {
      test(`proof ${formation} ${width}`, async ({ page }) => {
        test.skip(!PROOF, "proof images only with M97_PROOF=1");
        await open(page, formation, width);
        await turnOn(page);
        await page.waitForLoadState("networkidle");
        expect(await hoverZone(page, `· ${formation} · Fresh · 15m`)).toBe(true);
        await page.waitForTimeout(150);
        // a page screenshot clipped to the chart page: no scrolling, so the pointer stays and the tooltip stays open
        const clip = await page.locator("[data-page='charts-v2']").boundingBox();
        await expect(page.locator(width < 600 ? "[data-band-sheet]" : "[data-band-tip]")).toBeVisible();
        // M98 F3: the phone shot is the whole screen (the zone's bottom sheet sits at the screen's bottom edge)
        await page.screenshot({ path: `../docs/design/m97/sd-${formation.toLowerCase()}-${width}-dark.png`, scale: "css", clip: width < 600 ? undefined : clip ?? undefined });
      });
    }
  }
});
