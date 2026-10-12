import { expect, test } from "@playwright/test";
import { bars, chartRoutes, painted } from "./chartMocks";
import { mockApi } from "./mockApi";

/**
 * N4 CH6-2: 50,000 candles plus 6 study lines rendered on ProChart (the full-window chart, the production build, every
 * API answer mocked). Timed inside the page (performance.now), from the browser receiving each answer (the fetch
 * resolving) to the frame that shows it (a MutationObserver sees the attribute, then two animation frames):
 *   candles: the candles answer -> ProChart holds all 50,000 bars (data-bars) and has painted;
 *   studies: the studies answer -> the 6 lines are on the chart (StudiesPanel data-drawn, after the WebWorker) and painted.
 * Budgets (x ATP_CI_FACTOR, the Z10-d calibration of the runner, computed by the perf job; 1 when unset):
 *   candles < 1 s (the CHARTING "50k bars render < 1 s" target, ProChart with its default indicators and volume);
 *   6 study lines < 1.5 s on top (provisional, OPEN_QUESTIONS CH6-2: each lightweight-charts setData re-merges the
 *   time scale of every series, about 150 ms per 50,000-point line here).
 * Run: `npx playwright test -c playwright.perf.config.ts` (PW_CHROMIUM=/opt/pw-browsers/chromium in this container).
 */
const N = 50_000;
const STUDIES = ["SMA(close, 20)", "EMA(close, 50)", "SMA(close, 200)", "RSI(14)", "ATR(14)", "Max(high, 20) - Min(low, 20)"];
const FACTOR = Number(process.env.ATP_CI_FACTOR ?? "1") || 1;

test("50,000 candles render in under a second and 6 study lines in 1.5 s more (x CI factor)", async ({ page }, info) => {
  const b = bars(N);
  const times = b.map((x) => Math.floor(Date.parse(x.timestamp) / 1000));
  const closes = b.map((x) => x.close);
  const line = (k: number, scale: number, offset: number) => closes.map((c, i) => (i < k ? null : c * scale + offset));
  const studies = {
    schema: "study/1", instrument: "NSE:PERF", tf: "5m", at: "", start: "", source: "lake:5m", as_of: b[N - 1].timestamp, bars: N, warmup_bars: 0,
    times, first_time: times[0], last_time: times[N - 1], fingerprint: "perf", note: "", reason: null,
    studies: STUDIES.map((expr, i) => ({ id: i, expr, text: expr, ok: true, problems: [], kind: "num", unit: i === 3 ? "index" : "price",
                                         pane: i < 3 ? "price" : "own", lookback: 20, values: i < 3 ? line(20 * (i + 1), 1, 0) : line(14, 0, 50 + i),
                                         last: 1, last_text: "1.00" })),
  };
  await page.addInitScript((s) => { try { localStorage.setItem("atp.chart.studies", JSON.stringify(s)); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* */ } }, STUDIES);
  // in-page clocks: when each answer arrived, and when the chart showed it (attribute reached, then painted)
  await page.addInitScript((n) => {
    const w = window as unknown as { __rc: Record<string, number> };
    w.__rc = {};
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const res = await orig(input, init);
      const url = String(typeof input === "string" || input instanceof URL ? input : input.url);
      const watched = url.includes("/market-data/candles") ? "candles" : url.includes("/charts/studies") ? "studies" : null;
      if (watched) {
        // the clock starts when the whole body is in the browser (the mock's transfer is not the chart's time)
        const body = await res.arrayBuffer();
        if (watched === "studies" || !w.__rc.candles) w.__rc[watched] = performance.now();   // studies: the answer drawn is the last
        return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers });
      }
      return res;
    };
    const paintedAt = (key: string) => requestAnimationFrame(() => requestAnimationFrame(() => { w.__rc[key] = performance.now(); }));
    new MutationObserver(() => {
      if (!w.__rc.barsSeen && document.querySelector(`[data-testid="pro-chart"][data-bars="${n}"]`)) { w.__rc.barsSeen = 1; paintedAt("barsPainted"); }
      if (!w.__rc.drawnSeen && document.querySelector('[data-testid="studies-panel"][data-drawn="6"]')) { w.__rc.drawnSeen = 1; paintedAt("studiesPainted"); }
    }).observe(document, { subtree: true, attributes: true, childList: true, attributeFilter: ["data-bars", "data-drawn"] });
  }, N);
  await mockApi(page);
  await chartRoutes(page, { candles: () => b, studies: () => studies });
  await page.goto("/?chart=PERF&tf=5min&exchange=NSE");
  await expect(page.getByTestId("pro-chart").first()).toHaveAttribute("data-bars", String(N), { timeout: 20_000 });
  await expect(page.getByTestId("studies-panel")).toHaveAttribute("data-drawn", "6", { timeout: 20_000 });
  await page.waitForFunction(() => { const r = (window as unknown as { __rc: Record<string, number> }).__rc; return r.barsPainted && r.studiesPainted; });
  const rc = await page.evaluate(() => (window as unknown as { __rc: Record<string, number> }).__rc);
  const candlesMs = Math.round(rc.barsPainted - rc.candles);
  const studiesMs = Math.round(rc.studiesPainted - Math.max(rc.studies, rc.barsPainted));
  const shot = info.outputPath("render-50k-6-studies.png");
  await page.screenshot({ path: shot });
  await info.attach("render-50k-6-studies", { path: shot, contentType: "image/png" });
  const line1 = `render 50,000 candles: ${candlesMs} ms (budget ${Math.round(1000 * FACTOR)}); 6 study lines: ${studiesMs} ms (budget ${Math.round(1500 * FACTOR)}); `
              + `total ${candlesMs + studiesMs} ms; CI factor ${FACTOR.toFixed(2)}`;
  console.log(line1);
  if (process.env.GITHUB_ACTIONS === "true") console.log(`::notice title=CH6-2 render::${line1}`);
  expect(candlesMs, "50,000 candles").toBeLessThan(1000 * FACTOR);
  expect(studiesMs, "6 study lines").toBeLessThan(1500 * FACTOR);
});
