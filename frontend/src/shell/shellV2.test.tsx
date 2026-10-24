import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { DATA_PAGES } from "../ui/dataPages";
import { FeaturesProvider } from "../ui/useFeatures";
import AppShellV2 from "./AppShellV2";
import { OWN_BROKER_STATES } from "./pagesV2";
import { CommandList, isApplePlatform, isCommandShortcut, moveActive, otherDialogOpen, shortcutLabel } from "./CommandBarV2";
import { MoreSheetContent, moreSheetResults } from "./MoreSheetV2";
import { searchPages, visibleSections, type NavContext } from "./navModel";
import { SidebarV2View, initialsOf, roleLabel, showRoleFor } from "./SidebarV2";
import { v2RedirectFor } from "./shellRoutes";
import { topBarNotices } from "./TopBarV2";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const noop = () => undefined;

describe("UI2-1 command bar", () => {
  const key = (k: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "altKey" | "shiftKey", boolean>> = {}) =>
    ({ key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });

  it("opens on Ctrl K elsewhere and Cmd K on Apple devices - never the other modifier (review M6)", () => {
    expect(isCommandShortcut(key("k", { ctrlKey: true }), false)).toBe(true);
    expect(isCommandShortcut(key("K", { ctrlKey: true }), false)).toBe(true);
    expect(isCommandShortcut(key("k", { metaKey: true }), false)).toBe(false);
    expect(isCommandShortcut(key("k", { metaKey: true }), true)).toBe(true);
    expect(isCommandShortcut(key("k", { ctrlKey: true }), true)).toBe(false);          // Mac Ctrl K: delete to end of line
    expect(isCommandShortcut(key("k"), false)).toBe(false);
    expect(isCommandShortcut(key("k", { ctrlKey: true, shiftKey: true }), false)).toBe(false);
    expect(isCommandShortcut(key("k", { ctrlKey: true, altKey: true }), false)).toBe(false);
    expect(isCommandShortcut(key("j", { ctrlKey: true }), false)).toBe(false);
    expect(isApplePlatform("MacIntel")).toBe(true);
    expect(isApplePlatform("macOS")).toBe(true);
    expect(isApplePlatform("Win32")).toBe(false);
  });

  it("leaves the shortcut alone while another dialog is open", () => {
    const doc = (hit: string | null) => ({ querySelector: (sel: string) => (hit && sel.includes(hit) ? {} : null) }) as unknown as Document;
    expect(otherDialogOpen(doc(null))).toBe(false);
    expect(otherDialogOpen(doc('[role="dialog"]'))).toBe(true);
    // The selector itself excludes the command bar and the More sheet.
    let asked = "";
    otherDialogOpen({ querySelector: (sel: string) => { asked = sel; return null; } } as unknown as Document);
    expect(asked).toContain(":not([data-command-bar])");
    expect(asked).toContain(":not([data-more-sheet])");
    expect(asked).toContain('[role="alertdialog"]');
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
    for (const s of ["Markets", "Trading", "Insights", "Account"]) expect(html).toContain(`>${s}</h3>`);
    expect(html).not.toContain(">Admin</h3>");                          // the platform operator's group only
    expect(html).toContain('href="/option-chain"');
    expect(html).toContain('href="/strategies"');
    expect(html).not.toContain('href="/system-logs"');                  // UX3-9: found by search, not listed
    for (const inBar of ['href="/"', 'href="/charts"', 'href="/signals"', 'href="/positions"']) expect(html).not.toContain(inBar);
    expect(html).not.toContain('href="/admin"');
    expect(html).not.toContain('href="/journal"');                       // flag off
    expect(html.match(/min-h-11/g)?.length).toBeGreaterThanOrEqual(7);   // every tile is a 44 px target
    // P2b: Broker is the first tile, above the sections.
    expect(html.indexOf('href="/broker"')).toBeGreaterThan(-1);
    expect(html.indexOf('href="/broker"')).toBeLessThan(html.indexOf(">Markets</h3>"));
  });

  it("searches every visible page, the bar's included, and says when nothing matches", () => {
    expect(moreSheetResults(trader, "").kind).toBe("sections");
    const r = moreSheetResults(trader, "positions");
    expect(r.kind === "matches" && r.items[0].label).toBe("My trades");
    const logs = moreSheetResults(trader, "activity");
    expect(logs.kind === "matches" && logs.items.map((i) => i.label)).toEqual(["Activity log"]);
    const html = render("screener", { ...trader, on: flags("screener_v2") });       // UX1: Find setups needs screener_v2
    expect(html).toContain('href="/screener"');
    expect(html).toContain("Markets");                                  // the section as a caption
    expect(render("nothing-like-this")).toContain("No page matches &quot;nothing-like-this&quot;.");
  });
});

describe("UI2-1 sidebar", () => {
  const user = { id: 1, email: "abhi.wasu@example.com", tenant_id: 1, role: "OWNER" };
  const sidebar = (ctx: NavContext, current: Parameters<typeof SidebarV2View>[0]["current"]) => renderToStaticMarkup(
    <MemoryRouter>
      <SidebarV2View sections={visibleSections(ctx)} current={current} user={user} accountPath="/settings/account" />
    </MemoryRouter>,
  );
  const hrefs = (html: string) => [...html.matchAll(/<a [^>]*href="([^"]*)"/g)].map((m) => m[1]);

  it("UX3-9: one flat list in task order, Today first and Broker above Settings (day-one fix 9) - no section headings, nothing folds", () => {
    const html = sidebar(trader, "positions");
    expect(html).not.toContain("aria-expanded");
    expect(hrefs(html)).toEqual([
      "/", "/", "/charts", "/option-chain", "/signals", "/deployments", "/positions", "/analytics", "/strategies",
      "/news-events", "/broker", "/settings", "/settings/account",
    ]);                                                                   // logo, the entries, the account footer
    expect(html).toMatch(/aria-current="page"[^>]*href="\/positions"|href="\/positions"[^>]*aria-current="page"/);
    expect(html).toContain(">AW<");
    expect(html).not.toContain("Owner");                                 // members not known: no role label
    expect(html).toContain(">Paper<");
    expect(html).not.toContain("PAPER MODE");
    expect(html).not.toContain("data-broker-dot");                       // no status known: no dot
    expect(html).not.toContain("data-nav-admin");
  });

  it("user-friendly pass item 3: the role under the name only when the organisation has more than one member", () => {
    const withMembers = (members: number | null) => renderToStaticMarkup(
      <MemoryRouter>
        <SidebarV2View sections={visibleSections(trader)} current="dashboard" user={user} accountPath="/settings/account" members={members} />
      </MemoryRouter>,
    );
    expect(withMembers(1)).not.toContain("data-role-label");
    expect(withMembers(1)).not.toContain("Owner");
    expect(withMembers(null)).not.toContain("data-role-label");
    expect(withMembers(3)).toContain("data-role-label");
    expect(withMembers(3)).toContain(">Owner<");
  });

  it("UX3-9: 13 entries with every feature on; the Admin console set apart for the platform operator", () => {
    const all = { on: () => true, role: "SUPER_ADMIN" };
    const html = sidebar(all, "dashboard");
    const entries = hrefs(html).slice(1, -1);
    expect(entries).toEqual([
      "/", "/charts", "/option-chain", "/markets", "/screener", "/signals", "/deployments", "/positions",
      "/analytics", "/strategies", "/copilot", "/news-events", "/learn", "/broker", "/settings", "/admin",   // C13: Learn (flag `learn`)
    ]);
    expect(html.indexOf("data-nav-admin")).toBeLessThan(html.indexOf('href="/admin"'));
  });

  it("UX3-9: a merged page's tab and a Settings sub-page light up their entry", () => {
    const current = (key: Parameters<typeof SidebarV2View>[0]["current"]) =>
      /<a [^>]*aria-current="page"[^>]*href="([^"]*)"|<a [^>]*href="([^"]*)"[^>]*aria-current="page"/.exec(sidebar(trader, key))?.slice(1).find(Boolean);
    expect(current("orders")).toBe("/positions");
    expect(current("portfolio")).toBe("/positions");
    expect(current("backtest")).toBe("/strategies");
    expect(current("strategy-builder")).toBe("/strategies");
    expect(current("coach")).toBe("/analytics");
    expect(current("risk-management")).toBe("/settings");
    expect(current("settings-ai")).toBe("/settings");
  });

  it("P2b: the Broker entry carries the status dot (red only for 'not working')", () => {
    const at = (dot: "connected" | "needs_login" | "not_working") => renderToStaticMarkup(
      <MemoryRouter>
        <SidebarV2View sections={visibleSections(trader)} current="dashboard" dot={dot}
          user={{ id: 1, email: "abhi.wasu@example.com", tenant_id: 1, role: "OWNER" }} accountPath="/settings/account" />
      </MemoryRouter>,
    );
    expect(at("connected")).toMatch(/href="\/broker"[^]*?data-broker-dot="connected"[^>]*bg-up/);
    expect(at("needs_login")).toMatch(/data-broker-dot="needs_login"[^>]*bg-warn/);
    expect(at("not_working")).toMatch(/data-broker-dot="not_working"[^>]*bg-down/);
    expect(at("needs_login")).toContain(", needs login");                // the words, for screen readers
    expect(at("connected").match(/data-broker-dot/g)).toHaveLength(1);
  });

  it("labels roles and initials plainly", () => {
    expect(roleLabel("SUPER_ADMIN")).toBe("Platform operator");
    expect(showRoleFor(null)).toBe(false);
    expect(showRoleFor(1)).toBe(false);
    expect(showRoleFor(2)).toBe(true);
    expect(roleLabel("OWNER")).toBe("Owner");
    expect(roleLabel("RISK_MANAGER")).toBe("Risk manager");
    expect(initialsOf("abhi.wasu@example.com")).toBe("AW");
    expect(initialsOf("demo@example.com")).toBe("DE");
    expect(initialsOf("@x")).toBe("?");
  });
});

describe("UI2-1 top bar notices", () => {
  it("uses plain words: maintenance and LIVE paused are warnings; never an e-mail chip (item 1)", () => {
    const status = { maintenance_mode: true, maintenance_message: "Back at 10:00", disabled_brokers: ["zerodha"] } as never;
    expect(topBarNotices(status, { id: 1, email: "a@b.c", tenant_id: 1, role: "OWNER", email_verified: false })).toEqual([
      { kind: "warn", text: "Maintenance: new trades are paused. Back at 10:00" },
      { kind: "warn", text: "Live trading is paused on zerodha" },
    ]);
    expect(topBarNotices(null, { id: 1, email: "a@b.c", tenant_id: 1, role: "OWNER", email_verified: false })).toEqual([]);
    expect(topBarNotices(status, null, true)).toEqual([{ kind: "warn", text: "Maintenance: new trades are paused. Back at 10:00" }]);
    expect(topBarNotices(null, null)).toEqual([]);
  });
});

describe("UI2-1 the v2 shell puts the broker gate in front of every data page", () => {
  // Rendered once on the server: the auth check has not answered yet, so a gated page shows the gate's
  // "checking" state - never the page - while a page outside DATA_PAGES goes straight to its (lazy) content.
  // UX1: the pages behind a flag are on here (a page whose flag is off shows "Coming soon", tested below).
  const render = (path: string, value: Record<string, boolean> = { ui_v2: true, screener_v2: true, discipline_engine: true, options_builder: true, ai_copilot: true }) => renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <FeaturesProvider userKey={1} value={value}>
          <AppShellV2 />
        </FeaturesProvider>
      </AuthProvider>
    </MemoryRouter>,
  );

  it("gates every data page and its v2 sub-addresses", () => {
    // An old address that forwards (/scanner, /options-builder) is checked at the address it forwards to.
    const at = (p: string) => v2RedirectFor(p, "", { signedIn: true, on: flags() }) ?? p;
    // UI2-2: the v2 dashboard (`/`) shows the broker states itself (src/shell/pagesV2.ts OWN_BROKER_STATES) - see below.
    const paths = [...DATA_PAGES.filter((p) => !OWN_BROKER_STATES.includes(p.path)).map((p) => at(p.path === "/copilot" ? "/copilot/market-pulse" : p.path)),
      "/charts/RELIANCE", "/screener/custom", "/option-chain/plan"];
    expect(paths).toContain("/charts");
    expect(paths).not.toContain("/");
    for (const path of paths) {
      const html = render(path);
      expect(html, path).toContain('data-broker-gate="loading"');
      expect(html, path).not.toContain("Loading page");                 // the page itself is not even loading
    }
  });

  it("UI2-2: the dashboard is not wrapped by the shell - its v2 page shows the gate's states itself", () => {
    const html = render("/");
    expect(html).not.toContain("data-broker-gate");
    expect(html).toContain("Loading page");                               // the v2 dashboard's own chunk
  });

  it("gates percent-encoded and undecodable addresses too (review M1)", () => {
    for (const path of ["/option%2Dchain", "/positions/m%6Fney", "/strategies/t%65st", "/%63harts", "/%E0%A4"]) {
      expect(render(path), path).toContain('data-broker-gate="loading"');
    }
    // UI2-9a: My positions shows the broker states itself (OWN_BROKER_STATES), at its encoded address too.
    expect(render("/%70ositions")).not.toContain("data-broker-gate");
    expect(render("/%70ositions")).toContain("My trades");                // the title follows the router's reading
    expect(render("/positions/%6Frders")).not.toContain("data-broker-gate");   // UX3-9: Orders is the trader's own record
  });

  it("UX1: a page whose flag is off says 'Coming soon' at its address - no flag name, no gate, no page", () => {
    for (const path of ["/screener", "/analytics/journal", "/marketplace"]) {
      const html = render(path, { ui_v2: true });
      expect(html, path).toContain('data-state="coming-soon"');
      expect(html, path).toContain("Coming soon");
      expect(html, path).not.toMatch(/screener_v2|discipline_engine|>marketplace</);
      expect(html, path).not.toContain("data-broker-gate");
    }
    expect(render("/charts", { ui_v2: true })).not.toContain('data-state="coming-soon"');
  });

  it("does not gate Settings, Orders, Journal, Account or Team", () => {
    for (const path of ["/settings", "/positions/orders", "/analytics/journal", "/analytics/coach", "/strategies/build", "/strategies",
      "/analytics", "/settings/account", "/team", "/notifications", "/risk-management"]) {
      const html = render(path);
      expect(html, path).not.toContain("data-broker-gate");
      expect(html, path).toContain("Loading page");
    }
  });

  it("UX3-9: a merged page shows its tabs, the current one marked, each at its own address", () => {
    const html = render("/positions/orders");
    expect(html).toContain('data-page-tabs="my-trades"');
    expect(html).toMatch(/<nav aria-label="My trades"[^>]*>.*href="\/positions".*>Positions<.*href="\/positions\/orders".*>Orders<.*href="\/positions\/money".*>Money</s);
    expect(html).toMatch(/aria-current="page"[^>]*data-page-tab="orders"/);
    const strategies = render("/strategies/test", { ui_v2: true, idea_library: true });
    expect(strategies).toMatch(/>Library<.*>Ideas<.*>Build<.*>Test</s);
    expect(render("/strategies/test", { ui_v2: true })).not.toContain(">Ideas<");      // a tab whose feature is off is left out
    expect(render("/analytics", { ui_v2: true })).toMatch(/data-page-tabs="performance".*>Results<.*>Coach</s);
    expect(render("/analytics", { ui_v2: true })).not.toContain('data-page-tab="journal"');
    expect(render("/signals")).not.toContain("data-page-tabs");
  });

  it("is the v2 shell: sidebar sections, bottom bar of five, search", () => {
    const html = render("/settings");
    expect(html).toContain('data-shell="v2"');
    expect(html).toContain('aria-label="Quick navigation"');
    expect(html).toContain("Search pages");
    expect(html).toContain("Skip to content");
    expect(html).toMatch(/>Today<.*>Charts<.*>Signals<.*>My trades<.*>More</s);
  });
});
