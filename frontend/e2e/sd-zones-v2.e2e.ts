import { expect, test, type Page } from "@playwright/test";
import { api, madeUpBars, mockCharts } from "./chartsMocks";
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

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);
const candle = (b: ReturnType<typeof madeUpBars>[number], avg: number) => ({
  time: sec(b.timestamp), high: b.high, low: b.low, range: b.high - b.low,
  body_pct: Math.round((Math.abs(b.close - b.open) / (b.high - b.low)) * 100), mult: Math.round(((b.high - b.low) / avg) * 10) / 10,
});

/** One made-up zone on the mocked 15 min bars: leg-in at bar k, a two-candle base, the leg-out after it. */
function zoneOn(formation: Formation, tf: "15m" | "1d", k: number, id: string) {
  const bars = madeUpBars(tf === "15m" ? "15min" : "day");
  const avg = bars.slice(k - 20, k).reduce((s, b) => s + (b.high - b.low), 0) / 20;
  const base = bars.slice(k + 1, k + 3);
  const demand = formation === "DBR" || formation === "RBR";
  const hi = Math.max(...base.map((b) => b.high)), lo = Math.min(...base.map((b) => b.low));
  const proximal = demand ? hi : lo, distal = demand ? lo - 8 : hi + 8;
  const short = tf === "15m" ? "15m" : "D";
  return {
    id, timeframe: tf, side: demand ? "demand" : "supply", formation, state: tf === "15m" ? "fresh" : "tested", tests: tf === "15m" ? 0 : 1,
    proximal, distal, base_start: sec(base[0].timestamp), base_end: sec(base[1].timestamp), formed: sec(bars[k + 3].timestamp), violated_at: null,
    leg_in: candle(bars[k], avg), leg_out: { ...candle(bars[k + 3], avg), of: "average range" },
    base: { candles: 2, times: base.map((b) => sec(b.timestamp)), high: hi, low: lo, max_body_pct: 40, max_mult: 0.6, bars: base.map((b) => candle(b, avg)) },
    label: `${demand ? "Demand" : "Supply"} · ${formation} · ${tf === "15m" ? "Fresh" : "Tested 1"} · ${short}`,
  };
}

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
  const calls = await mockCharts(page, { prefs: { theme: "dark", indicators: { volume: false, rsi: false } } });
  const asks: URLSearchParams[] = [];
  await page.route(api("/charts/layers/sd-zones"), (r) => {
    const q = new URL(r.request().url()).searchParams;
    asks.push(q);
    const tfs = (q.get("timeframes") ?? "").split(",");
    const rows = tfs.map((tf) => ({
      timeframe: tf, bars: 160, as_of_bar: null, more: 0, hidden_violated: tf === "15m" ? 1 : 0, source: "lake", reason: null,
      zones: tf === "15m" ? [zoneOn(formation, "15m", 128, `z-${formation}`)] : tf === "1d" ? [zoneOn(formation === "DBR" ? "RBD" : formation === "RBD" ? "DBR" : formation === "RBR" ? "DBD" : "RBR", "1d", 150, "z-d")] : [],
    }));
    return r.fulfill({ json: { schema: "sd-layer/1", symbol: q.get("symbol"), at: "2026-10-19T05:30:00Z", note: "", include_violated: q.get("include_violated") === "1",
      settings: {}, settings_note: "reference - edit before use", timeframes: rows } });
  });
  await page.goto("/charts?tf=15min");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
  return Object.assign(asks, { calls });
}

async function turnOn(page: Page) {
  await page.getByRole("button", { name: /^Indicators/ }).click();
  await page.getByRole("switch", { name: /Supply & Demand zones/ }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("sd-zones-bar")).toBeVisible();
}

/** Moves the pointer down the price pane (left of the price axis) at a few widths until the tooltip of a zone whose
 * label holds `want` opens. */
async function hoverZone(page: Page, want = "·") {
  const box = await page.locator("[data-page='charts-v2'] .tv-lightweight-charts").first().boundingBox();
  if (!box) throw new Error("no chart");
  const plot = box.width - 90;                                   // the price axis takes the right edge
  for (const frac of [0.97, 0.9, 0.8]) {
    for (let y = box.y + 30; y < box.y + box.height * 0.8; y += 3) {
      await page.mouse.move(box.x + plot * frac, y);
      const tip = page.locator("[data-band-tip]");
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
    await expect(page.getByTestId("sd-zones-bar")).toContainText("1 violated hidden");
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

  test("PR-5: the toggle saves sd_zones on the server; a trading style preselects its timeframes and an edit is kept", async ({ page }) => {
    const asks = await open(page, "DBR", 1440);
    await turnOn(page);
    await expect.poll(() => asks.calls.prefsPuts.at(-1)?.body.sd_zones).toBe(true);
    await page.getByTestId("sd-zones-bar").getByRole("combobox", { name: "Trading style" }).click();
    await page.getByRole("option", { name: "Swing · D, 2H" }).click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("2h,1d");
    await expect.poll(() => (asks.calls.prefsPuts.at(-1)?.body.sd_overlay as { style?: string })?.style).toBe("swing");
    await page.getByTestId("sd-zones-bar").getByRole("button", { name: "1H", exact: true }).click();
    await expect.poll(() => asks.at(-1)?.get("timeframes")).toBe("1h,2h,1d");
    await expect.poll(() => (asks.calls.prefsPuts.at(-1)?.body.sd_overlay as { presets?: unknown })?.presets).toEqual({ swing: ["1h", "2h", "1d"] });
    await page.getByRole("button", { name: /^Indicators/ }).click();
    await page.getByRole("switch", { name: /Supply & Demand zones/ }).click();
    await expect.poll(() => asks.calls.prefsPuts.at(-1)?.body.sd_zones).toBe(false);
  });

  for (const formation of FORMATIONS) {
    for (const width of [390, 1440]) {
      test(`proof ${formation} ${width}`, async ({ page }) => {
        test.skip(!PROOF, "proof images only with M97_PROOF=1");
        await open(page, formation, width);
        await turnOn(page);
        await page.getByTestId("sd-zones-bar").getByRole("button", { name: "D", exact: true }).click();
        await page.waitForLoadState("networkidle");
        await hoverZone(page, `· ${formation} · Fresh · 15m`);
        await page.waitForTimeout(150);
        await page.locator("[data-page='charts-v2']").screenshot({ path: `../docs/design/m97/sd-${formation.toLowerCase()}-${width}-dark.png`, scale: "css" });
      });
    }
  }
});
