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
    expect(none).toContain("No symbol or page matches &quot;zz&quot;.");
    const busy = renderToStaticMarkup(<CommandList query="zz" results={[]} searching active={-1} listId="l" optionId={String} onPick={noop} onHover={noop} />);
    expect(busy).toContain("Searching…");
  });
});

describe("UI2-1 More sheet", () => {
  const render = (query: string, ctx = trader) => renderToStaticMarkup(
    <MemoryRouter><MoreSheetContent ctx={ctx} current="dashboard" query={query} onQuery={noop} onPick={noop} /></MemoryRouter>,
  );

  it("shows every other page grouped by section, with a search box (M60: under the sidebar's section titles)", () => {
    const html = render("");
    expect(html).toContain('type="search"');
    expect(html).toContain("Search pages");
    for (const s of ["Find", "Build", "Learn &amp; AI", "Account"]) expect(html).toContain(`>${s}</h3>`);
    expect(html).not.toContain(">Trade</h3>");                          // its four entries are all on the bar
    expect(html).toContain('href="/signals"');
    expect(html).toContain('href="/strategies"');
    expect(html).toContain('href="/settings"');
    expect(html).not.toContain('href="/system-logs"');                  // found by search, not listed
    for (const inBar of ['href="/"', 'href="/charts"', 'href="/option-chain"', 'href="/positions"']) expect(html).not.toContain(inBar);
    expect(html).not.toContain('href="/admin"');
    expect(html).not.toContain('href="/broker"');                       // M60: Broker accounts live in Settings
    expect(html.match(/min-h-11/g)?.length).toBeGreaterThanOrEqual(5);   // every tile is a 44 px target
  });

  it("M57 U7-2: from the top bar's menu button the sheet lists every section, the bar's entries too", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><MoreSheetContent ctx={trader} current="dashboard" query="" onQuery={noop} onPick={noop} all /></MemoryRouter>,
    );
    expect(html).toContain(">Trade</h3>");
    for (const p of ['href="/"', 'href="/charts"', 'href="/option-chain"', 'href="/positions"', 'href="/signals"', 'href="/settings"']) expect(html).toContain(p);
  });

  it("searches every visible page, the bar's included, and says when nothing matches", () => {
    expect(moreSheetResults(trader, "").kind).toBe("sections");
    const r = moreSheetResults(trader, "positions");
    expect(r.kind === "matches" && r.items[0].label).toBe("My trades");
    const logs = moreSheetResults(trader, "activity");
    expect(logs.kind === "matches" && logs.items.map((i) => i.label)).toEqual(["Activity log"]);
    const html = render("screener", { ...trader, on: flags("screener_v2") });       // UX1: Find setups needs screener_v2
    expect(html).toContain('href="/screener"');
    expect(html).toContain("Find");                                     // the section as a caption
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

  it("M60: five sections with short titles, entries in task order; nothing folds away", () => {
    const html = sidebar(trader, "positions");
    expect(html).not.toContain("aria-expanded");
    expect([...html.matchAll(/<h2 [^>]*>([^<]+)<\/h2>/g)].map((m) => m[1])).toEqual(["Trade", "Find", "Build", "Learn &amp; AI", "Account"]);
    expect(hrefs(html)).toEqual([
      "/", "/", "/charts", "/option-chain", "/positions", "/signals", "/strategies", "/deployments", "/learn/news", "/settings",
      "/settings/account",
    ]);                                                                   // logo, the entries, the account footer
    expect(html).toMatch(/aria-current="page"[^>]*href="\/positions"|href="\/positions"[^>]*aria-current="page"/);
    expect(html).toContain(">AI Trading Bot<");
    expect(html).toContain(">AW<");
    expect(html).not.toContain("Owner");                                 // members not known: no role label
    expect(html).toContain(">Paper<");
    expect(html).not.toContain("PAPER MODE");
    expect(html).not.toContain("data-broker-dot");                       // no status known: no dot
    expect(html).not.toContain("data-nav-admin");
    expect(html).toContain('data-sidebar-mode="expanded"');
    expect(html).toContain("w-60");
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

  it("M60: 11 entries with every feature on; Admin at the end of Account for the platform operator only", () => {
    const all = { on: () => true, role: "SUPER_ADMIN" };
    const html = sidebar(all, "dashboard");
    const entries = hrefs(html).slice(1, -1);
    expect(entries).toEqual([
      "/", "/terminal", "/positions", "/screener", "/signals", "/markets", "/strategies", "/deployments", "/copilot", "/learn",
      "/settings", "/admin",
    ]);
    expect(html.indexOf("data-nav-admin")).toBeLessThan(html.indexOf('href="/admin"'));
    expect(html.indexOf(">Account</h2>")).toBeLessThan(html.indexOf('href="/admin"'));
    expect(sidebar({ on: () => true, role: "OWNER" }, "dashboard")).not.toContain('href="/admin"');
  });

  it("M60: a page lights up its home entry - a tab, a part of the Terminal, a Settings page", () => {
    const current = (ctx: NavContext, key: Parameters<typeof SidebarV2View>[0]["current"]) =>
      [...sidebar(ctx, key).matchAll(/<a [^>]*aria-current="page"[^>]*href="([^"]*)"|<a [^>]*href="([^"]*)"[^>]*aria-current="page"/g)]
        .map((m) => m[1] ?? m[2]);
    expect(current(trader, "orders")).toEqual(["/positions"]);
    expect(current(trader, "analytics")).toEqual(["/positions"]);
    expect(current(trader, "backtest")).toEqual(["/strategies"]);
    expect(current(trader, "risk-management")).toEqual(["/settings"]);
    expect(current(trader, "broker")).toEqual(["/settings"]);
    expect(current(trader, "charts")).toEqual(["/charts"]);                     // a stand-in while the Terminal is off
    expect(current({ on: flags("terminal"), role: "OWNER" }, "charts")).toEqual(["/terminal"]);
    expect(current({ on: flags("ai_copilot"), role: "OWNER" }, "chart-analyze")).toEqual(["/copilot"]);
  });

  it("M57 U7-1: the rail - 56 px of icons, each named by a tooltip and for screen readers; section titles read out", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <SidebarV2View sections={visibleSections(trader)} current="positions" user={user} accountPath="/settings/account" mode="rail" dot="needs_login" />
      </MemoryRouter>,
    );
    expect(html).toContain('data-sidebar-mode="rail"');
    expect(html).toContain("w-14");
    expect(html).not.toContain("w-60");
    for (const s of visibleSections(trader)) {
      for (const i of s.items) {
        expect(html, i.label).toMatch(new RegExp(`title="${i.label.replace("&", "&amp;")}"`));
        expect(html, i.label).toContain(`<span class="sr-only">${i.label.replace("&", "&amp;")}</span>`);
      }
    }
    expect(html).toMatch(/<h2 [^>]*class="sr-only"[^>]*>Trade<\/h2>/);
    expect(html).toMatch(/href="\/settings"[^]*?data-broker-dot="needs_login"/);  // the dot stays on the rail
    expect(html).toContain('aria-label="Account, abhi.wasu@example.com"');
    expect(html).not.toContain(">Paper<");
  });

  it("P2b + M60: the Settings entry carries the broker's status dot (red only for 'not working')", () => {
    const at = (dot: "connected" | "needs_login" | "not_working") => renderToStaticMarkup(
      <MemoryRouter>
        <SidebarV2View sections={visibleSections(trader)} current="dashboard" dot={dot}
          user={{ id: 1, email: "abhi.wasu@example.com", tenant_id: 1, role: "OWNER" }} accountPath="/settings/account" />
      </MemoryRouter>,
    );
    // M60: the dot sits on the Settings row (Broker accounts live in Settings).
    expect(at("connected")).toMatch(/href="\/settings"[^]*?data-broker-dot="connected"[^>]*bg-up/);
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
    for (const path of ["/screener", "/positions/results/journal", "/marketplace"]) {
      const html = render(path, { ui_v2: true });
      expect(html, path).toContain('data-state="coming-soon"');
      expect(html, path).toContain("Coming soon");
      expect(html, path).not.toMatch(/screener_v2|discipline_engine|>marketplace</);
      expect(html, path).not.toContain("data-broker-gate");
    }
    expect(render("/charts", { ui_v2: true })).not.toContain('data-state="coming-soon"');
  });

  it("does not gate Settings, Orders, Results, Journal, Account or Team", () => {
    for (const path of ["/settings", "/positions/orders", "/positions/results", "/positions/results/journal", "/analytics/coach",
      "/copilot/coach", "/strategies/build", "/strategies", "/settings/account", "/settings/broker", "/team", "/notifications",
      "/risk-management", "/learn/news"]) {
      const html = render(path);
      expect(html, path).not.toContain("data-broker-gate");
    }
  });

  it("UX3-9 + M60: a merged page shows its tabs (at most four), the current one marked, each at its own address", () => {
    const html = render("/positions/orders");
    expect(html).toContain('data-page-tabs="my-trades"');
    expect(html).toMatch(/<nav aria-label="My trades"[^>]*>.*href="\/positions".*>Positions<.*href="\/positions\/orders".*>Orders<.*href="\/positions\/money".*>Money<.*href="\/positions\/results".*>Results</s);
    expect(html).toMatch(/aria-current="page"[^>]*data-page-tab="orders"/);
    const strategies = render("/strategies/test", { ui_v2: true, idea_library: true });
    expect(strategies).toMatch(/>Library<.*>Build<.*>Test</s);
    expect(strategies).not.toContain(">Ideas<");                                       // M60: Ideas is a Learn tab
    // Default 2: Results holds Summary · Journal as a second row; the Results tab is marked on both.
    const journal = render("/positions/results/journal");
    expect(journal).toMatch(/aria-current="page"[^>]*data-page-tab="analytics"/);
    expect(journal).toMatch(/data-page-parts="analytics".*>Summary<.*>Journal</s);
    expect(journal).toMatch(/aria-current="page"[^>]*data-page-part="journal"/);
    expect(render("/positions/results", { ui_v2: true })).not.toContain('data-page-part="journal"');   // its feature off
    // Settings: Account & broker · Trading · AI · App, with the pages of the tab on screen as parts.
    const broker = render("/settings/broker");
    expect(broker).toMatch(/data-page-tabs="settings".*>Account &amp; broker<.*>Trading<.*>AI<.*>App</s);
    expect(broker).toMatch(/aria-current="page"[^>]*data-page-part="broker"/);
    expect(render("/notifications")).toMatch(/data-page-parts="trading-defaults".*>Alerts<.*>Inbox</s);
    expect(render("/learn/news", { ui_v2: true, learn: true, idea_library: true })).toMatch(/data-page-tabs="learn".*>Lessons<.*>Words explained<.*>Ideas<.*>News &amp; events</s);
    expect(render("/signals")).not.toContain("data-page-tabs");
  });

  it("is the v2 shell: sidebar sections, bottom bar of five, search", () => {
    const html = render("/settings");
    expect(html).toContain('data-shell="v2"');
    expect(html).toContain('aria-label="Quick navigation"');
    expect(html).toContain("Search, e.g. reliance or nifty 22700 ce");   // UX-2: symbols first
    expect(html).not.toMatch(/aria-label="[^"]*[Ss]ymbol[^"]*"/);       // the Symbol fields keep their name to themselves
    expect(html).toContain("Skip to content");
    expect(html).toMatch(/>Today<.*>Charts<.*>Chain<.*>My trades<.*>More</s);
    // M57: the menu button left of the title - hides the sidebar from md up, opens the menu sheet on a phone.
    expect(html).toMatch(/data-menu-toggle="(expanded|rail)"[^>]*aria-label="(Hide|Show) menu"|aria-label="(Hide|Show) menu"[^>]*data-menu-toggle/);
    expect(html).toContain('aria-keyshortcuts="["');
    expect(html).toMatch(/data-menu-sheet[^>]*|aria-label="Show menu"[^>]*data-menu-sheet/);
    expect(html.indexOf("data-menu-toggle")).toBeLessThan(html.indexOf("data-topbar-title"));
  });
});
