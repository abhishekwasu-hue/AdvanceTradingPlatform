import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { cardTextProblems } from "../ui/textBudget";
import { MyDayView } from "./MyDayCardV2";
import { factsLine, sinceLines, sortPositions, type MyDay, type MyDayPosition } from "./myDay";

const pos = (id: number, over: Partial<MyDayPosition> = {}): MyDayPosition => ({
  id, symbol: `SYM${id}`, mode: "PAPER", side: "LONG", kind: "UNDERLYING", underlying: `SYM${id}`, deployment_id: null,
  chip: "safe", chip_label: "Safe", room_left_pct: 80, chip_text: null, expiry: null, expiry_sessions: null, expiry_text: null,
  area: null, area_text: "No structure areas stored for SYM yet.", ...over,
});

const day = (positions: MyDayPosition[], since: Partial<MyDay["since_last_session"]> = {}): MyDay => ({
  as_of: "2026-10-29T05:00:00Z", today: "2026-10-29", previous_session: "2026-10-28", note: "Information only.",
  thresholds: { watch_room_left_pct: 50, near_room_left_pct: 25 }, positions,
  since_last_session: { gaps: [], events: [], areas: [], empty: null, ...since },
});

const render = (d: MyDay) => renderToStaticMarkup(<MemoryRouter><MyDayView state={{ kind: "ok", data: d }} /></MemoryRouter>);

describe("AUD-T1: My day in one card", () => {
  it("what needs the eye first: near, watch, no chip, then safe", () => {
    const rows = [pos(1), pos(2, { chip: null, chip_label: null, chip_text: "No stop set" }), pos(3, { chip: "near", chip_label: "Near invalidation" }),
      pos(4, { chip: "watch", chip_label: "Watch" })];
    expect(sortPositions(rows).map((p) => p.id)).toEqual([3, 4, 2, 1]);
  });

  it("a row: the symbol, one chip from the user's own stop, expiry and the nearest area once", () => {
    const p = pos(7, { symbol: "NIFTY 24500 CE", chip: "watch", chip_label: "Watch", expiry_text: "Expires in 2 sessions",
      area_text: "Nearest area is 0.41% below your strike." });
    expect(factsLine(p)).toBe("Expires in 2 sessions · Nearest area is 0.41% below your strike.");
    const html = render(day([p]));
    expect(html).toContain("My day");
    expect(html).toContain('data-chip="my-day-watch"');
    expect(html).toContain("NIFTY 24500 CE");
  });

  it("no stop: no chip, the plain words", () => {
    const html = render(day([pos(1, { chip: null, chip_label: null, chip_text: "No stop set" })]));
    expect(html).not.toContain("data-chip=");
    expect(html).toContain("No stop set");
  });

  it("since the last session: each line once; the explanation sits in Details", () => {
    const d = day([pos(1), pos(2)], { gaps: [{ underlying: "NIFTY", session: "2026-10-29", gap_pct: 0.4, text: "NIFTY opened 0.40% above the previous close today." }],
      events: ["Today: RBI policy.", "Today: RBI policy."], areas: [] });
    expect(sinceLines(d)).toEqual(["NIFTY opened 0.40% above the previous close today.", "Today: RBI policy."]);
    const html = render(d);
    expect(html).toContain("Since the last session");
    expect(html.match(/Today: RBI policy\./g)).toHaveLength(1);
    expect(html).toMatch(/data-details="today.my-day"[\s\S]*Watch at 50% or less, near invalidation at 25% or less/);
  });

  it("every visible line stays within the text budget", () => {
    const lines = [factsLine(pos(1, { expiry_text: "Expires next session", area_text: "Nearest area is 1.25% above your stop." })),
      "BANKNIFTY area 52,100.00-52,300.00 was closed through and held beyond since the last session.",
      "NIFTY opened 0.40% above the previous close on Wed 28 Oct."];
    expect(cardTextProblems(lines.map((l) => ({ card: "My day", lines: [l] })))).toEqual([]);
  });

  it("flag off, no open position: no card; loading and failure say so plainly", () => {
    expect(renderToStaticMarkup(<MemoryRouter><MyDayView state={{ kind: "off" }} /></MemoryRouter>)).toBe("");
    expect(render(day([]))).toBe("");
    expect(renderToStaticMarkup(<MemoryRouter><MyDayView state={{ kind: "error" }} /></MemoryRouter>)).toContain("We could not load your day");
  });

  it("Today renders the card only while my_day_card is on, under the market strip", () => {
    const src = readFileSync(new URL("./DashboardV2.tsx", import.meta.url), "utf8");
    expect(src).toContain('{features.on("my_day_card") && <MyDayCardV2 />}');
    expect(src.indexOf("<MarketStripV2")).toBeLessThan(src.indexOf("<MyDayCardV2"));
    expect(src.indexOf("<MyDayCardV2")).toBeLessThan(src.indexOf("<NeedsAttentionV2"));
  });
});
