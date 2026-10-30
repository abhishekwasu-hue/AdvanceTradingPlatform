import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CommandList } from "./CommandBarV2";
import { searchPages } from "./navModel";
import { contractHit, mergeHits, parseContractQuery, recentHits } from "./symbolSearch";

const trader = { on: () => true, role: null };
const noop = () => {};

describe("UX-2: symbol-first search", () => {
  it("reads contract queries the way a trader types them", () => {
    expect(parseContractQuery("nifty 22700 ce")).toEqual({ underlying: "NIFTY", kind: "CE", strike: 22700 });
    expect(parseContractQuery("NIFTY22700CE")).toEqual({ underlying: "NIFTY", kind: "CE", strike: 22700 });
    expect(parseContractQuery("bank nifty 51000 put")).toEqual({ underlying: "BANKNIFTY", kind: "PE", strike: 51000 });
    expect(parseContractQuery("banknifty fut")).toEqual({ underlying: "BANKNIFTY", kind: "FUT", strike: undefined });
    expect(parseContractQuery("reliance 1400 call")).toEqual({ underlying: "RELIANCE", kind: "CE", strike: 1400 });
    expect(parseContractQuery("nifty 50")).toBeNull();                                // the index, not a strike
    expect(parseContractQuery("reliance 1400")).toBeNull();                           // no CE / PE / FUT: a symbol search
    expect(parseContractQuery("reliance")).toBeNull();
    expect(parseContractQuery("ce")).toBeNull();
    expect(parseContractQuery("22700 ce")).toBeNull();                               // no underlying
  });

  it("recent symbols come first, never twice, and match the text", () => {
    const recent = recentHits([{ symbol: "RELIANCE", exchange: "NSE", name: "Reliance Industries" }, { symbol: "NIFTY 50", exchange: "NSE" }], "rel");
    expect(recent.map((h) => h.label)).toEqual(["RELIANCE"]);
    const found = recentHits([{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "RELINFRA", exchange: "NSE" }], "").map((h) => ({ ...h, recent: false }));
    expect(mergeHits(recent, found).map((h) => h.label)).toEqual(["RELIANCE", "RELINFRA"]);
    expect(recent[0].type === "symbol" && recent[0].path).toBe("/charts/RELIANCE");
  });

  it("a contract row names it in words and opens the contract on the Charts page (CT-1 C1)", () => {
    const hit = contractHit({ key: "NFO:NIFTY:CE:2026-10-28:22700", tradingsymbol: "NIFTY26O2822700CE", underlying: "NIFTY", kind: "CE",
      expiry: "2026-10-28", strike: 22700, lot_size: 75 });
    expect(hit.label).toBe("NIFTY 28 Oct 22700 CE");
    expect(hit.type === "contract" && hit.path).toBe("/charts/NIFTY26O2822700CE?exchange=NFO");
    expect(hit.type === "contract" && hit.href).toBe("/?contract=NFO%3ANIFTY%3ACE%3A2026-10-28%3A22700");   // the old window, still reachable
    expect(hit.detail).toBe("Option · lot 75");
  });

  it("the list shows symbols above pages under small headings, one highlighted row across both", () => {
    const symbols = recentHits([{ symbol: "RELIANCE", exchange: "NSE" }], "");
    const html = renderToStaticMarkup(<CommandList query="" results={searchPages("orders", trader)} symbols={symbols} active={1} listId="l"
      optionId={(i) => `o${i}`} onPick={noop} onHover={noop} />);
    expect(html.indexOf("Recent symbols")).toBeLessThan(html.indexOf("RELIANCE"));
    expect(html.indexOf("RELIANCE")).toBeLessThan(html.indexOf(">Pages<"));
    expect(html).toMatch(/id="o0" role="option" aria-selected="false" data-command-symbol="symbol"/);
    expect(html).toMatch(/id="o1" role="option" aria-selected="true"/);
  });
});
