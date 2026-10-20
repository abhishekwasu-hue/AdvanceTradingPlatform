import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { DATA_PAGES } from "../ui/dataPages";
import { FeaturesProvider } from "../ui/useFeatures";
import AppShellV2 from "./AppShellV2";
import { CommandList, isCommandShortcut, moveActive, shortcutLabel } from "./CommandBarV2";
import { MoreSheetContent, moreSheetResults } from "./MoreSheetV2";
import { searchPages, visibleSections, type NavContext } from "./navModel";
import {
  DEFAULT_OPEN, SidebarV2View, initialsOf, isSectionOpen, openForCurrent, parseSectionMemory, roleLabel,
} from "./SidebarV2";
import { v2RedirectFor } from "./shellRoutes";
import { topBarNotices } from "./TopBarV2";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const noop = () => undefined;

describe("UI2-1 command bar", () => {
  const key = (k: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "altKey" | "shiftKey", boolean>> = {}) =>
    ({ key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });

  it("opens on Ctrl K and Cmd K only", () => {
    expect(isCommandShortcut(key("k", { ctrlKey: true }))).toBe(true);
    expect(isCommandShortcut(key("K", { metaKey: true }))).toBe(true);
    expect(isCommandShortcut(key("k"))).toBe(false);
    expect(isCommandShortcut(key("k", { ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isCommandShortcut(key("k", { ctrlKey: true, altKey: true }))).toBe(false);
    expect(isCommandShortcut(key("j", { ctrlKey: true }))).toBe(false);
  });

  it("moves the highlight with the arrow keys, wrapping around", () => {
    expect(moveActive(0, 1, 3)).toBe(1);
    expect(moveActive(2, 1, 3)).toBe(0);
    expect(moveActive(0, -1, 3)).toBe(2);
    expect(moveActive(-1, 1, 3)).toBe(0);
    expect(moveActive(-1, -1, 3)).toBe(2);
    expect(moveActive(0, 1, 0)).toBe(-1);
  });

  it("names the shortcut for the platform", () => {
    expect(shortcutLabel("MacIntel")).toBe("⌘K");
    expect(shortcutLabel("iPhone")).toBe("⌘K");
    expect(shortcutLabel("Win32")).toBe("Ctrl K");
    expect(shortcutLabel("Linux x86_64")).toBe("Ctrl K");
  });

  it("lists the matches as options with the highlighted one selected, or says nothing matched", () => {
    const results = searchPages("orders", trader);
    const html = renderToStaticMarkup(
      <CommandList query="orders" results={results} active={0} listId="l" optionId={(i) => `o${i}`} onPick={noop} onHover={noop} />,
    );
    expect(html).toContain('role="listbox"');
    expect(html).toMatch(/id="o0" role="option" aria-selected="true"[^>]*>.*Orders/);
    const none = renderToStaticMarkup(<CommandList query="zz" results={[]} active={-1} listId="l" optionId={String} onPick={noop} onHover={noop} />);
    expect(none).toContain("No page matches &quot;zz&quot;.");
  });
});

describe("UI2-1 More sheet", () => {
  const render = (query: string, ctx = trader) => renderToStaticMarkup(
    <MemoryRouter><MoreSheetContent ctx={ctx} current="dashboard" query={query} onQuery={noop} onPick={noop} /></MemoryRouter>,
  );

  it("shows every other page grouped by section, with a search box", () => {
    const html = render("");
    expect(html).toContain('type="search"');
    expect(html).toContain("Search pages");
    for (const s of ["Markets", "Strategies", "Trading", "Insights", "Account", "More"]) expect(html).toContain(`>${s}</h3>`);
    expect(html).toContain('href="/option-chain"');
    expect(html).toContain('href="/system-logs"');
    for (const inBar of ['href="/"', 'href="/charts"', 'href="/signals"', 'href="/positions"']) expect(html).not.toContain(inBar);
    expect(html).not.toContain('href="/admin"');
    expect(html).not.toContain('href="/journal"');                       // flag off
    expect(html.match(/min-h-11/g)?.length).toBeGreaterThanOrEqual(15);  // every tile is a 44 px target
  });

  it("searches every visible page, the bar's included, and says when nothing matches", () => {
    expect(moreSheetResults(trader, "").kind).toBe("sections");
    const r = moreSheetResults(trader, "positions");
    expect(r.kind === "matches" && r.items[0].label).toBe("My positions");
    const html = render("screener");
    expect(html).toContain('href="/screener"');
    expect(html).toContain("Markets");                                  // the section as a caption
    expect(render("nothing-like-this")).toContain("No page matches &quot;nothing-like-this&quot;.");
  });
});

describe("UI2-1 sidebar", () => {
  it("remembers open sections; Markets and Trading start open; the current page's section opens", () => {
    expect(DEFAULT_OPEN).toEqual({ markets: true, strategies: false, trading: true, insights: false, account: false, more: false });
    expect(parseSectionMemory('{"strategies":true,"markets":false,"bogus":true,"more":"yes"}')).toEqual({ strategies: true, markets: false });
    expect(parseSectionMemory("not json")).toEqual({});
    expect(parseSectionMemory("[1]")).toEqual({});
    expect(isSectionOpen({}, "trading")).toBe(true);
    expect(isSectionOpen({ trading: false }, "trading")).toBe(false);
    const m = { insights: false };
    expect(openForCurrent(m, "insights")).toEqual({ insights: true });
    expect(openForCurrent(m, "markets")).toBe(m);                       // already open: unchanged
    expect(openForCurrent(m, null)).toBe(m);
  });

  it("renders the visible sections, items of open sections only, and marks the current page", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <SidebarV2View sections={visibleSections(trader)} current="positions" memory={{}} onToggle={noop}
          user={{ id: 1, email: "abhi.wasu@example.com", tenant_id: 1, role: "OWNER" }} accountPath="/settings/account" />
      </MemoryRouter>,
    );
    expect(html.match(/aria-expanded="true"/g)).toHaveLength(2);         // Markets, Trading
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(4);
    expect(html).toContain('href="/charts"');
    expect(html).not.toContain('href="/strategies"');                    // Strategies closed
    expect(html).toMatch(/aria-current="page"[^>]*href="\/positions"|href="\/positions"[^>]*aria-current="page"/);
    expect(html).toContain(">AW<");
    expect(html).toContain("Owner");
    expect(html).toContain(">Paper<");
    expect(html).not.toContain("PAPER MODE");
  });

  it("labels roles and initials plainly", () => {
    expect(roleLabel("SUPER_ADMIN")).toBe("Platform operator");
    expect(roleLabel("OWNER")).toBe("Owner");
    expect(roleLabel("RISK_MANAGER")).toBe("Risk manager");
    expect(initialsOf("abhi.wasu@example.com")).toBe("AW");
    expect(initialsOf("demo@example.com")).toBe("DE");
    expect(initialsOf("@x")).toBe("?");
  });
});

describe("UI2-1 top bar notices", () => {
  it("uses plain words: maintenance and LIVE paused are warnings, verify email is a link", () => {
    const status = { maintenance_mode: true, maintenance_message: "Back at 10:00", disabled_brokers: ["zerodha"] } as never;
    expect(topBarNotices(status, { id: 1, email: "a@b.c", tenant_id: 1, role: "OWNER", email_verified: false })).toEqual([
      { kind: "warn", text: "Maintenance: new trades are paused. Back at 10:00" },
      { kind: "warn", text: "Live trading is paused on zerodha" },
      { kind: "link", text: "Verify your email", title: "Live trading and broker keys may need a verified address" },
    ]);
    expect(topBarNotices(null, null)).toEqual([]);
  });
});

describe("UI2-1 the v2 shell puts the broker gate in front of every data page", () => {
  // Rendered once on the server: the auth check has not answered yet, so a gated page shows the gate's
  // "checking" state - never the page - while a page outside DATA_PAGES goes straight to its (lazy) content.
  const render = (path: string) => renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <FeaturesProvider userKey={1} value={{ ui_v2: true }}>
          <AppShellV2 />
        </FeaturesProvider>
      </AuthProvider>
    </MemoryRouter>,
  );

  it("gates every data page and its v2 sub-addresses", () => {
    // An old address that forwards (/scanner, /options-builder) is checked at the address it forwards to.
    const at = (p: string) => v2RedirectFor(p, "", { signedIn: true, on: flags() }) ?? p;
    const paths = [...DATA_PAGES.map((p) => at(p.path === "/copilot" ? "/copilot/market-pulse" : p.path)), "/charts/RELIANCE", "/screener/custom", "/option-chain/plan"];
    expect(paths).toContain("/charts");
    for (const path of paths) {
      const html = render(path);
      expect(html, path).toContain('data-broker-gate="loading"');
      expect(html, path).not.toContain("Loading page");                 // the page itself is not even loading
    }
  });

  it("does not gate Settings, Orders, Journal, Account or Team", () => {
    for (const path of ["/settings", "/orders", "/journal", "/journal/coach", "/settings/account", "/team", "/notifications"]) {
      const html = render(path);
      expect(html, path).not.toContain("data-broker-gate");
      expect(html, path).toContain("Loading page");
    }
  });

  it("is the v2 shell: sidebar sections, bottom bar of five, search", () => {
    const html = render("/settings");
    expect(html).toContain('data-shell="v2"');
    expect(html).toContain('aria-label="Quick navigation"');
    expect(html).toContain("Search pages");
    expect(html).toContain("Skip to content");
    expect(html).toMatch(/>Home<.*>Charts<.*>Signals<.*>Positions<.*>More</s);
  });
});
