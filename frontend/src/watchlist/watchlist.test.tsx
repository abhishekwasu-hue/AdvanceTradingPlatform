import { describe, expect, it } from "vitest";
import { flagOffFallback } from "../shell/aliases";
import { isItemVisible, navEntryFor, navItemFor, searchPages } from "../shell/navModel";
import { closedFlag, PAGE_FLAGS } from "../shell/pageFlags";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { isGatedPath, shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import { tourIdFor } from "../tours/pages";
import { USER_PAGE_ROOTS } from "../ux/bannedUiWords";
import shellSource from "../shell/AppShellV2.tsx?raw";
import pageSource from "./WatchlistPageV2.tsx?raw";
import { INTRO_LINE, NOT_FILLED_NOTE, readyMadeState } from "./model";

/** P1-8 (2) W-PR: the Watchlist page at /watchlist - v2 only, behind `watchlists`, the same panel as Charts. */

const flags = (...on: string[]) => (f: string) => on.includes(f);
const list = (count: number) => ({ key: `k${count}`, group: "INDICES", group_label: "Indices", name: "Indices", count, read_only: true as const });

describe("the ready-made lists' state", () => {
  it("filled only while a list has symbols; empty while the daily list update has not filled them; unknown otherwise", () => {
    expect(readyMadeState({ groups: [{ group: "INDICES", label: "Indices", lists: [list(3)] }] })).toBe("filled");
    expect(readyMadeState({ groups: [] })).toBe("empty");                            // the server leaves empty groups out
    expect(readyMadeState({ groups: [{ group: "INDICES", label: "Indices", lists: [list(0)] }] })).toBe("empty");
    expect(readyMadeState(null)).toBe("unknown");
    expect(readyMadeState({} as never)).toBe("unknown");
  });

  it("the page's words are plain: no ids, no flag names, no trade-call words", () => {
    for (const text of [NOT_FILLED_NOTE, INTRO_LINE]) {
      expect(text).not.toMatch(/watchlists\b|flag|sync|universe|api|buy|sell|target|stop/i);
    }
    expect(NOT_FILLED_NOTE).toBe("Ready-made lists: not filled yet");   // UI-28 U1.6: an empty-row line, no explanation
  });
});

describe("the shell wiring", () => {
  it("/watchlist is its own v2 page behind `watchlists`; with ui_v2 off it goes to the dashboard", () => {
    expect(shellKeyFromPath("/watchlist")).toBe("watchlist");
    expect(shellKeyFromPath("/watchlist/")).toBe("watchlist");
    expect(shellTitle("watchlist")).toBe("Watchlist");
    expect(PAGE_FLAGS.watchlist).toBe("watchlists");
    expect(closedFlag("watchlist", { answered: true, on: () => false })).toBe("watchlists");
    expect(closedFlag("watchlist", { answered: true, on: flags("watchlists") })).toBeNull();
    expect(flagOffFallback("/watchlist")).toBe("/");
    expect(PAGE_COMPONENTS_V2.watchlist).toBeTruthy();
    expect(shellSource).toMatch(/<Route path=\{WATCHLIST_ADDRESS\} caseSensitive element=\{<WatchlistPage onNavigate=\{go\} \/>\} \/>/);
    expect(shellSource).toMatch(/<BottomNavV2 [^>]*ctx=\{ctx\}/);
    // M60: the page stays at /watchlist (no Terminal Watchlist panel yet) and lights up the Terminal, its future home.
    expect(navEntryFor("watchlist")).toBe("terminal");
    expect(tourIdFor("watchlist")).toBeNull();
    expect(USER_PAGE_ROOTS).toContain("watchlist");             // the plain-words guard reads this page
  });

  it("search lists it only while the flag is on (M60: a part of the Terminal, no sidebar entry of its own)", () => {
    const item = navItemFor("watchlist")!;
    expect(item).toMatchObject({ label: "Watchlist", path: "/watchlist", section: "trade" });
    expect(isItemVisible(item, { on: flags("watchlists"), role: "OWNER" })).toBe(true);
    expect(isItemVisible(item, { on: flags(), role: "OWNER" })).toBe(false);
    expect(searchPages("my lists", { on: flags("watchlists"), role: "OWNER" })[0]?.key).toBe("watchlist");
  });

  it("the page needs no broker gate: list names show without a broker; prices come from the panel's own reads", () => {
    expect(isGatedPath("/watchlist")).toBe(false);
  });
});

describe("the page reuses the Charts panel as it is", () => {
  it("mounts WatchlistPanel with the Charts view's list choice and opens a row on Charts", () => {
    expect(pageSource).toContain('import { WatchlistPanel } from "../chart/WatchlistPanel"');
    expect(pageSource).toMatch(/listId=\{view\.watchlist_id\}/);
    expect(pageSource).toMatch(/systemKey=\{view\.watchlist_system\}/);
    expect(pageSource).toMatch(/navigate\(chartTabPath\(item\.symbol, item\.exchange\)\)/);
    expect(pageSource).toMatch(/liveTicks=\{features\.on\("data_hub"\)\}/);
    // Only the list choice is written back, onto a fresh read of the Charts view (Charts' other settings stay).
    expect(pageSource).toMatch(/\{ \.\.\.readView\(userId\), \.\.\.patch \}/);
  });

  it("text sizes and colours from the tokens only", () => {
    expect(pageSource).not.toMatch(/text-\[\d+px\]|#[0-9a-f]{3,6}\b|rgb\(/i);
  });
});
