import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { INDEX_SYMBOLS, STOCK_SYMBOLS } from "../optionChain/model";
import { LiveBadge, PaperBadge } from "./PaperBadge";
import { PAPER_WORD, showsPaper } from "./paperWords";
import { OPTION_INDEX_ROWS, SymbolPicker, emptyRows, enterChoice, leaveChoice } from "./SymbolPicker";
import { OPTIONABLE, OPTION_INDICES, OPTION_STOCKS, isOptionable, searchOptionable } from "./symbolUniverse";

// CT-1 C6 + C8 (owner order 30 Oct): one symbol picker over one universe; one PAPER badge per screen.
const sources = import.meta.glob(["../**/*.tsx", "!../**/*.test.tsx", "!../stories/**"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("CT-1 C6: one symbol universe", () => {
  it("the option chain's list is the universe's optionable list", () => {
    expect(INDEX_SYMBOLS).toBe(OPTION_INDICES);
    expect(STOCK_SYMBOLS).toBe(OPTION_STOCKS);
    expect(OPTIONABLE.slice(0, 4).map((r) => r.symbol)).toEqual(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]);
    expect(OPTIONABLE.find((r) => r.symbol === "SENSEX")?.exchange).toBe("BSE");
  });

  it("searches the optionable symbols with no server call: starts-with first, then contains", () => {
    expect(searchOptionable("")).toEqual([]);
    const bank = searchOptionable("bank").map((r) => r.symbol);
    expect(bank[0]).toBe("BANKNIFTY");                       // starts with BANK
    expect(bank).toContain("AXISBANK");                      // contains BANK
    expect(searchOptionable("sensex").map((r) => r.symbol)).toEqual(["SENSEX"]);
    expect(searchOptionable("NOSUCHNAME")).toEqual([]);
    expect(searchOptionable("A", 5)).toHaveLength(5);
    expect(isOptionable(" reliance ")).toBe(true);
    expect(isOptionable("NOSUCHNAME")).toBe(false);
  });

  it("Enter in an optionable picker takes only a symbol with options", () => {
    const base = { open: true, rows: [], rowsFor: "X", active: -1, value: "NIFTY", exchange: "NSE" };
    expect(enterChoice({ ...base, text: "nosuch", accept: isOptionable })).toBeNull();
    expect(enterChoice({ ...base, text: "reliance", accept: isOptionable })).toEqual({ symbol: "RELIANCE", exchange: "NSE" });
    expect(enterChoice({ ...base, text: "nosuch" })).toEqual({ symbol: "NOSUCH", exchange: "NSE" });   // the whole list: as before
  });

  it("a form field keeps the typed symbol when left, clears when emptied, else shows the symbol again", () => {
    const base = { value: "TCS", exchange: "NSE", clearable: true };
    expect(leaveChoice({ ...base, text: " infy " })).toEqual({ symbol: "INFY", exchange: "NSE" });
    expect(leaveChoice({ ...base, text: "nifty   50" })).toEqual({ symbol: "NIFTY 50", exchange: "NSE" });
    expect(leaveChoice({ ...base, text: "tcs" })).toBeNull();
    expect(leaveChoice({ ...base, text: "" })).toEqual({ symbol: "", exchange: "NSE" });
    expect(leaveChoice({ ...base, text: "", clearable: false })).toBeNull();
    expect(leaveChoice({ ...base, text: "nosuch", accept: isOptionable })).toBeNull();
  });

  it("an empty field lists the recent symbols of its universe, then the suggestions", () => {
    const recent = [{ symbol: "TCS", exchange: "NSE" }, { symbol: "MYSMALLCAP", exchange: "NSE" }, { symbol: "NIFTY", exchange: "NSE" }];
    expect(emptyRows(recent, OPTION_INDEX_ROWS, "optionable").map((r) => r.symbol)).toEqual(["TCS", "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]);
    expect(emptyRows(recent, [], "all").map((r) => r.symbol)).toEqual(["TCS", "MYSMALLCAP", "NIFTY"]);
  });

  it("a caller's own label: the field takes its id, with no second hidden label", () => {
    const html = renderToStaticMarkup(<SymbolPicker id="f1" label="Symbol" value="NIFTY" exchange="NSE" storageKey={null} onPick={() => undefined} universe="optionable" />);
    expect(html).toContain('id="f1"');
    expect(html).not.toContain("<label");
    expect(html).toMatch(/role="combobox"[^>]*value="NIFTY"/);
    const own = renderToStaticMarkup(<SymbolPicker value="" exchange="NSE" storageKey={null} onPick={() => undefined} placeholder="All symbols" invalid />);
    expect(own).toContain('class="sr-only"');
    expect(own).toContain('placeholder="All symbols"');
    expect(own).toContain('aria-invalid="true"');
  });

  it("every symbol field named in the order is the one picker (no free-text symbol input left there)", () => {
    const pick = (f: string) => sources[`../${f}`];
    for (const f of ["components/AlertRulesPanel.tsx", "chartAnalyze/AlertDialogV2.tsx", "chartAnalyze/StrikesSectionV2.tsx", "trading/RiskPartsV2.tsx",
      "paperbot/PaperBotV2.tsx", "optionChain/OptionChainPageV2.tsx", "signals/TryOnSymbolV2.tsx"]) {
      expect(pick(f), f).toContain("<SymbolPicker");
    }
    expect(pick("optionChain/OptionChainPageV2.tsx")).toContain('universe="optionable"');
    expect(pick("chartAnalyze/StrikesSectionV2.tsx")).toContain('universe="optionable"');
    expect(pick("optionChain/OptionChainPageV2.tsx")).not.toContain("symbolOptions(");
    expect(pick("paperbot/PaperBotV2.tsx")).not.toContain("Another symbol");
  });
});

describe("CT-1 C8: one PAPER badge", () => {
  it("one word, its sentence as the tooltip; LIVE has its own badge", () => {
    const paper = renderToStaticMarkup(<PaperBadge />);
    expect(paper).toContain(`>${PAPER_WORD}</span>`);
    expect(paper).toContain("data-practice-frame");
    expect(paper).toContain('title="Practice mode: no real order is sent and no money moves."');
    const live = renderToStaticMarkup(<LiveBadge />);
    expect(live).toContain(">LIVE</span>");
    expect(live).toContain('data-mode-badge="live"');
  });

  it("a screen shows it while any paper row is on it", () => {
    expect(showsPaper([])).toBe(false);
    expect(showsPaper(["LIVE", "Live"])).toBe(false);
    expect(showsPaper(["Paper", "LIVE"])).toBe(true);
    expect(showsPaper(["PAPER", null])).toBe(true);
  });

  it("no hand-made PAPER chip or practice words left outside the one badge (the Terminal has its own step)", () => {
    for (const [f, src] of Object.entries(sources)) {
      if (f.startsWith("../terminal/") || f.endsWith("/PaperBadge.tsx")) continue;
      expect(src, f).not.toMatch(/<span[^>]*data-practice-frame[^>]*>/);
      expect(src, f).not.toMatch(/data-practice-frame>\{PRACTICE_TITLE\}/);
      expect(src, f).not.toContain('" (practice)"');
      expect(src, f).not.toContain('title="Practice account"');
      expect(src, f).not.toMatch(/<ChipV2[^>]*>\{(row|p)\.mode\}<\/ChipV2>/);
      expect(src, f).not.toContain("<ChipV2>Paper</ChipV2>");
    }
  });
});
