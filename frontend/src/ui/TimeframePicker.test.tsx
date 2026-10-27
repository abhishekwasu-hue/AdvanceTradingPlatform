/** TF-1 (owner 25 Oct): the one timeframe control - grouped list, custom interval, stars, favourites, saved per user. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_FAVOURITES, TIMEFRAMES, cleanFavourites, groupTimeframes, parseCustomInterval, servedText, shownFavourites, toggleFavourite,
  type TimeframeId,
} from "../chart/timeframes";
import { ChartPrefsStore, DEFAULT_CHART_PREFS } from "../chart/useChartPrefs";
import { TimeframePicker, favouritesAfterAdd, menuMove, opensCustomBox } from "./TimeframePicker";

const ALL: TimeframeId[] = [...TIMEFRAMES];
const NO_PERIODS: TimeframeId[] = ALL.filter((tf) => tf !== "week" && tf !== "month");

describe("TF-1 model", () => {
  it("groups the offered timeframes as minutes / hours / days / weeks / months, leaving out an empty group", () => {
    expect(groupTimeframes(ALL)).toEqual([
      { label: "Minutes", ids: ["1min", "3min", "5min", "15min", "30min"] }, { label: "Hours", ids: ["60min"] },
      { label: "Days", ids: ["day"] }, { label: "Weeks", ids: ["week"] }, { label: "Months", ids: ["month"] }]);
    expect(groupTimeframes(NO_PERIODS).map((g) => g.label)).toEqual(["Minutes", "Hours", "Days"]);
  });

  it("default favourites are 5m, 15m, 1H and D; a stored list is cleaned (known, once, in order)", () => {
    expect(DEFAULT_FAVOURITES).toEqual(["5min", "15min", "60min", "day"]);
    expect(cleanFavourites(undefined)).toEqual(["5min", "15min", "60min", "day"]);
    expect(cleanFavourites(["day", "2min", "1min", "day"])).toEqual(["1min", "day"]);
    expect(cleanFavourites([])).toEqual([]);
  });

  it("a star toggles a timeframe in or out, keeping the list's order", () => {
    expect(toggleFavourite(["5min", "day"], "1min")).toEqual(["1min", "5min", "day"]);
    expect(toggleFavourite(["5min", "day"], "day")).toEqual(["5min"]);
  });

  it("the buttons show only what the chart offers; a phone (or a small cell) at most three", () => {
    expect(shownFavourites(["5min", "15min", "60min", "day", "week"], NO_PERIODS, false)).toEqual(["5min", "15min", "60min", "day"]);
    expect(shownFavourites(["1min", "5min", "15min", "60min", "day"], ALL, true)).toEqual(["1min", "5min", "15min"]);
  });

  it("a custom interval the charts can show is accepted; any other is refused in plain words", () => {
    expect(parseCustomInterval("15", "minutes", ALL)).toEqual({ ok: true, id: "15min" });
    expect(parseCustomInterval("60", "minutes", ALL)).toEqual({ ok: true, id: "60min" });
    expect(parseCustomInterval("1", "hours", ALL)).toEqual({ ok: true, id: "60min" });
    expect(parseCustomInterval("1", "weeks", ALL)).toEqual({ ok: true, id: "week" });
    expect(parseCustomInterval("1", "weeks", NO_PERIODS)).toMatchObject({ ok: false });
    expect(parseCustomInterval("2", "hours", ALL)).toEqual({ ok: false,
      message: "A 2 hours chart is not available yet. Charts here can show 1, 3, 5, 15 and 30 minutes, 1 hour, daily, weekly and monthly." });
    expect(parseCustomInterval("abc", "minutes", ALL)).toEqual({ ok: false, message: "Type a whole number, for example 15." });
    expect(parseCustomInterval("0", "minutes", ALL)).toMatchObject({ ok: false });
    expect(servedText(NO_PERIODS)).toBe("1, 3, 5, 15 and 30 minutes, 1 hour and daily");
  });
});

describe("TF-1 control", () => {
  const noop = () => undefined;

  it("favourites are one-click toggle buttons beside one dropdown; no 9-button row, no More row", () => {
    const html = renderToStaticMarkup(<TimeframePicker offered={ALL} value="15min" onChange={noop} favourites={DEFAULT_FAVOURITES} onFavourites={noop} />);
    expect(html).toMatch(/role="group" aria-label="Timeframe"/);
    expect([...html.matchAll(/data-favourite="true"/g)]).toHaveLength(4);
    expect(html).toMatch(/aria-pressed="true"[^>]*data-subtab="15min"/);
    expect(html).toContain('aria-haspopup="menu"');
    expect(html).toContain('aria-label="Timeframe: 15 min"');
    expect(html).not.toContain('data-subtab="3min"');
  });

  it("a timeframe that is not a favourite shows on the dropdown button, filled", () => {
    const html = renderToStaticMarkup(<TimeframePicker offered={ALL} value="week" onChange={noop} favourites={DEFAULT_FAVOURITES} onFavourites={noop} />);
    expect(html).toMatch(/aria-label="Timeframe: Weekly"[^>]*data-active="true"[^>]*class="[^"]*bg-brand/);
    expect(html).not.toMatch(/aria-pressed="true"/);
  });

  it("compact shows at most three favourites", () => {
    const html = renderToStaticMarkup(<TimeframePicker offered={ALL} value="5min" onChange={noop} compact favourites={DEFAULT_FAVOURITES} onFavourites={noop} />);
    expect([...html.matchAll(/data-favourite="true"/g)]).toHaveLength(3);
  });

  it("the open list is grouped, one menuitemradio per timeframe with a star toggle (aria-pressed), then Add custom interval", () => {
    const html = renderToStaticMarkup(<TimeframePicker offered={ALL} value="5min" onChange={noop} favourites={DEFAULT_FAVOURITES} onFavourites={noop} defaultOpen />);
    expect([...html.matchAll(/role="group" aria-label="(Minutes|Hours|Days|Weeks|Months)"/g)].map((m) => m[1])).toEqual(["Minutes", "Hours", "Days", "Weeks", "Months"]);
    expect([...html.matchAll(/role="menuitemradio"/g)]).toHaveLength(9);
    expect(html).toMatch(/role="menuitemradio" aria-checked="true" tabindex="0" data-timeframe="5min"/);   // roving focus starts on the pick
    expect(html).toMatch(/aria-pressed="true" tabindex="-1" data-star="5min" aria-label="Remove 5 min from favourites"/);
    expect(html).toMatch(/aria-pressed="false" tabindex="-1" data-star="3min" aria-label="Add 3 min to favourites"/);
    expect(html).toContain("Add custom interval");
  });

  it("keys in the list: up / down / home / end move between timeframes (wrapping), right to the star, left back", () => {
    expect(menuMove(9, 0, false, "ArrowDown")).toEqual({ to: 1, star: false });
    expect(menuMove(9, 8, false, "ArrowDown")).toEqual({ to: 0, star: false });
    expect(menuMove(9, 0, false, "ArrowUp")).toEqual({ to: 8, star: false });
    expect(menuMove(9, 4, true, "Home")).toEqual({ to: 0, star: false });
    expect(menuMove(9, 4, false, "End")).toEqual({ to: 8, star: false });
    expect(menuMove(9, 4, false, "ArrowRight")).toEqual({ to: 4, star: true });
    expect(menuMove(9, 4, true, "ArrowLeft")).toEqual({ to: 4, star: false });
    expect(menuMove(9, 4, true, "ArrowRight")).toBeNull();
    expect(menuMove(9, -1, false, "ArrowDown")).toBeNull();
  });

  it("an added custom interval is starred once; a digit typed outside a field opens the box", () => {
    expect(favouritesAfterAdd(["5min", "day"], "30min")).toEqual(["5min", "30min", "day"]);
    expect(favouritesAfterAdd(["5min", "day"], "5min")).toEqual(["5min", "day"]);
    expect(opensCustomBox("4", false, false)).toBe(true);
    expect(opensCustomBox("4", true, false)).toBe(false);      // Ctrl / Cmd / Alt + digit stays the browser's
    expect(opensCustomBox("4", false, true)).toBe(false);      // typing in a field
    expect(opensCustomBox("a", false, false)).toBe(false);
  });
});

describe("TF-1 favourites are saved per user (chart prefs)", () => {
  it("a star change is sent with the rest of the chart settings and read back", async () => {
    const sent: unknown[] = [];
    const fetcher = (async (path: string, init?: RequestInit) => {
      if (path === "/me/prefs") return { prefs: { chart: { ...DEFAULT_CHART_PREFS, timeframe_favourites: ["day"] } }, versions: { chart: 1 } };
      sent.push(JSON.parse(String(init?.body)));
      return { namespace: "chart", value: {}, version: 2 };
    }) as unknown as ConstructorParameters<typeof ChartPrefsStore>[1];
    const store = new ChartPrefsStore(`tf1-${Math.random()}`, fetcher);
    await store.loadOnce();
    expect(store.get().prefs.timeframe_favourites).toEqual(["day"]);
    await store.update({ timeframe_favourites: toggleFavourite(store.get().prefs.timeframe_favourites, "1min") });
    expect(sent[0]).toMatchObject({ timeframe_favourites: ["1min", "day"] });
    expect(store.get().prefs.timeframe_favourites).toEqual(["1min", "day"]);
  });
});
