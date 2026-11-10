import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mockApi } from "../../e2e/mockApi";
import { mockCharts, api } from "../../e2e/chartsMocks";

/**
 * M95 F1: makes the link-preview image `public/og/front-door.png` (1200 x 630): the logo, one line saying what the app
 * is, the three benefits and a real app screen - the app's own Charts page, rendered against a mocked API.
 *
 * Rule M95-Q1 (owner, 10 Nov): the screen shows index data only (NIFTY 50), as of a date at least three months old,
 * with every price figure and price axis hidden. So:
 * - the bars are the index's daily bars from a CSV you pass in (`timestamp,...,open,high,low,close`, plain or .gz);
 *   the file is private and is never committed - only the finished image is;
 * - every bar after OG_AS_OF is dropped, and the run fails when OG_AS_OF is less than three months (92 days) old;
 * - the screenshot is clipped to the price pane's plot, left of the price axis and above the time axis, and the
 *   chart's own number readouts are hidden, so no price figure is in the image.
 *
 * Run (from frontend/, after `npx vite build`):
 *   OG_BARS_CSV=/path/to/NIFTY_daily.csv.gz OG_AS_OF=2026-07-31 PW_CHROMIUM=/opt/pw-browsers/chromium \
 *     npx playwright test -c scripts/og-image/playwright.config.ts
 * The run fails when the image is 300 KB or more (link previews skip large images).
 */

const SYMBOL = "NIFTY 50";
const BARS = 140;
const MIN_AGE_DAYS = 92;
const OUT = join(tmpdir(), "atp-og-image");
const SCREEN = join(OUT, "screen.png");
const HERE = new URL(".", import.meta.url);
const TARGET = new URL("../../public/og/front-door.png", import.meta.url);

interface Bar { timestamp: string; open: number; high: number; low: number; close: number; volume: number }

function asOf(): string {
  const value = process.env.OG_AS_OF ?? "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("OG_AS_OF=YYYY-MM-DD is needed (the newest bar's date)");
  const age = (Date.now() - Date.parse(`${value}T00:00:00Z`)) / 86_400_000;
  if (age < MIN_AGE_DAYS) throw new Error(`OG_AS_OF ${value} is ${Math.floor(age)} days old; M95-Q1 needs at least three months`);
  return value;
}

/** The index's daily bars up to `until` (inclusive), the newest BARS of them, at the session start in IST. */
function readBars(until: string): Bar[] {
  const path = process.env.OG_BARS_CSV;
  if (!path) throw new Error("OG_BARS_CSV is needed (the index's daily bars; never commit the file)");
  const raw = readFileSync(path);
  const text = (path.endsWith(".gz") ? gunzipSync(raw) : raw).toString("utf8");
  const [head, ...rows] = text.trim().split(/\r?\n/);
  const col = head.split(",");
  const at = (name: string) => { const i = col.indexOf(name); if (i < 0) throw new Error(`the CSV has no ${name} column`); return i; };
  const [t, o, h, l, c] = ["timestamp", "open", "high", "low", "close"].map(at);
  const bars = rows.map((r) => r.split(",")).filter((f) => f[t].slice(0, 10) <= until).map((f) => ({
    timestamp: `${f[t].slice(0, 10)}T09:15:00+05:30`, open: +f[o], high: +f[h], low: +f[l], close: +f[c], volume: 0,
  }));
  if (bars.length < BARS) throw new Error(`only ${bars.length} bars up to ${until}`);
  return bars.slice(-BARS);
}

async function openChart(page: Page, bars: Bar[], until: string) {
  await page.clock.install({ time: new Date(`${until}T18:00:00+05:30`) });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true }, brokers: [{ broker_name: "upstox", account_label: "primary", token_status: "VALID",
    token_expires_at: null, last_verified_at: null, needs_login: false, oauth_supported: true, oauth_callback_url: null,
    login_url_supported: false, code_param: null }] });
  // Volume off: the index has no volume of its own (the chart would show an empty pane).
  await mockCharts(page, { market: "closed", prefs: { indicators: { volume: false, rsi: true } } });
  // Registered last, so it answers first: the stored index bars instead of the made-up ones.
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; before: string | null };
    const out = body.before ? [] : bars;
    return r.fulfill({ json: {
      source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "day",
      lookback_days: 365, before: body.before, fetched_at: `${until}T12:00:00Z`, warnings: [], note: "",
      symbols: { [body.symbols[0]]: { bars: out, count: out.length, first: out[0]?.timestamp ?? null, last: out.at(-1)?.timestamp ?? null,
                                      error: null, volume_source: null } },
    } });
  });
  await page.goto(`/charts/${encodeURIComponent(SYMBOL)}?tf=day`);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
}

/** Hides every number the screen would show: the price axis (covered in the chart's own background, so the plot's width
 * stays), and the view buttons; returns the box of the chart card to capture. */
async function hidePrices(page: Page) {
  return page.evaluate(() => {
    const chart = document.querySelector<HTMLElement>("[data-page='charts-v2'] .tv-lightweight-charts")!;
    const table = chart.querySelector("table")!;
    const bg = getComputedStyle(document.body).backgroundColor;
    const plotBg = (() => { let el: HTMLElement | null = chart; while (el) { const c = getComputedStyle(el).backgroundColor;
      if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") return c; el = el.parentElement; } return bg; })();
    const cover = (r: DOMRect) => {
      const d = document.createElement("div");
      Object.assign(d.style, { position: "absolute", left: `${r.left + scrollX}px`, top: `${r.top + scrollY}px`, width: `${r.width}px`,
        height: `${r.height}px`, background: plotBg, zIndex: "50" });
      document.body.appendChild(d);
    };
    // Every row's right-hand cell is the price axis (and, on the last row, the corner under it).
    for (const row of Array.from(table.querySelectorAll("tr"))) { const cells = row.querySelectorAll("td"); if (cells.length) cover(cells[cells.length - 1].getBoundingClientRect()); }
    const view = document.querySelector<HTMLElement>("[role='group'][aria-label='Chart view']");
    if (view) view.style.visibility = "hidden";
    // The card: from the symbol box to the chart's bottom-right corner.
    const symbol = Array.from(document.querySelectorAll<HTMLElement>("[data-page='charts-v2'] input, [data-page='charts-v2'] button"))
      .find((e) => /NIFTY 50/.test((e as HTMLInputElement).value ?? "") || /NIFTY 50/.test(e.textContent ?? ""))!;
    const s = symbol.getBoundingClientRect(); const t = table.getBoundingClientRect();
    return { x: Math.max(0, s.left - 10), y: Math.max(0, s.top - 10), width: t.right - s.left + 18, height: t.bottom - s.top + 18 };
  });
}

test.use({ viewport: { width: 1100, height: 820 }, deviceScaleFactor: 1 });

test("the link-preview image", async ({ page }) => {
  const until = asOf();
  const bars = readBars(until);
  mkdirSync(OUT, { recursive: true });
  await openChart(page, bars, until);
  await page.addStyleTag({ content: "* { caret-color: transparent !important; } *::-webkit-scrollbar { display: none !important; }" });
  await page.waitForTimeout(500);
  // The market-hours chip ("Closed · opens ...") is today's clock, not the screen's date: hidden.
  await page.getByText(/^(Closed|Market open|Market closed)/).first().evaluate((el) => { el.style.visibility = "hidden"; });
  const clip = await hidePrices(page);
  await page.screenshot({ path: SCREEN, clip });

  // The card: rendered from card.html with the logo and the screen inlined, at exactly 1200 x 630.
  const logo = readFileSync(new URL("../../public/favicon.svg", import.meta.url)).toString("base64");
  const screen = readFileSync(SCREEN).toString("base64");
  const date = new Date(`${until}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const html = readFileSync(new URL("card.html", HERE), "utf8")
    .replace("{{LOGO}}", `data:image/svg+xml;base64,${logo}`).replace("{{SCREEN}}", `data:image/png;base64,${screen}`)
    .replace("{{AS_OF}}", date);
  await page.setViewportSize({ width: 1200, height: 630 });
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  mkdirSync(new URL(".", TARGET), { recursive: true });
  await page.screenshot({ path: TARGET.pathname, clip: { x: 0, y: 0, width: 1200, height: 630 } });
  expect(statSync(TARGET).size).toBeLessThan(300_000);
  writeFileSync(join(OUT, "as-of.txt"), `${SYMBOL} daily, ${bars.length} bars, ${bars[0].timestamp.slice(0, 10)} to ${bars.at(-1)!.timestamp.slice(0, 10)}\n`);
});
