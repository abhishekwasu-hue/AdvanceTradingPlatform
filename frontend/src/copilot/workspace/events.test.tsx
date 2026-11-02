import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import source from "./EventsV2.tsx?raw";
import rulesSource from "./events.ts?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { addOwn, eventChips, eventsNote, MAX_OWN_EVENTS, ownEventOf, readOwnEvents, type EventsApi, type WorkspaceEvents } from "./events";
import { EventsV2 } from "./EventsV2";
import { eventLine } from "../../fno/riskParts";

/** M76 MP-7 (S-3): the events on the workspace chart and in the check card - dates and names only, information only. */
const VIEW: WorkspaceEvents = {
  underlying: "NIFTY", from: "2031-03-10", until: "2031-03-27", more: 0, calendar_empty: false, message: null, source: "Your events calendar", blocks: false,
  expiries: [{ date: "2031-03-13", kind: "weekly", name: "Weekly expiry" }, { date: "2031-03-27", kind: "monthly", name: "Monthly expiry" }],
  events: [
    { date: "2031-03-12", kind: "RBI_POLICY", name: "RBI policy", scope: "all indices", rule: null, source: "market" },
    { date: "2031-03-13", kind: "OWN", name: "My review", scope: "NIFTY", rule: null, source: "own" },
  ],
};

describe("event rules", () => {
  it("chips by date, the expiry first on its day, his own marked as his", () => {
    const chips = eventChips(VIEW);
    expect(chips.map((c) => c.tone)).toEqual(["market", "expiry", "own", "expiry"]);
    expect(chips[0].text).toContain("RBI policy · all indices");
    expect(chips[1].text).toContain("Weekly expiry");
    expect(chips[2].text).toContain("My review (yours)");
    expect(chips[2].text).not.toContain("· NIFTY");                     // the chart's own symbol is not repeated
    expect(eventChips(null)).toEqual([]);
  });

  it("an empty calendar and no listed expiry say so; nothing is made up", () => {
    expect(eventsNote({ ...VIEW, expiries: [], events: [], calendar_empty: true, message: "Empty." })).toBe("Empty.");
    expect(eventsNote({ ...VIEW, expiries: [], events: [] })).toContain("No event in your events calendar");
    expect(eventsNote(VIEW)).toBeNull();
  });

  it("his own entry: date and words he typed, for this symbol or every symbol; at most the server's limit", () => {
    expect(ownEventOf("", "x", null)).toBe("Pick the date.");
    expect(ownEventOf("2031-03-12", "  ", null)).toBe("Type what the event is.");
    expect(ownEventOf("2031-03-12", "bad;name", null)).toContain("simple marks");
    expect(ownEventOf("2031-03-12", " My  review ", "nifty")).toEqual({ date: "2031-03-12", name: "My review", underlying: "NIFTY" });
    const one = { date: "2031-03-12", name: "B", underlying: null };
    expect(addOwn([{ date: "2031-03-20", name: "A", underlying: null }], one)?.map((e) => e.name)).toEqual(["B", "A"]);
    expect(addOwn(Array.from({ length: MAX_OWN_EVENTS }, () => one), one)).toBeNull();
    expect(readOwnEvents({ events: [one, { date: "x", name: "y" }, null] })).toEqual([one]);
    expect(readOwnEvents(undefined)).toEqual([]);
  });

  it("the check card's event line names his own event like any calendar entry", () => {
    const line = eventLine({ underlying: "NIFTY", from: "2031-03-10", until: "2031-03-13", clipped: false, more: 0, calendar_empty: false, message: null,
      source: "", blocks: false, events: [VIEW.events[1]] }, "2031-03-13");
    expect(line?.text).toContain("My review on");
  });
});

describe("the strip", () => {
  const api: EventsApi = {
    read: () => Promise.resolve(VIEW), loadOwn: () => Promise.resolve({ list: [], version: 0 }),
    saveOwn: () => Promise.reject(new Error("not in this test")),
  };
  it("renders his list section and nothing that places or holds back an order", () => {
    const html = renderToStaticMarkup(<EventsV2 symbol="NIFTY" api={api} />);
    expect(html).toContain("Your events");
    expect(html).toContain("Information only. An event never holds back an order or an exit.");
    for (const s of [source, rulesSource]) {
      expect(s).not.toMatch(/\/order|placeOrder|exit-now|basket\/execute|fno\/order/);
      expect(s).not.toMatch(/\b(buy|sell)\b/i);
    }
    expect(workspaceSource).toContain("<EventsV2 symbol={pick.symbol} />");
  });
});
