import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BROKER_PATH } from "../broker/paths";
import { legalPath } from "../legal/legal";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { PageIntroView } from "../ui/PageIntro";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-6b (docs/design/M75_NO_NAVIGATION.md, the shell rows): the 20 click-navigations of the shell open in place -
 * the bell, the account notices, help and support links, page intros' "Show me", tours, one-time notices, the broker
 * gate and the broker prompt, the status notice and the legal banner. The PATH of the tab stays, the panel registered
 * for the destination is on top, and closing returns to the exact entry before. "Show all tips again" stays on the tab.
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

function inPlace(h: ReturnType<typeof history>) {
  return createInPlaceApi({ enabled: true, on: () => false, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
}

/** Each site's destination; a renderer whose destination comes from data is shown with a representative one. */
const SITES: { site: string; to: string; kind: string; params?: Record<string, string> }[] = [
  { site: "App.tsx:138 the classic bell (no host there: a plain link)", to: "/notifications", kind: "notifications" },
  { site: "broker/BrokerPromptHost.tsx:63 Connect", to: BROKER_PATH, kind: "broker" },
  { site: "learn/LearnPageV2.tsx:184 the next lesson's page (data)", to: "/charts/NIFTY%2050", kind: "chart", params: { symbol: "NIFTY 50" } },
  { site: "legal/LegalAcceptanceBanner.tsx:56 a legal document", to: legalPath("risk"), kind: "legal", params: { slug: "risk" } },
  { site: "notices/OneTimeNoticesV2.tsx:17 Open Risk limits", to: "/settings/risk-limits", kind: "risk-limits" },
  { site: "rhythm/DailyRhythmCardV2.tsx:31 a section's link (data)", to: "/learn/news", kind: "news" },
  { site: "shell/TopBarV2.tsx:208 Show me (data)", to: "/signals", kind: "signals" },
  { site: "shell/TopBarV2.tsx:254 the bell", to: "/notifications", kind: "notifications" },
  { site: "shell/TopBarV2.tsx:273 an account notice", to: "/settings/account", kind: "account" },
  { site: "status/DegradedNotice.tsx:22 See status", to: "/status", kind: "status" },
  { site: "support/HelpCentreV2.tsx:72 Open the page (data)", to: "/settings/trading-defaults", kind: "trading-defaults" },
  { site: "support/SupportDialog.tsx:80 a reply's link (data)", to: "/positions/orders", kind: "orders" },
  { site: "tours/TourHost.tsx:58 the welcome tour's last step", to: BROKER_PATH, kind: "broker" },
  { site: "ui/BrokerGate.tsx:172 Connect a broker (gate card)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "ui/BrokerGate.tsx:285 Connect (stored-daily note)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "ui/BrokerGate.tsx:333 Connect a broker (login card)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "ui/BrokerGate.tsx:362 onConnect (stored daily)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "ui/BrokerGate.tsx:372 onConnect (gate)", to: CONNECT_BROKER_PATH, kind: "broker" },
  { site: "ui/PageIntro.tsx:102 Show me (data)", to: "/screener", kind: "screener" },
];

describe("NAV-6b: every shell link opens in place (the path stays)", () => {
  it("covers 19 of the 20 sites (the 20th, Show all tips again, now stays on the tab: below)", () => expect(SITES).toHaveLength(19));

  for (const s of SITES) {
    it(s.site, () => {
      const h = history("/positions");
      const before = h.location();
      const api = createInPlaceApi({ enabled: true, on: (f) => f === "screener_v2", registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
      expect(api.handles(s.to)).toBe(true);
      api.open(s.to);
      expect(h.location().pathname).toBe("/positions");
      expect(openPanels(h.location().search, DEFAULT_REGISTRY, (f) => f === "screener_v2").map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);
      expect(handoffs(h.location().state)).toEqual([]);
      api.close();
      expect(h.location()).toBe(before);
    });
  }

  it("new panels: Notifications is a drawer, the status and the legal documents are modals; each resolves its own address", () => {
    expect(DEFAULT_REGISTRY.get("notifications")!.presentation).toBe("drawer");
    expect(DEFAULT_REGISTRY.get("status")!.presentation).toBe("modal");
    expect(DEFAULT_REGISTRY.get("legal")!.presentation).toBe("modal");
    expect(DEFAULT_REGISTRY.get("legal")!.title({ slug: "terms" })).toBe("Terms of use");
    for (const [kind, params] of [["notifications", {}], ["account", {}], ["status", {}], ["legal", { slug: "privacy" }]] as const) {
      expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get(kind)!.href(params))).toEqual({ kind, params });
    }
    expect(DEFAULT_REGISTRY.resolve("/account")).toEqual({ kind: "account", params: {} });
    expect(inPlace(history("/")).handles("/legal")).toBe(false);
  });

  it("a page intro's Show me keeps its real address, also outside a router", () => {
    const view = <PageIntroView lines={["x"]} showMe={{ href: "/screener", label: "Show me" }} onDismiss={() => undefined} onExpand={() => undefined} />;
    expect(renderToStaticMarkup(view)).toContain('href="/screener"');
    expect(renderToStaticMarkup(<MemoryRouter>{view}</MemoryRouter>)).toContain('href="/screener"');
  });
});

describe("NAV-6b: the sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["App.tsx", /<InPlaceLink\n\s+to=\{pathFor\("notifications"\)\}/],
    ["broker/BrokerPromptHost.tsx", /onConnect=\{\(\) => \{ close\(\); inPlace\.open\(BROKER_PATH\); \}\}/],
    ["learn/LearnPageV2.tsx", /<InPlaceLink to=\{lesson\.next\.path\}/],
    ["legal/LegalAcceptanceBanner.tsx", /<InPlaceLink to=\{legalPath\(slug\)\} newTabOtherwise rel="noopener"/],
    ["notices/OneTimeNoticesV2.tsx", /<InPlaceLink to=\{notice\.link\}/],
    ["rhythm/DailyRhythmCardV2.tsx", /<InPlaceLink to=\{section\.link\.to\}/],
    ["shell/TopBarV2.tsx", /<InPlaceLink to=\{help\.showMe\.href\}/],
    ["shell/TopBarV2.tsx", /<InPlaceLink to="\/notifications"/],
    ["shell/TopBarV2.tsx", /<InPlaceLink key=\{n\.text\} to=\{accountPath\}/],
    ["status/DegradedNotice.tsx", /<InPlaceLink to="\/status"/],
    ["support/HelpCentreV2.tsx", /<InPlaceLink to=\{article\.page\}/],
    ["support/SupportDialog.tsx", /<InPlaceLink key=\{l\.url\} to=\{l\.url\} onClick=\{\(\) => onOpenChange\(false\)\}/],
    ["tours/TourHost.tsx", /if \(closed\.go\) inPlace\.open\(closed\.go\);/],
    ["ui/PageIntro.tsx", /<InPlaceLink to=\{showMe\.href\}/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));

  it("the broker gate's five Connect sites use the host", () => {
    const src = read("ui/BrokerGate.tsx");
    expect(src.match(/<InPlaceLink to=\{CONNECT_BROKER_PATH\} onClick=/g)).toHaveLength(2);
    expect(src.match(/<InPlaceLink to=\{CONNECT_BROKER_PATH\} className=/g)).toHaveLength(1);
    expect(src.match(/inPlace\.open\(CONNECT_BROKER_PATH\)/g)).toHaveLength(2);
    expect(src).not.toMatch(/navigate\(CONNECT_BROKER_PATH\)/);
  });

  it("Show all tips again replays the tips on the tab he is on (no jump to Today)", () => {
    expect(read("tours/TipsButtonsV2.tsx")).not.toMatch(/navigate\(/);
  });
});
