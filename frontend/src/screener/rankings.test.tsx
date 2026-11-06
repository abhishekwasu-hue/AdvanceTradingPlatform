import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import RankingsPanel from "./RankingsPanel";
import { allSystems, isRankingRun, isRankingSystems, notRankedList, rankTone, systemLabel } from "./rankings";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const REPLY = {
  platform: [{ id: null, name: "platform:Momentum", description: "d", platform: true, archived: false }],
  systems: [{ id: 3, name: "Mine", description: "", platform: false, archived: false }, { id: 4, name: "Old", description: "", platform: false, archived: true }],
  disclaimer: "x",
};

describe("FN2-k rankings", () => {
  it("checks the server's shapes", () => {
    expect(isRankingSystems(REPLY)).toBe(true);
    expect(isRankingSystems({ detail: "Not found" })).toBe(false);
    expect(isRankingRun({ ranked: [{ symbol: "A", components: [] }], scanned: 1 })).toBe(true);
    expect(isRankingRun({ ranked: [{ symbol: "A" }], scanned: 1 })).toBe(false);
  });
  it("lists platform systems first, leaves archived ones out and drops the prefix", () => {
    expect(allSystems(REPLY).map((s) => s.name)).toEqual(["platform:Momentum", "Mine"]);
    expect(systemLabel(REPLY.platform[0])).toBe("Momentum");
  });
  it("colours a rank by where it stands, never a missing one", () => {
    expect([rankTone(90), rankTone(50), rankTone(10), rankTone(null), rankTone(NaN)]).toEqual(["up", "flat", "down", "flat", "flat"]);
  });
  it("lists the symbols that could not be ranked in symbol order", () => {
    expect(notRankedList({ not_ranked: { B: "b", A: "a" } })).toEqual([{ symbol: "A", why: "a" }, { symbol: "B", why: "b" }]);
  });
  it("loads before it lists (no request text or ids on screen)", () => {
    const api = { list: vi.fn(() => new Promise<never>(() => undefined)), run: vi.fn() };
    expect(text(renderToStaticMarkup(<RankingsPanel api={api} symbols={["A"]} />))).not.toMatch(/screener\/rankings|system_id|404/);
    expect(api.run).not.toHaveBeenCalled();
  });
});
