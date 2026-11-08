import type { Page } from "@playwright/test";
import type { Director } from "./director";
import { settle, videoWorld, type WorldOptions } from "./videoWorld";

/**
 * M92 V2: the 19 scenes of the "Find setups" series (docs/video/EPISODES.md; the outline's scene numbers). Each scene
 * opens its own made-up world and takes one still per beat; the beat ids are the `shot` ids of the script lines
 * (docs/video/scripts/E*.json). The drawings (scenes 1, 2, 13, 14, 19) are cards drawn over the page.
 */

export const EPISODES: Record<string, number[]> = {
  E1: [1, 2, 3, 4, 5],
  E2: [6, 7],
  E3: [8, 9, 10],
  E4: [11, 12, 13],
  E5: [15, 16, 17],
  E6: [14, 18, 19],
};

async function open(d: Director, path: string, opts: WorldOptions = {}): Promise<void> {
  const world = await videoWorld(d.page, opts);
  d.unmocked = world.unmocked;
  await d.page.goto(path);
  await settle(d.page);
}

/** The Find setups page, ready (the hero cards shown). */
async function findSetups(d: Director, opts: WorldOptions = {}): Promise<Page> {
  await open(d, "/screener", opts);
  await d.page.getByTestId("hero-list").waitFor({ timeout: 15_000 });
  return d.page;
}

/** The Today page shows index levels: blurred while the menu is the subject, so no name stands next to a number. */
async function blurMain(page: Page): Promise<void> {
  await page.evaluate(() => {
    const s = document.createElement("style");
    s.id = "atp-video-blur";
    s.textContent = "main{filter:blur(9px) !important}";
    document.head.appendChild(s);
  });
}
async function unblurMain(page: Page): Promise<void> {
  await page.evaluate(() => document.getElementById("atp-video-blur")?.remove());
}

/** Run: the header button on a computer, the bottom bar's on a phone. */
const runButton = (d: Director) => d.page.getByTestId(d.phone ? "run-scan-mobile" : "run-scan");
const runWhy = (d: Director) => d.page.getByTestId(d.phone ? "run-why-mobile" : "run-why");

async function template(d: Director, name: string, shot?: string): Promise<void> {
  const page = d.page;
  await page.getByRole("tablist", { name: "Start a scan" }).getByRole("tab", { name: "Template", exact: true }).click();
  const button = page.getByRole("button", { name }).first();
  if (shot) await d.click(shot, button); else await button.click();
  await page.getByTestId("conditions-toggle").first().waitFor();
}

async function run(d: Director, shot?: string): Promise<void> {
  // Run waits for the conditions to be checked (greyed out until then).
  const button = runButton(d);
  for (let i = 0; i < 30 && !(await button.isEnabled().catch(() => false)); i++) await d.page.waitForTimeout(500);
  if (shot) await d.click(shot, runButton(d)); else await runButton(d).click();
  await d.page.getByTestId("results-matched").first().waitFor({ timeout: 30_000 }).catch(() => undefined);
  await settle(d.page);
}

/** Picks the kind of block ("Indicator", "Filter", "Category", "Rank", "Group") and adds one. */
async function addBlock(d: Director, kind: string, shot?: string): Promise<void> {
  const page = d.page;
  const picker = page.getByRole("combobox", { name: "Kind of condition to add" }).first();
  await picker.click();
  await page.locator("[data-combobox-list] [role='option']", { hasText: kind }).first().click();
  const add = page.getByRole("button", { name: "Add a condition" }).first();
  if (shot) await d.click(shot, add); else await add.click();
}

const SECTIONS: [string, string, number][] = [
  ["A", "Price and volume of a candle", 6], ["B", "Moving averages", 6], ["C", "Momentum oscillators", 10],
  ["D", "Trend direction and strength", 13], ["E", "Volatility and bands", 10], ["F", "Volume measures", 3],
  ["G", "Maths and history helpers", 16], ["H", "Ranks across your list", 3], ["I", "Session and intraday levels", 9],
  ["J", "Market structure, swings and levels", 15], ["K", "Candle patterns", 3], ["L", "RSI divergence", 2],
  ["M", "Option chain and futures", 4], ["N", "Breakouts (yes / no and labels)", 22], ["O", "Breakout levels", 11],
  ["P", "Market zones", 4], ["Q", "Wave count", 16], ["R", "Company data, ownership, events", 16],
];

/** Made-up candles: closed ones in solid colour, the forming one dashed. No axis, no numbers. */
const CANDLES = (() => {
  const c = [[60, 40, 70, 30], [42, 55, 62, 36], [55, 48, 66, 44], [48, 62, 70, 45], [62, 74, 80, 58], [74, 68, 82, 64], [68, 80, 88, 66]];
  const y = (v: number) => 260 - v * 2.6;
  const bars = c.map(([o, cl, h, l], i) => {
    const x = 40 + i * 70, up = cl >= o, col = up ? "#22c55e" : "#ef4444", last = i === c.length - 1;
    return `<line x1="${x + 15}" x2="${x + 15}" y1="${y(h)}" y2="${y(l)}" stroke="${last ? "#94a3b8" : col}" stroke-width="3"/>
      <rect x="${x}" y="${y(Math.max(o, cl))}" width="30" height="${Math.abs(y(o) - y(cl))}" fill="${last ? "none" : col}"
        stroke="${last ? "#94a3b8" : col}" stroke-width="3" ${last ? "stroke-dasharray=\"6 5\"" : ""}/>`;
  }).join("");
  return `<svg viewBox="0 0 540 280" style="width:70%;display:block;margin:2vmin auto">${bars}
    <text x="40" y="275" fill="#cbd5e1" font-size="16">closed candles: final, never change</text>
    <text x="440" y="275" fill="#94a3b8" font-size="16">forming</text></svg>`;
})();

const STEP = (n: number, title: string, sub: string) => `<div class="step"><b>${n}. ${title}</b><span>${sub}</span></div>`;

export const SCENES: Record<number, (d: Director) => Promise<void>> = {
  // 1 - what a screener is: the five-step flow, then the funnel
  1: async (d) => {
    await open(d, "/screener");
    await d.card("s01.flow", `<h1>Find setups</h1><div class="flow">${[
      STEP(1, "Pick a list", "an index, a sector, your watchlist"),
      STEP(2, "Pick a chart", "Daily, Weekly, 15 min ..."),
      STEP(3, "Pick a scan", "ready scan, template or your own"),
      STEP(4, "Run", "closed candles, each stock"),
      STEP(5, "Read results", "Matched N of M")].join("")}</div>`);
    await d.card("s01.funnel", `<h2>What happens when you press Run</h2><div class="flow" style="flex-direction:column;max-width:70%">
      <div class="step"><b>Your list: 50 stocks</b><span>closed candles of each one are read</span></div>
      <div class="step" style="margin:0 6%"><b>Condition 1: 18 still in</b><span>32 did not meet it</span></div>
      <div class="step" style="margin:0 14%"><b>Condition 2: 6 still in</b><span>12 more did not meet it</span></div>
      <div class="step yes" style="margin:0 22%"><b>Matched 6 of 50</b><span>every condition passed</span></div></div>
      <p class="note">Made-up counts.</p>`);
  },
  // 2 - what it does NOT do: the red box
  2: async (d) => {
    await open(d, "/screener");
    await d.card("s02.red", `<h1>What Find setups does NOT do</h1><div class="red"><ul>
      <li>A match is information: not a signal to trade and not advice.</li>
      <li>It does not rank stocks as good or bad, and says nothing about what price does next.</li>
      <li>It never places, changes or cancels an order. An alert only tells you.</li>
      <li>It adds no hidden rule: every condition is on screen.</li>
      <li>It decides on closed candles only.</li></ul></div>`);
  },
  // 3 - sign in (nothing is typed: no email on screen)
  3: async (d) => {
    await open(d, "/", { signedOut: true });
    const page = d.page;
    await page.locator("[data-screen='sign-in']").waitFor({ timeout: 15_000 });
    await d.shot("s03.screen", page.getByRole("heading", { name: "Sign in" }), { cursor: false });
    await d.step("s03.email", () => d.shot("s03.email", page.getByLabel("Email")));
    await d.step("s03.password", () => d.shot("s03.password", page.getByLabel("Password", { exact: true })));
    await d.step("s03.submit", () => d.shot("s03.submit", page.getByRole("button", { name: "Sign in", exact: true })));
    await d.step("s03.help", () => d.shot("s03.help", page.getByText(/Forgot password/).first()));
  },
  // 4 - open Find setups: the left menu on a computer, the bottom bar / More search on a phone
  4: async (d) => {
    await open(d, "/");
    const page = d.page;
    await blurMain(page);
    if (!d.phone) {
      const link = page.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Find setups" });
      await d.shot("s04.menu", link);
      await d.shot("s04.more", link);
      await d.click("s04.search", link);
    } else {
      const bar = page.locator("[data-bottom-nav-v2]");
      const inBar = bar.getByRole("link", { name: "Find setups" });
      // The bottom bar holds the pages switched on for this account; Find setups may be there or under More.
      if (await inBar.count()) await d.shot("s04.menu", inBar);
      else await d.shot("s04.menu", bar, { cursor: false });
      await d.step("s04.more", async () => {
        await d.click("s04.more", page.getByRole("button", { name: "More pages" }));
        const sheet = page.getByRole("dialog", { name: "All pages" });
        await sheet.getByRole("searchbox", { name: "Search pages" }).fill("scan");
        await d.click("s04.search", sheet.getByRole("link", { name: /Find setups/ }).first());
      });
      if (!(await page.getByTestId("hero-list").isVisible().catch(() => false))) await page.goto("/screener");
    }
    await unblurMain(page);
    await settle(page);
    await page.getByTestId("hero-list").waitFor({ timeout: 15_000 });
    await d.shot("s04.page", page.getByRole("heading", { name: "Find setups" }).first(), { cursor: false });
  },
  // 5 - the page tour: the two cards, Start a scan, Conditions, Results, More tools
  5: async (d) => {
    const page = await findSetups(d);
    await d.shot("s05.overview");
    await d.shot("s05.list", page.getByTestId("hero-list"));
    await d.shot("s05.chart", page.getByTestId("hero-chart"));
    await d.shot("s05.start", page.getByRole("tablist", { name: "Start a scan" }));
    await d.step("s05.conditions", () => d.shot("s05.conditions", page.getByTestId("conditions-toggle").first()));
    await d.step("s05.results", () => d.shot("s05.results", page.getByTestId("results-empty")));
    await d.step("s05.more", async () => {
      await d.click("s05.more", page.getByTestId("screener-more"));
      await d.shot("s05.tools", page.getByTestId("screener-more-list"), { cursor: false });
    });
  },
  // 6 - pick a list (M90 shipped: no fixed 50; the whole list in one run with "N / M done")
  6: async (d) => {
    const page = await findSetups(d);
    await d.click("s06.change", page.getByTestId("list-change"));
    const select = page.getByTestId("universe-select");
    await d.shot("s06.panel", select);
    await d.step("s06.groups", async () => {
      await select.click();
      await d.shot("s06.groups", page.locator("[data-combobox-list]").first(), { cursor: false });
      await d.click("s06.pick", page.locator("[data-combobox-list] [role='option'][data-value='sys:index-nifty500']").first());
    });
    await d.step("s06.big", () => d.shot("s06.big", page.getByTestId("universe-batches").or(page.getByTestId("hero-list")).first()));
    await d.step("s06.done", () => d.click("s06.done", page.getByTestId("list-done")));
    await d.shot("s06.list", page.getByTestId("hero-list"));
    await d.step("s06.progress", async () => {
      await page.getByTestId("ready-scan-run").first().click();
      const progress = page.getByTestId("scan-progress").first();
      await progress.waitFor({ timeout: 10_000 });
      await d.shot("s06.progress", progress);
      await page.getByTestId("results-matched").first().waitFor({ timeout: 60_000 });
      await d.shot("s06.result", page.getByTestId("results-matched").first());
    });
  },
  // 7 - pick a chart: Daily ... 1 min; closed vs forming candle
  7: async (d) => {
    const page = await findSetups(d);
    await d.click("s07.change", page.getByTestId("chart-change"));
    const tf = page.locator("#screener-tf");
    await d.step("s07.options", async () => {
      await tf.click();
      await d.shot("s07.options", page.locator("[data-combobox-list]").first(), { cursor: false });
      await page.keyboard.press("Escape");
    });
    await d.shot("s07.daily", tf);
    await d.card("s07.candles", `<h1>Closed candle and forming candle</h1>${CANDLES}<p class="note">Made-up candles.</p>`);
    await d.step("s07.done", () => d.click("s07.done", page.getByTestId("list-done")));
    await d.shot("s07.chart", page.getByTestId("hero-chart"));
  },
  // 8 - start a scan: Ready scan / Template / Own words; the screen library
  8: async (d) => {
    const page = await findSetups(d);
    const tabs = page.getByRole("tablist", { name: "Start a scan" });
    await d.shot("s08.tabs", tabs);
    await d.shot("s08.ready", page.getByTestId("ready-scans"));
    await d.click("s08.template", tabs.getByRole("tab", { name: "Template", exact: true }));
    await d.shot("s08.three", page.getByRole("button", { name: "Momentum" }).first());
    await d.step("s08.words", () => d.shot("s08.words", tabs.getByRole("tab", { name: "Own words", exact: true })));
    await d.step("s08.library", async () => {
      await tabs.getByRole("tab", { name: "Ready scan", exact: true }).click();
      await d.click("s08.seeall", page.getByTestId("ready-see-all"));
      await settle(page);
      await d.shot("s08.library");
    });
  },
  // 9 - build blocks: All of these must pass; underlined parts; Indicator / Filter / Category / Rank / Group
  9: async (d) => {
    const page = await findSetups(d);
    await template(d, "Momentum");
    await d.step("s09.all", () => d.shot("s09.all", page.getByTestId("all-label").first()));
    await d.step("s09.row", () => d.shot("s09.row", page.getByTestId("stage-row").first()));
    await d.step("s09.kinds", async () => {
      const picker = page.getByRole("combobox", { name: "Kind of condition to add" }).first();
      await d.click("s09.kind", picker);
      await d.shot("s09.kinds", page.locator("[data-combobox-list]").first(), { cursor: false });
      await page.keyboard.press("Escape");
    });
    await d.step("s09.add", async () => {
      await d.click("s09.add", page.getByRole("button", { name: "Add a condition" }).first());
      await d.shot("s09.new", page.getByTestId("stage-row").last());
    });
  },
  // 10 - bars ago and the timeframe chip (the row's advanced options)
  10: async (d) => {
    const page = await findSetups(d);
    await template(d, "Momentum");
    const row = page.getByTestId("stage-row").first();
    await d.step("s10.chip", () => d.shot("s10.chip", row.getByRole("combobox", { name: /Timeframe of the left side/ }).first()));
    await d.step("s10.open", () => d.click("s10.open", row.getByRole("button", { name: "Advanced options for stage 1" })));
    await d.step("s10.field", async () => {
      const field = row.getByTestId("bars-ago-left").getByRole("textbox").first();
      await d.shot("s10.field", field);
      await field.fill("3");
      await field.press("Enter");
      await d.shot("s10.three", row);
    });
  },
  // 11 - run: why Run is greyed out; the run
  11: async (d) => {
    const page = await findSetups(d);
    await d.step("s11.why", () => d.shot("s11.why", runWhy(d)));
    await template(d, "Momentum");
    await d.shot("s11.ready", runButton(d));
    await run(d, "s11.run");
    await d.shot("s11.done", page.getByTestId("results-matched").first());
  },
  // 12 - read the results: Matched N of M, Ran at, the table, Show on chart
  12: async (d) => {
    const page = await findSetups(d);
    await template(d, "Momentum");
    await run(d);
    await d.shot("s12.header", page.getByTestId("results-matched").first());
    await d.step("s12.pill", () => d.shot("s12.pill", page.getByRole("status").filter({ hasText: "Ran at" }).first()));
    await d.step("s12.table", () => d.shot("s12.table", page.getByRole("table").first()));
    await d.step("s12.hidden", () => d.shot("s12.hidden", page.getByTestId("results-hidden").first()));
    await d.step("s12.chart", () => d.shot("s12.chart", page.getByTestId("show-on-chart").first()));
  },
  // 13 - where the data comes from (M86 shipped: end-of-day for users without a broker)
  13: async (d) => {
    const page = await findSetups(d);
    await d.card("s13.card", `<h1>Where the data comes from</h1><div class="flow">
      <div class="step yes"><b>Your own broker connected</b><span>Every chart, 1 min to Monthly. Paced inside your broker's limits.</span></div>
      <div class="step no"><b>No broker</b><span>Daily, Weekly, Monthly from ATP's stored end-of-day history (Last close &lt;date&gt;). 1 min to 1 hour: connect your broker.</span></div>
      </div><p class="note">No shared live data: live data comes only from your own broker.</p>`);
    await d.step("s13.banner", () => d.shot("s13.banner", page.getByText(/end-of-day/i).first()));
    await template(d, "Momentum");
    await run(d);
    await d.step("s13.pill", () => d.shot("s13.pill", page.getByRole("status").filter({ hasText: "Ran at" }).first()));
  },
  // 14 - function families: the groups A to R with their counts
  14: async (d) => {
    await findSetups(d);
    await d.card("s14.groups", `<h2>169 building blocks in 18 groups</h2><div class="grid">${SECTIONS.map(([k, name, n]) =>
      `<div>${k}. ${name}<i>${n}</i></div>`).join("")}</div><p class="note">Some groups show only when switched on.</p>`);
    await d.step("s14.kinds", async () => {
      const page = d.page;
      await template(d, "Momentum");
      await page.getByTestId("stage-row").first().getByRole("combobox", { name: "Left indicator" }).click();
      await d.shot("s14.list", page.locator("[data-combobox-list]").first(), { cursor: false });
      await page.keyboard.press("Escape");
    });
  },
  // 15 - example 1: the Momentum template on NIFTY 50, Daily
  15: async (d) => {
    const page = await findSetups(d);
    await d.shot("s15.list", page.getByTestId("hero-list"));
    await template(d, "Momentum", "s15.template");
    await d.shot("s15.conds", page.getByTestId("conditions").first(), { cursor: false });
    await run(d, "s15.run");
    await d.shot("s15.result", page.getByTestId("results-matched").first());
    await d.step("s15.chart", () => d.shot("s15.chart", page.getByTestId("show-on-chart").first()));
  },
  // 16 - example 2: build your own (three blocks); one stage removes every symbol
  16: async (d) => {
    const page = await findSetups(d);
    await d.step("s16.list", async () => {
      await page.getByTestId("list-change").click();
      await page.getByTestId("universe-select").click();
      await page.locator("[data-combobox-list] [role='option'][data-value='sys:fno-stocks']").first().click();
      await page.getByTestId("list-done").click();
      await d.shot("s16.list", page.getByTestId("hero-list"));
    });
    await d.step("s16.blank", async () => {
      await page.getByRole("tablist", { name: "Start a scan" }).getByRole("tab", { name: "Template", exact: true }).click();
      await d.click("s16.blank", page.getByRole("button", { name: /Blank: I add conditions/ }).first());
    });
    await d.step("s16.category", () => addBlock(d, "Category", "s16.category"));
    await d.step("s16.filter", () => addBlock(d, "Filter", "s16.filter"));
    await d.step("s16.pattern", () => addBlock(d, "Filter", "s16.pattern"));
    await d.shot("s16.conds", page.getByTestId("conditions").first(), { cursor: false });
    await d.step("s16.run", () => run(d, "s16.run"));
    await d.step("s16.empty", () => d.shot("s16.empty", page.getByText(/removes every symbol/).first()));
  },
  // 17 - example 3: a group ("Any of these"), then "Tell me when it matches"
  17: async (d) => {
    const page = await findSetups(d);
    await template(d, "Momentum");
    await d.step("s17.group", async () => {
      await addBlock(d, "Group", "s17.group");
      await d.shot("s17.any", page.getByTestId("stage-row").last());
    });
    await d.step("s17.run", () => run(d));
    await d.step("s17.open", () => d.click("s17.open", page.getByTestId("scan-alert-open")));
    await d.step("s17.dialog", async () => {
      const dialog = page.getByRole("dialog", { name: "Tell me when it matches" });
      await d.shot("s17.dialog", dialog, { cursor: false });
      await d.click("s17.create", dialog.getByTestId("scan-alert-create"));
      await d.shot("s17.done", dialog.getByTestId("scan-alert-done"));
    });
  },
  // 18 - troubleshooting: one button per reason
  18: async (d) => {
    const page = await findSetups(d);
    await d.step("s18.why", () => d.shot("s18.why", runWhy(d)));
    await d.card("s18.table", `<h2>Messages and what to do</h2><div class="grid" style="grid-template-columns:1fr">
      <div>Your broker login has ended.<i>Log in</i></div>
      <div>Daily history for this stock is not stored yet.<i>Log in</i></div>
      <div>Connect your broker for intraday bars.<i>Log in</i></div>
      <div>The broker's request limit was reached.<i>Retry</i></div>
      <div>The broker could not be reached.<i>Retry</i></div>
      <div>No closed bars came back for this timeframe.<i>Try another timeframe</i></div>
      <div>Too little history for its indicators.<i>Try another timeframe</i></div>
      <div>Matched 0 of 50: no stock met all the conditions.<i>Loosen or switch off one</i></div></div>`);
    void page;
  },
  // 19 - close
  19: async (d) => {
    await findSetups(d);
    await d.card("s19.close", `<h1>Conditions on data</h1><p>For your own study and your own decision.</p>
      <div class="red"><ul><li>A match is information, not a signal and not advice.</li>
      <li>Scans and alerts never place, change or cancel an order.</li></ul></div>`);
  },
};
