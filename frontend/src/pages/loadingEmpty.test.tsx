import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

/**
 * M63 I-7 (quality floor, part 1): while a page's list is on its way it shows the shared placeholder rows (Skeleton /
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
