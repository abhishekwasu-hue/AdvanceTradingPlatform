import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { chartWindowUrl } from "../components/chartHelpers";
import { COPILOT_TABS, copilotPath, tabForAction } from "../copilot/tabs";
import { pathFor } from "../routes";
import { SETTINGS_PATH } from "../settings/paths";
import { askAiOptions } from "../shell/askAiEntry";
import { chartTabPath } from "../shell/shellRoutes";
import { signalsLinkFor } from "../signals/watchtower";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";
import { PanelRouter } from "./PanelRouter";

/**
 * M75 NAV-2 (docs/design/M75_NO_NAVIGATION.md, the Copilot rows): the 19 Copilot click-navigations open in place
 * through the host. For each site, the destination it opens (the same expression the site uses) is opened on the
 * Copilot's own address: the PATH stays the same, the panel registered for it is on top, what the button handed over
 * rides along, and closing goes back to the exact entry before. Clicking each button in a real browser (phone 390 and
 * desktop 1440) is e2e/copilot-inplace-v2.e2e.ts.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in inplace.test.tsx). */
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

const FLAGS: Record<string, boolean> = { ai_copilot: true, chart_analyze: true };
function inPlace(h: ReturnType<typeof history>, { enabled = true, flags = FLAGS, newTabs = [] as string[] } = {}) {
  return createInPlaceApi({ enabled, on: (f) => flags[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate,
    newTab: (p) => newTabs.push(p) });
}

afterEach(() => { delete (globalThis as { window?: unknown }).window; });

interface Site { site: string; from: string; to: () => string; kind: string; params?: Record<string, string>; state?: unknown; flags?: Record<string, boolean> }

/** The 19 sites of the design table, each with the destination its code builds. */
const SITES: Site[] = [
  { site: "copilot/chat/AnswerV2.tsx:150 (answer action)", from: "/copilot", to: () => "/strategies/build", kind: "strategy-build" },
  { site: "copilot/chat/AnswerV2.tsx:154 Open chart", from: "/copilot", to: () => chartTabPath("NIFTY 50", "NSE", "15min"), kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } },
  { site: "copilot/chat/ChartTurnV2.tsx:53", from: "/copilot", to: () => "/chart-analyze", kind: "analyze" },
  { site: "copilot/chat/CopilotChatV2.tsx:100 attach", from: "/copilot", to: () => "/copilot/analyze", kind: "analyze" },
  { site: "copilot/chat/CopilotChatV2.tsx:173 AI on", from: "/copilot", to: () => SETTINGS_PATH.ai, kind: "ai-key", flags: { ...FLAGS, ai_one_key: true } },
  { site: "copilot/chat/CopilotChatV2.tsx:190 Connect", from: "/copilot", to: () => SETTINGS_PATH.ai, kind: "ai-key" },
  { site: "copilot/chat/CopilotChatV2.tsx:267 (job link)", from: "/copilot", to: () => "/signals", kind: "signals" },
  { site: "copilot/chat/CopilotChatV2.tsx:278 Advanced tools", from: "/copilot", to: () => copilotPath("market-pulse"), kind: "copilot", params: { tab: "market-pulse" } },
  { site: "copilot/tabs/AskCopilotTab.tsx:92 guide", from: "/copilot/ask", to: () => pathFor("coach"), kind: "copilot", params: { tab: "coach" } },
  { site: "copilot/tabs/AskCopilotTab.tsx:95 tool", from: "/copilot/ask", to: () => copilotPath(tabForAction("interview")), kind: "copilot", params: { tab: "idea-builder" }, state: { prompt: "a breakout idea" } },
  { site: "copilot/tabs/IdeaBuilderTab.tsx:69 builder", from: "/copilot/idea-builder", to: () => "/strategies/build", kind: "strategy-build", state: { idea: "a breakout idea" } },
  { site: "copilot/tabs/IdeaBuilderTab.tsx:344 draft", from: "/copilot/idea-builder", to: () => copilotPath("strategy-lab"), kind: "copilot", params: { tab: "strategy-lab" }, state: { draftId: 7 } },
  { site: "copilot/tabs/IdeaBuilderTab.tsx:473 Open chart", from: "/copilot/idea-builder", to: () => chartWindowUrl("NIFTY 50", "5min", "NSE", "upstox"), kind: "chart", params: { symbol: "NIFTY 50", tf: "5min", exchange: "NSE" } },
  { site: "copilot/tabs/NewsRadarTab.tsx:95", from: "/copilot/news-radar", to: () => pathFor("news-events"), kind: "news" },
  { site: "copilot/tabs/WatchtowerTab.tsx:80", from: "/copilot/watchtower", to: () => signalsLinkFor([{ id: 7 }]), kind: "signals", params: { proposal: "7" } },
  { site: "copilot/tabs/lab/DraftsPanel.tsx:73", from: "/copilot/strategy-lab", to: () => copilotPath("idea-builder"), kind: "copilot", params: { tab: "idea-builder" }, state: { prompt: "my rules" } },
  { site: "shell/AskAiSheetV2.tsx:67 (sheet row)", from: "/charts/NIFTY", to: () => askAiOptions({ symbol: "NIFTY" }, () => true, "charts").find((o) => o.id === "copilot")!.to!, kind: "copilot", params: { symbol: "NIFTY" } },
  { site: "shell/askAiEntry.tsx:99 Analyze", from: "/charts/NIFTY", to: () => askAiOptions(null, () => true, "charts").find((o) => o.id === "analyze")!.to!, kind: "analyze" },
  { site: "shell/askAiEntry.tsx:102 Copilot", from: "/terminal", to: () => askAiOptions({ symbol: "BANKNIFTY" }, () => true, "terminal").find((o) => o.id === "copilot")!.to!, kind: "copilot", params: { symbol: "BANKNIFTY" } },
];

describe("NAV-2: every Copilot button opens in place (the path stays)", () => {
  it("covers the 19 sites of the design table", () => {
    expect(SITES).toHaveLength(19);
  });

  for (const s of SITES) {
    it(s.site, () => {
      (globalThis as { window?: unknown }).window = { location: { pathname: s.from } };   // chartWindowUrl reads it
      const h = history(s.from);
      const before = h.location();
      const api = inPlace(h, { flags: s.flags ?? FLAGS });
      const to = s.to();
      expect(api.handles(to)).toBe(true);
      api.open(to, s.state === undefined ? undefined : { state: s.state });
      expect(h.location().pathname).toBe(s.from);                           // the URL path is unchanged
      const open = openPanels(h.location().search, DEFAULT_REGISTRY, (f) => (s.flags ?? FLAGS)[f] === true);
      expect(open.map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);                          // one history entry: Back closes it
      expect(handoffs(h.location().state)).toEqual(s.state === undefined ? [] : [s.state]);     // what the button handed over rides along
      api.close();
      expect(h.location()).toBe(before);                                     // the exact entry before (the tab's state)
    });
  }
});

describe("NAV-2: as before where no panel shows it", () => {
  it("an answer's draft action: the Trading bot page in place since NAV-3 (its panel), the draft handed to it", () => {
    const h = history("/copilot");
    const draft = { copilotDraft: { mode: "PAPER" } };
    inPlace(h).open("/deployments", { state: draft });
    expect(h.url()).toBe("/copilot?panel=bots");
    expect(handoffs(h.location().state)).toEqual([draft]);
  });

  it("an address no panel shows navigates with its state, as today", () => {
    const h = history("/copilot");
    inPlace(h).open("/journal", { state: { x: 1 } });
    expect(h.url()).toBe("/journal");
    expect(h.location().state).toEqual({ x: 1 });
  });

  it("with ui_v2 off a hand-off navigates with its state and Open chart opens a new browser tab, as before", () => {
    (globalThis as { window?: unknown }).window = { location: { pathname: "/ai-copilot/idea-builder" } };
    const h = history("/ai-copilot/idea-builder");
    const newTabs: string[] = [];
    const api = inPlace(h, { enabled: false, newTabs });
    api.open(chartWindowUrl("NIFTY 50", "5min", "NSE"), { newTab: true });
    expect(newTabs).toEqual(["/ai-copilot/idea-builder?chart=NIFTY+50&tf=5min&exchange=NSE"]);
    expect(h.url()).toBe("/ai-copilot/idea-builder");
    api.open(copilotPath("strategy-lab"), { state: { draftId: 7 } });
    expect(h.url()).toBe("/copilot/strategy-lab");
    expect(h.location().state).toEqual({ draftId: 7 });
  });

  it("the Copilot panels need their feature: off, the button navigates as today", () => {
    const h = history("/charts/NIFTY");
    inPlace(h, { flags: {} }).open("/copilot?symbol=NIFTY");
    expect(h.url()).toBe("/copilot?symbol=NIFTY");
    expect(inPlace(history("/copilot"), { flags: {} }).handles("/chart-analyze")).toBe(false);
  });

  it("the Copilot's own tab bar addresses that are not tools stay with their routes", () => {
    expect(DEFAULT_REGISTRY.resolve("/copilot/signals")).toBeNull();      // forwarded to the Signals page
    expect(DEFAULT_REGISTRY.resolve("/copilot/unknown")).toBeNull();
    for (const tab of COPILOT_TABS) expect(DEFAULT_REGISTRY.resolve(copilotPath(tab))).toEqual({ kind: "copilot", params: { tab } });
  });

  it("each new panel's full route resolves back to the same panel (its href is the real address)", () => {
    const specs: { kind: string; params: Record<string, string> }[] = [{ kind: "copilot", params: { tab: "strategy-lab" } }, { kind: "copilot", params: { symbol: "NIFTY" } }, { kind: "analyze", params: {} },
      { kind: "strategy-build", params: {} }, { kind: "news", params: {} }, { kind: "signals", params: { proposal: "7" } }];
    for (const spec of specs) {
      expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get(spec.kind)!.href(spec.params))).toEqual(spec);
    }
  });
});

function Where() {
  const l = useLocation();
  return <p data-where={`${l.pathname}${l.search}`} data-state={JSON.stringify(l.state)} />;
}

describe("NAV-2: a panel's page has its own address", () => {
  it("reads its own path, search and the handed-over state (not the tab's), inside the tab's router", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/copilot/idea-builder?panel=strategy-build"]}>
        <Routes><Route path="*" element={<>
          <Where />
          <PanelRouter path="/signals?proposal=7" state={{ idea: "x" }} onLeave={() => undefined}><Where /></PanelRouter>
        </>} /></Routes>
      </MemoryRouter>,
    );
    expect(html).toContain('data-where="/copilot/idea-builder?panel=strategy-build"');
    expect(html).toContain('data-where="/signals?proposal=7" data-state="{&quot;idea&quot;:&quot;x&quot;}"');
  });
});

describe("NAV-2: the sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["copilot/chat/AnswerV2.tsx", /<InPlaceLink to=\{answer\.action\.to\} state=\{actionState\(answer\)\}/],
    ["copilot/chat/AnswerV2.tsx", /<InPlaceLink to=\{chartTabPath\(chart\.symbol/],
    ["copilot/chat/ChartTurnV2.tsx", /<InPlaceLink to="\/chart-analyze"/],
    ["copilot/chat/CopilotChatV2.tsx", /<InPlaceLink to="\/copilot\/analyze"/],
    ["copilot/chat/CopilotChatV2.tsx", /<InPlaceLink to=\{home\.ai\.settings \?\? SETTINGS_PATH\.ai\} className=\{SMALL_CHIP\} data-ai-state="on">/],
    ["copilot/chat/CopilotChatV2.tsx", /<InPlaceLink to=\{home\.ai\.settings \?\? SETTINGS_PATH\.ai\} className=\{SMALL_CHIP\} data-connect>/],
    ["copilot/chat/CopilotChatV2.tsx", /<InPlaceLink to=\{link\.to\}/],
    ["copilot/chat/CopilotChatV2.tsx", /<InPlaceLink to=\{copilotPath\(tab\)\}/],
    ["copilot/tabs/AskCopilotTab.tsx", /inPlace\.open\(pathFor\("coach"\)\)/],
    ["copilot/tabs/AskCopilotTab.tsx", /inPlace\.open\(copilotPath\(tabForAction\(reply\.action\.tab\)\), reply\.prompt \? \{ state: \{ prompt: reply\.prompt \} \}/],
    ["copilot/tabs/IdeaBuilderTab.tsx", /inPlace\.open\("\/strategies\/build", idea \? \{ state: \{ idea \} \}/],
    ["copilot/tabs/IdeaBuilderTab.tsx", /inPlace\.open\(copilotPath\("strategy-lab"\), \{ state: \{ draftId \} \}\)/],
    ["copilot/tabs/IdeaBuilderTab.tsx", /inPlace\.open\(chartWindowUrl\(symbol, "5min", "NSE", broker\), \{ newTab: true \}\)/],
    ["copilot/tabs/NewsRadarTab.tsx", /<InPlaceLink to=\{pathFor\("news-events"\)\}/],
    ["copilot/tabs/WatchtowerTab.tsx", /<InPlaceLink to=\{signalsLinkFor\(entries\)\}/],
    ["copilot/tabs/lab/DraftsPanel.tsx", /inPlace\.open\(copilotPath\("idea-builder"\), \{ state: \{ prompt \} \}\)/],
    ["shell/AskAiSheetV2.tsx", /<InPlaceLink key=\{o\.id\} to=\{o\.to\} onClick=\{onClose\}/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));

  it("the Copilot chat's composer sits inside a panel (never fixed to the bottom of the screen there)", () => {
    expect(read("copilot/chat/CopilotChatV2.tsx")).toMatch(/return inPanel \? "inline" : placement;/);
  });
});
