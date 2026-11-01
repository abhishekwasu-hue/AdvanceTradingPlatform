import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BROKER_PATH } from "../broker/paths";
import { adoptPathFor, testPathForIdea } from "../ideas/IdeaLibraryV2";
import { screenerPathFor } from "../ideas/model";
import { journalLink } from "../journal/recall";
import { DEFAULTS_PATH } from "../settings/tradingDefaults";
import { chartTabPath, chartsPath } from "../shell/shellRoutes";
import { paperBotPathFor, testPathFor } from "../strategyTemplates/link";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-5a (docs/design/M75_NO_NAVIGATION.md, the Signals / bots / ideas / journal / scanner rows): the 17
 * click-navigations open in place through the host. For each site, the destination its code builds is opened on the
 * tab the site lives on: the PATH stays, the panel registered for it is on top with the address's pre-fill carried
 * (a template's settings, an idea's scan, a journal week and row, a Watchtower proposal), and closing goes back to the
 * exact entry before. The Strategies flow hands its strategy over as state, exactly as its navigation did.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in terminalChartsNav.test.tsx). */
function history(start: string) {
  const at = (path: string, st: unknown): RouterLocation => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: st };
  };
  const entries: RouterLocation[] = [at(start, null)];
  let index = 0;
  const navigate = ((to: number | string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown }) => {
    if (typeof to === "number") { index = Math.max(0, Math.min(entries.length - 1, index + to)); return; }
    const next = typeof to === "string" ? at(to, opts?.state ?? null) : { ...to, state: opts?.state ?? null };
    if (opts?.replace) entries[index] = next;
    else { entries.splice(index + 1); entries.push(next); index += 1; }
  }) as NavigateFn;
  return { navigate, location: () => entries[index], url: () => { const l = entries[index]; return `${l.pathname}${l.search}${l.hash}`; } };
}

const ON: Record<string, boolean> = { screener_v2: true, discipline_engine: true, ai_copilot: true };
function inPlace(h: ReturnType<typeof history>, { enabled = true, flags = ON } = {}) {
  return createInPlaceApi({ enabled, on: (f) => flags[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
}

interface Site { site: string; from: string; to: () => string; kind: string; params?: Record<string, string>; state?: unknown }

const IDEA = { adopt: { available: true, template: "D1", timeframe: "1d", params: { period: 12 }, note: null } };
const q = (path: string) => path.slice(path.indexOf("?") + 1);

const SITES: Site[] = [
  { site: "ideas/IdeaLibraryV2.tsx:178 Try as a scan", from: "/ideas", to: () => screenerPathFor("T1", "up"), kind: "screener", params: { q: "idea=T1&scan=up" } },
  { site: "ideas/IdeaLibraryV2.tsx:198 Adopt as a paper strategy", from: "/ideas", to: () => adoptPathFor(IDEA as never)!, kind: "bots",
    params: { q: "template=D1&timeframe=1d&set.period=12" } },
  { site: "ideas/IdeaLibraryV2.tsx:203 Test it on past prices", from: "/ideas", to: () => testPathForIdea(IDEA as never)!, kind: "test",
    params: { q: "template=D1&timeframe=1d&set.period=12" } },
  { site: "journal/SimilarSetupsV2.tsx:30 Open (a similar setup)", from: "/signals", to: () => journalLink({ trade_id: 12, journal_week: "2026-W40" }), kind: "journal",
    params: { q: "week=2026-W40", h: "trade-12" } },
  { site: "pages/ScreenerPage.tsx:545 log in / connect", from: "/screener", to: () => "/broker", kind: "broker" },
  { site: "paperbot/BotPositionsV2.tsx:62 a bot's position", from: "/deployments", to: () => "/positions", kind: "positions" },
  { site: "paperbot/PaperBotV2.tsx:620 Connect your broker", from: "/deployments", to: () => BROKER_PATH, kind: "broker" },
  { site: "paperbot/PaperBotV2.tsx:663 Test it first", from: "/deployments", to: () => testPathFor({ template: "D2", timeframe: "15m", params: { fast: 9 } }), kind: "test",
    params: { q: "template=D2&timeframe=15m&set.fast=9" } },
  { site: "paperbot/PaperBotV2.tsx:799 Change my defaults", from: "/deployments", to: () => DEFAULTS_PATH, kind: "trading-defaults" },
  { site: "screener/ScanAlertPanel.tsx:131 Settings › Alerts", from: "/screener", to: () => "/settings/alerts", kind: "alerts" },
  { site: "signals/DayOneV2.tsx:130 Open Trading bot", from: "/signals", to: () => "/deployments", kind: "bots" },
  { site: "signals/DayOneV2.tsx:301 Start a trading bot with it", from: "/signals", to: () => paperBotPathFor({ template: "A5", timeframe: "1d", params: {} }), kind: "bots",
    params: { q: "template=A5&timeframe=1d" } },
  { site: "signals/SignalPartsV2.tsx:364 Open chart", from: "/signals", to: () => chartTabPath("RELIANCE", "NSE", "15min"), kind: "chart", params: { symbol: "RELIANCE", tf: "15min" } },
  { site: "signals/SignalsV2.tsx:137 Open the Watchtower", from: "/signals", to: () => "/copilot/watchtower?action=7", kind: "copilot", params: { tab: "watchtower", action: "7" } },
  { site: "signals/TryOnSymbolV2.tsx:151 Open chart", from: "/signals", to: () => chartsPath("TCS"), kind: "chart", params: { symbol: "TCS" } },
  { site: "strategyFlow/StrategyFlowV2.tsx:550 a template to the bot", from: "/strategies/build", to: () => paperBotPathFor({ template: "B3", timeframe: "15m", params: {} }),
    kind: "bots", params: { q: "template=B3&timeframe=15m" }, state: { flowStrategy: { strategyId: "template-pick:B3" } } },
  { site: "strategyFlow/StrategyFlowV2.tsx:554 a strategy to the bot", from: "/strategies/build", to: () => "/deployments", kind: "bots",
    state: { flowStrategy: { strategyId: "custom:4" } } },
];

describe("NAV-5a: every Signals / bots / ideas / journal / scanner button opens in place (the path stays)", () => {
  it("covers the 17 sites of the design table", () => expect(SITES).toHaveLength(17));

  for (const s of SITES) {
    it(s.site, () => {
      const h = history(s.from);
      const before = h.location();
      const api = inPlace(h);
      const to = s.to();
      expect(api.handles(to)).toBe(true);
      api.open(to, s.state === undefined ? undefined : { state: s.state });
      expect(h.location().pathname).toBe(s.from);                            // the URL path is unchanged
      const open = openPanels(h.location().search, DEFAULT_REGISTRY, (f) => ON[f] === true);
      expect(open.map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);                          // one history entry: Back closes it
      expect(handoffs(h.location().state)).toEqual(s.state === undefined ? [] : [s.state]);   // the flow's strategy rides along
      // The panel's own address is the full route the button went to before (search and hash), so the page reads it.
      const entry = DEFAULT_REGISTRY.get(s.kind)!;
      expect(DEFAULT_REGISTRY.resolve(entry.href(open[0].spec.params ?? {}))).toEqual(open[0].spec);
      if (s.params?.q) expect(q(entry.href(open[0].spec.params ?? {}))).toContain(s.params.q);
      api.close();
      expect(h.location()).toBe(before);                                     // the exact entry before (the tab's state)
    });
  }

  it("a journal row's hash survives the panel's address (the row opens)", () => {
    expect(DEFAULT_REGISTRY.get("journal")!.href({ q: "week=2026-W40", h: "trade-12" })).toBe("/positions/results/journal?week=2026-W40#trade-12");
  });

  it("a flag that is off navigates as before (Find setups without screener_v2, the journal without discipline_engine)", () => {
    const h = history("/ideas");
    inPlace(h, { flags: {} }).open(screenerPathFor("T1", "up"));
    expect(h.url()).toBe("/screener?idea=T1&scan=up");
    expect(inPlace(history("/signals"), { flags: {} }).handles(journalLink({ trade_id: 1, journal_week: null }))).toBe(false);
  });

  it("with ui_v2 off the bot link navigates with its template, as before", () => {
    const h = history("/signals");
    inPlace(h, { enabled: false }).open(paperBotPathFor({ template: "A5", timeframe: "1d", params: {} }));
    expect(h.url()).toBe("/deployments?template=A5&timeframe=1d");
  });
});

describe("NAV-5a: the sites use the host and the pages read the router's address (source)", () => {
  const uses: [string, RegExp][] = [
    ["ideas/IdeaLibraryV2.tsx", /<InPlaceLink key=\{s\.id\} to=\{screenerPathFor\(idea\.id, s\.id\)\}/],
    ["ideas/IdeaLibraryV2.tsx", /<InPlaceLink to=\{adoptPath\}/],
    ["ideas/IdeaLibraryV2.tsx", /<InPlaceLink to=\{testPath\}/],
    ["journal/SimilarSetupsV2.tsx", /<InPlaceLink to=\{journalLink\(m\)\}/],
    ["pages/ScreenerPage.tsx", /if \(action === "login" \|\| action === "connect"\) inPlace\.open\("\/broker"\);/],
    ["paperbot/BotPositionsV2.tsx", /<InPlaceLink key=\{p\.id\} to="\/positions"/],
    ["paperbot/PaperBotV2.tsx", /<InPlaceLink to=\{BROKER_PATH\}/],
    ["paperbot/PaperBotV2.tsx", /<InPlaceLink to=\{testPathFor\(/],
    ["paperbot/PaperBotV2.tsx", /<InPlaceLink to=\{DEFAULTS_PATH\}/],
    ["screener/ScanAlertPanel.tsx", /<InPlaceLink to="\/settings\/alerts"/],
    ["signals/DayOneV2.tsx", /<InPlaceLink to="\/deployments"/],
    ["signals/DayOneV2.tsx", /<InPlaceLink to=\{paperBotPathFor\(/],
    ["signals/SignalPartsV2.tsx", /<InPlaceLink to=\{chartTabPath\(item\.symbol, item\.exchange, chartTimeframe\)\}/],
    ["signals/SignalsV2.tsx", /<InPlaceLink to=\{`\/copilot\/watchtower\?action=\$\{proposal\}`\}/],
    ["signals/TryOnSymbolV2.tsx", /<InPlaceLink to=\{chartsPath\(signal\.symbol\)\}/],
    ["strategyFlow/StrategyFlowV2.tsx", /inPlace\.open\(paperBotPathFor\(\{ template: template\.id, timeframe: flow\.timeframe, params: \{\} \}\), \{ state \}\);/],
    ["strategyFlow/StrategyFlowV2.tsx", /inPlace\.open\(PAPER_PATH, \{ state \}\);/],
    ["paperbot/PaperBotV2.tsx", /initialTemplate=\{templateFromUrl\(location\.search\)\}/],
    ["pages/BacktestPage.tsx", /templateLinkFromSearch\(address\.search\)/],
    ["journal/JournalPageV2.tsx", /journalTarget\(here\.search, here\.hash\)/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));
});
