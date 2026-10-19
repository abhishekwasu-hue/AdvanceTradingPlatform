import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { WatchlistPicker } from "./WatchlistPicker";
import { isWatchlists, loadNote, optionLabel, replaceWarning, shownList, splitSymbols, type Watchlist, type WatchlistClient } from "./watchlists";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&quot;/g, '"');
const list = (id: number, name: string, symbols: string[]): Watchlist =>
  ({ id, name, symbols, count: symbols.length, created_at: null, updated_at: null });
const BANKS = list(1, "Banks", ["HDFCBANK", "ICICIBANK", "SBIN"]);
const BIG = list(2, "Big", Array.from({ length: 60 }, (_, i) => `S${i}`));
const noClient: WatchlistClient = { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() };

describe("watchlist helpers (AMW-2)", () => {
  it("checks the server's shape", () => {
    expect(isWatchlists([BANKS])).toBe(true);
    expect(isWatchlists([])).toBe(true);
    expect(isWatchlists({ detail: "Not found" })).toBe(false);
    expect(isWatchlists([{ id: "1", name: "x", symbols: [] }])).toBe(false);
  });

  it("says when a list is cut to the page's cap or empty", () => {
    expect(loadNote(BANKS, 50)).toBe("");
    expect(loadNote(BIG, 50)).toBe('"Big" has 60 symbols; the first 50 are used here.');
    expect(loadNote(BIG)).toBe("");
    expect(loadNote(list(3, "Later", []))).toBe('"Later" has no symbols yet.');
    expect(loadNote(undefined)).toBe("");
    expect(optionLabel(BANKS)).toBe("Banks (3)");
  });

  it("shows the selected list, else the first remaining one (after a delete)", () => {
    expect(shownList([BANKS, BIG], 2)).toBe(BIG);
    expect(shownList([BANKS, BIG], "")).toBe(BANKS);
    expect(shownList([BIG], 1)).toBe(BIG);              // "Banks" was deleted
    expect(shownList([], "")).toBeUndefined();
  });

  it("asks before a replace would drop symbols", () => {
    expect(replaceWarning(BIG, 50)).toBe('"Big" has 60 symbols; replacing it keeps only these 50. Replace anyway?');
    expect(replaceWarning(BIG, 60)).toBeNull();
    expect(replaceWarning(BANKS, 4)).toBeNull();
  });

  it("reads the Scanner's symbol box like its run does", () => {
    expect(splitSymbols(" infy, tcs ,,M&M ")).toEqual(["INFY", "TCS", "M&M"]);
    expect(splitSymbols("")).toEqual([]);
  });
});

describe("WatchlistPicker (AMW-2)", () => {
  it("lists the caller's lists with a Load button and the manage panel", () => {
    const html = renderToStaticMarkup(<WatchlistPicker symbols={["INFY", "TCS"]} onLoad={() => undefined} initial={[BANKS, BIG]} client={noClient} />);
    const words = text(html);
    expect(words).toContain("My watchlists");
    expect(words).toContain("Banks (3)");
    expect(words).toContain("Big (60)");
    expect(html).toContain('data-testid="watchlist-load"');
    expect(words).toContain("Manage watchlists");
    expect(words).toContain("Save 2 as new");
    expect(words).toContain('Replace "Banks" with these 2');
    expect(words).toContain('Delete "Banks"');
    expect(noClient.list).not.toHaveBeenCalled();
  });

  it("after a delete, the select and Load point at the first remaining list", () => {
    // the selection still names the deleted list (id 1); the lists that are left start with "Big"
    const html = renderToStaticMarkup(<WatchlistPicker symbols={["INFY"]} onLoad={() => undefined} initial={[BIG]} initialSelected={1} client={noClient} />);
    expect(html).toMatch(/<option value="2" selected="">Big \(60\)<\/option>/);
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*data-testid="watchlist-load"/);
    expect(text(html)).toContain('Delete "Big"');
    // with nothing selected yet, the first list is shown and loadable
    const first = renderToStaticMarkup(<WatchlistPicker symbols={[]} onLoad={() => undefined} initial={[BANKS, BIG]} initialSelected="" client={noClient} />);
    expect(first).toMatch(/<option value="1" selected="">Banks \(3\)<\/option>/);
    expect(first).not.toMatch(/<button[^>]*disabled=""[^>]*data-testid="watchlist-load"/);
  });

  it("uses the page's label and says when there is no list yet", () => {
    const words = text(renderToStaticMarkup(<WatchlistPicker label="Load watchlist" symbols={[]} onLoad={() => undefined} initial={[]} client={noClient} />));
    expect(words).toContain("Load watchlist");
    expect(words).toContain("No watchlists yet.");
    expect(words).not.toContain("Replace");
  });

  it("renders nothing until the lists load (and so nothing while the flag is off)", () => {
    expect(renderToStaticMarkup(<WatchlistPicker symbols={[]} onLoad={() => undefined} client={noClient} />)).toBe("");
  });

  it("uses no Devanagari", () => {
    const words = text(renderToStaticMarkup(<WatchlistPicker symbols={["INFY"]} onLoad={() => undefined} initial={[BANKS]} client={noClient} />));
    expect(/[\u0900-\u097f]/.test(words)).toBe(false);
  });
});
