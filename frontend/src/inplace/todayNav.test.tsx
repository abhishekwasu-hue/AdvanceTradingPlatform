import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BROKER_PATH } from "../broker/paths";
import { CardV2, EmptyStateV2 } from "../dashboard/CardV2";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-6a (docs/design/M75_NO_NAVIGATION.md, the Today cards and attention rows): the 16 click-navigations on Today
 * open in place: the two shared renderers (CardV2's "more", EmptyStateV2's action) and the attention list draw
 * <InPlaceLink>, so every destination handed to them opens the panel registered for it over Today - the PATH stays,
 * nothing is handed over, and closing goes back to the exact entry before. An address no panel shows still navigates.
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

const ON: Record<string, boolean> = { screener_v2: true, markets_view: true };
function inPlace(h: ReturnType<typeof history>, flags = ON) {
  return createInPlaceApi({ enabled: true, on: (f) => flags[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
}

/** Each site's destination (the renderers' two rows carry a destination from data: one representative each). */
const SITES: { site: string; to: string; kind: string; params?: Record<string, string> }[] = [
  { site: "dashboard/BlocksV2.tsx:25 Open positions › View all", to: "/positions", kind: "positions" },
  { site: "dashboard/BlocksV2.tsx:28 Find setups (empty)", to: "/screener", kind: "screener" },
  { site: "dashboard/BlocksV2.tsx:66 Active signals › View all", to: "/signals", kind: "signals" },
  { site: "dashboard/BlocksV2.tsx:69 Watch a list on Signals", to: "/signals", kind: "signals" },
  { site: "dashboard/BlocksV2.tsx:70 Set up a scan", to: "/screener/custom", kind: "custom-scan" },
  { site: "dashboard/BlocksV2.tsx:134 Performance", to: "/analytics", kind: "performance" },
  { site: "dashboard/CardV2.tsx:42 more (renderer)", to: "/positions/orders", kind: "orders" },
  { site: "dashboard/CardV2.tsx:60 action (renderer)", to: "/settings/risk-limits", kind: "risk-limits" },
  { site: "dashboard/DashboardV2.tsx:132 Connect your broker", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "dashboard/MyDayCardV2.tsx:16 My positions", to: "/positions", kind: "positions" },
  { site: "dashboard/NeedsAttentionV2.tsx:106 action (renderer)", to: "/settings/alerts", kind: "alerts" },
  { site: "dashboard/WeekTilesV2.tsx:44 Sectors", to: "/markets?period=week", kind: "markets", params: { q: "period=week" } },
  { site: "dashboard/WeekTilesV2.tsx:47 a week tile", to: "/markets?period=week", kind: "markets", params: { q: "period=week" } },
  { site: "dashboard/model.ts:442 Brokers", to: BROKER_PATH, kind: "broker" },
  { site: "dashboard/model.ts:489 Risk limits (the Risk status page)", to: "/risk-management", kind: "risk" },
  { site: "dashboard/model.ts:492 Portfolio", to: "/positions/money", kind: "money" },
];

describe("NAV-6a: every Today card and attention link opens in place (the path stays)", () => {
  it("covers the 16 sites of the design table", () => expect(SITES).toHaveLength(16));

  for (const s of SITES) {
    it(s.site, () => {
      const h = history("/");
      const before = h.location();
      const api = inPlace(h);
      expect(api.handles(s.to)).toBe(true);
      api.open(s.to);
      expect(h.location().pathname).toBe("/");
      expect(openPanels(h.location().search, DEFAULT_REGISTRY, (f) => ON[f] === true).map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);
      expect(handoffs(h.location().state)).toEqual([]);
      api.close();
      expect(h.location()).toBe(before);
    });
  }

  it("each new panel's address resolves back to itself (and the old addresses to the same panels)", () => {
    for (const kind of ["money", "performance", "markets", "custom-scan"]) expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get(kind)!.href({}))).toEqual({ kind, params: {} });
    expect(DEFAULT_REGISTRY.resolve("/portfolio")).toEqual({ kind: "money", params: {} });
    expect(DEFAULT_REGISTRY.get("performance")!.href({})).toBe("/positions/results");
  });

  it("a whole page's own address on its own tab changes that tab's view, never the same page over itself", () => {
    const h = history("/markets");
    const api = inPlace(h);
    expect(api.handles("/markets?view=sectors&period=day&list=sector-it")).toBe(false);
    api.open("/markets?list=sector-it");
    expect(h.url()).toBe("/markets?list=sector-it");                         // the Markets tab's own list, as before
    const admin = history("/admin?section=users");                            // a special panel on the same path still opens
    expect(inPlace(admin).handles("/admin?section=support&user=12")).toBe(true);
  });

  it("a flag that is off navigates as before (Sectors without markets_view)", () => {
    const h = history("/");
    inPlace(h, {}).open("/markets?period=week");
    expect(h.url()).toBe("/markets?period=week");
  });

  it("the renderers keep the real address in href (long-press / new tab give the page)", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><CardV2 title="x" more={{ label: "View all", to: "/positions" }}><EmptyStateV2 text="y" action={{ label: "Find setups", to: "/screener" }} /></CardV2></MemoryRouter>);
    expect(html).toContain('href="/positions"');
    expect(html).toContain('href="/screener"');
  });
});

describe("NAV-6a: the renderers and sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["dashboard/CardV2.tsx", /<InPlaceLink to=\{more\.to\} className=\{MORE\}>/],
    ["dashboard/CardV2.tsx", /\? <InPlaceLink to=\{action\.to\} className=\{LINK_SECONDARY\}>/],
    ["dashboard/NeedsAttentionV2.tsx", /<InPlaceLink to=\{action\.to\}/],
    ["dashboard/WeekTilesV2.tsx", /<InPlaceLink key=\{t\.key\} to="\/markets\?period=week"/],
    ["dashboard/DashboardV2.tsx", /onConnect=\{\(\) => inPlace\.open\(CONNECT_BROKER_PATH\)\}/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));
  it("no Today file draws a plain Link any more", () => {
    for (const f of ["dashboard/CardV2.tsx", "dashboard/NeedsAttentionV2.tsx", "dashboard/WeekTilesV2.tsx"]) expect(read(f)).not.toMatch(/<Link\b/);
  });
});
