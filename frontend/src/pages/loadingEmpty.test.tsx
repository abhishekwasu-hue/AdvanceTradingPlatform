import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

/**
 * M63 I-7 (quality floor, parts 1 and 2): while a page's list is on its way it shows the shared placeholder rows (Skeleton /
 * SkeletonRows), never a blank block or "No ... yet" before the answer; an empty list says one plain line (EmptyStateV2)
 * with at most one action. Rendered on the server: the first screen is the "still loading" one.
 */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "USER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});
// The Notifications feed's reads: by default no answer; `feed.answer` hands the page an empty feed once.
const feed = vi.hoisted(() => ({ answer: false }));
vi.mock("../live/useNotificationReads", () => ({
  useNotificationReads: (_on: boolean, t: { setNotifications: (rows: unknown[]) => void }) => {
    if (feed.answer) { feed.answer = false; t.setNotifications([]); }
    return { refresh: () => {}, refreshFeed: () => {} };
  },
}));

import NotificationsPage from "./NotificationsPage";
import SystemLogsPage, { EventRows, LoginRows } from "./SystemLogsPage";
import StrategyBuilderPage, { SavedStrategyRows } from "./StrategyBuilderPage";
import NewsEventsPage from "./NewsEventsPage";
import { LibraryView } from "./StrategyLibraryV2";
import ChartWindow from "./ChartWindow";
import OrdersPage from "./OrdersPage";
import ordersSource from "./OrdersPage.tsx?raw";
import PortfolioPage from "./PortfolioPage";
import portfolioSource from "./PortfolioPage.tsx?raw";
import DashboardPage from "./DashboardPage";
import dashboardSource from "./DashboardPage.tsx?raw";
import { StatusBarV2 } from "../terminal/StatusBarV2";
import panelsSource from "../terminal/PanelsV2.tsx?raw";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const busy = (html: string, label: string) => expect(html).toContain(`aria-busy="true" aria-label="${label}"`);
const emptyLine = (html: string) => textOf(html.split("data-empty")[1] ?? "");

describe("M63 I-7: placeholder rows while loading, one line when empty", () => {
  it("Notifications: placeholder rows before the feed answers, then one line", () => {
    const loading = renderPage(<NotificationsPage />);
    busy(loading, "Loading your notifications");
    expect(textOf(loading)).not.toContain("No notifications yet");
    feed.answer = true;
    const empty = renderPage(<NotificationsPage />);
    expect(empty).not.toContain("Loading your notifications");
    expect(emptyLine(empty)).toContain("No notifications yet.");
    expect(empty.match(/data-empty/g)?.length).toBe(1);
    expectNoDeveloperText(empty);
  });

  it("System logs: both lists show placeholder rows first, then one line each", () => {
    const loading = renderPage(<SystemLogsPage />);
    busy(loading, "Loading your login history");
    busy(loading, "Loading your events");
    expect(textOf(loading)).not.toMatch(/No (login attempts|events) yet/);
    expect(textOf(loading)).not.toContain("(0)");
    expect(textOf(renderToStaticMarkup(<LoginRows logins={[]} />))).toBe("No login attempts yet.");
    expect(textOf(renderToStaticMarkup(<EventRows logs={[]} />))).toBe("No events yet.");
    expect(renderToStaticMarkup(<EventRows logs={[]} />)).toContain("data-empty");
  });

  it("Strategy builder: the saved list shows placeholder rows first, then one line", () => {
    const loading = renderPage(<StrategyBuilderPage />);
    busy(loading, "Loading your saved strategies");
    expect(textOf(loading)).not.toContain("None yet");
    const empty = renderToStaticMarkup(<SavedStrategyRows saved={[]} onDelete={() => {}} />);
    expect(empty).toContain("data-empty");
    expect(textOf(empty)).toBe("None yet - build one below.");
  });

  it("News & events: placeholder rows before the list answers (never 'No events match' first)", () => {
    const loading = renderPage(<NewsEventsPage />);
    busy(loading, "Loading");
    expect(textOf(loading)).not.toContain("No events match this filter.");
  });

  it("Strategy library: placeholder cards while the list loads, one line + Show all when a filter is empty", () => {
    const props = { error: null, inUse: null, category: "all", query: "", chosen: null,
      onCategory: () => {}, onQuery: () => {}, onChoose: () => {}, onTry: () => {} };
    const loading = renderPage(<LibraryView {...props} strategies={null} />);
    busy(loading, "Loading strategies");
    expect(loading).not.toContain("data-library-empty");
    const empty = renderPage(<LibraryView {...props} strategies={[]} />);
    expect(empty).not.toContain("Loading strategies");
    const line = textOf(empty.split("data-library-empty")[1]);
    expect(line).toContain("No strategies here yet.");
    expect((empty.split("data-library-empty")[1].match(/<button/g) ?? []).length).toBe(1);
  });
});

/** The JSX of the branch a page renders when its list answered empty (one EmptyStateV2 line, at most one action). */
const emptyLines = (source: string) => [...source.matchAll(/<EmptyStateV2 text="([^"]+)"([^/]*)\/>/g)].map((m) => ({ text: m[1], rest: m[2] }));

describe("M63 I-7 part 2: placeholders before the first answer, one line when empty", () => {
  it("Chart window: a chart-sized placeholder, no bare \"Loading…\" line", () => {
    const html = renderPage(<ChartWindow params={new URLSearchParams("chart=NIFTY&tf=15min")} />);
    busy(html, "Loading the chart");
    expect(textOf(html)).not.toContain("Loading…");
    expectNoDeveloperText(html);
  });

  it("Orders: the heading, placeholder rows and no count before the answer; one line when empty", () => {
    const html = renderPage(<OrdersPage />);
    expect(textOf(html)).toContain("Orders");
    busy(html, "Loading your orders");
    expect(textOf(html)).not.toMatch(/No orders yet|\(0\)/);
    expectNoDeveloperText(html);
    expect(emptyLines(ordersSource)).toEqual([{ text: "No orders yet - your entry and exit fills show here.", rest: " " }]);
  });

  it("Portfolio: placeholder figures, curve and rows before the answer; one line when empty", () => {
    const html = renderPage(<PortfolioPage />);
    expect(textOf(html)).toContain("Portfolio");
    busy(html, "Loading your open positions");
    busy(html, "Loading your P&amp;L curve");
    expect(html.match(/data-skeleton-block/g)?.length ?? 0).toBeGreaterThanOrEqual(5);    // four tiles + the curve
    expect(textOf(html)).not.toMatch(/No open positions|Not enough trades|₹\s?0\b/);
    expectNoDeveloperText(html);
    expect(emptyLines(portfolioSource).map((l) => l.text)).toEqual(["No open positions - capital shows here once a position is open."]);
  });

  it("Dashboard: placeholder rows in the four book cards; empty cards say one line, deployments keep its one action", () => {
    const html = renderPage(<DashboardPage />);
    expect(textOf(html)).toMatch(/Good (morning|afternoon|evening), trader/);
    for (const label of ["Loading your exposure", "Loading your deployments", "Loading your open positions", "Loading your alerts"]) busy(html, label);
    expect(textOf(html)).not.toMatch(/Nothing deployed yet|exposure is empty|Flat\. Nothing at risk|Quiet\. Entries/);
    expectNoDeveloperText(html);
    const lines = emptyLines(dashboardSource);
    expect(lines.map((l) => l.text)).toEqual(["No open positions - exposure is empty.", "Nothing deployed yet.",
      "Flat. Nothing at risk right now.", "Quiet. Entries, exits and risk events will show here."]);
    expect(lines.filter((l) => l.rest.includes("action=")).map((l) => l.text)).toEqual(["Nothing deployed yet."]);
  });

  it("Terminal status bar: placeholder figures while loading (read out, not shown as a text line); the failed line stays", () => {
    const bar = (failed: boolean) => renderToStaticMarkup(<StatusBarV2 status={null} failed={failed} masked={false} onMask={() => {}} />);
    const loading = bar(false);
    expect(loading).toContain('aria-busy="true"');
    expect(loading).toContain('<span class="sr-only">Loading your figures</span>');
    expect(loading).not.toContain("Loading your figures…");
    expect(loading.match(/data-skeleton-block/g)?.length).toBe(8);                        // four figures, label + value
    const compact = renderToStaticMarkup(<StatusBarV2 status={null} failed={false} masked={false} onMask={() => {}} compact />);
    expect(compact.match(/data-skeleton-block/g)?.length).toBe(4);
    expect(bar(true)).not.toContain("data-skeleton");
    expect(textOf(bar(true))).toContain("Your figures could not be read. We will try again in a moment.");
  });

  it("Terminal orders panel: an order's steps load as placeholder rows, never a text line", () => {
    expect(panelsSource).toContain(`trail.status === "loading" ? <SkeletonRows rows={2} label="Loading the order's steps" />`);
    expect(panelsSource).not.toContain("Loading the order&apos;s steps…");
  });

  it("Strategy library: no \"0 Ready to try\" and no chip counts while the list loads", () => {
    const props = { error: null, inUse: new Set<string>(), category: "all", query: "", chosen: null,
      onCategory: () => {}, onQuery: () => {}, onChoose: () => {}, onTry: () => {} };
    const loading = renderPage(<LibraryView {...props} strategies={null} />);
    const stats = loading.split("data-library-stat").slice(1);
    expect(stats.length).toBeGreaterThanOrEqual(2);
    for (const stat of stats) expect(stat.split("</div></div>")[0]).toContain("data-skeleton-block");
    expect(textOf(loading)).not.toMatch(/\b0 (Ready to try|You use now|Categories)\b/);
    expect(textOf(`<x ${loading.split('aria-label="Category"')[1].split("</div>")[0]}`)).not.toMatch(/\d/);
    const ready = renderPage(<LibraryView {...props} strategies={[]} />);
    expect(textOf(ready)).toContain("0 Ready to try");
    expect(textOf(`<x ${ready.split('aria-label="Category"')[1].split("</div>")[0]}`)).toMatch(/All 0/);
  });
});
