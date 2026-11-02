import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DEFAULT_REGISTRY } from "../inplace";
import { askAiPath, askContextFrom, askContextLabel, signalObject } from "./askContext";
import { AboutChip } from "./chat/CopilotChatV2";
import { copilotI18n } from "./i18n";

/** M72 PR-3 (CP-3, F8): "Ask AI about this" - the context in the address, its chip, the in-place panel. Made-up ids. */
const params = (q: string) => { const s = new URLSearchParams(q); return (k: string) => s.get(k); };

describe("M72 PR-3 ask context", () => {
  it("round-trips through the address: symbol, timeframe, one object, the page", () => {
    const path = askAiPath({ symbol: "nifty", timeframe: "15min", object: { kind: "trade", id: 42 }, page: "positions" });
    expect(path).toBe("/copilot?symbol=NIFTY&tf=15min&ctx=trade%3A42&from=positions");
    expect(askContextFrom(params(path.split("?")[1]))).toEqual({ symbol: "NIFTY", timeframe: "15min", object: { kind: "trade", id: 42 }, page: "positions" });
    expect(askAiPath({})).toBe("/copilot");
  });

  it("drops anything malformed instead of guessing", () => {
    expect(askContextFrom(params("ctx=wallet:3&symbol=%3Cscript%3E&from=Bad Page"))).toBeNull();
    expect(askContextFrom(params("ctx=trade:0"))).toBeNull();
    expect(askContextFrom(params("ctx=trade:12abc"))).toBeNull();
    expect(askContextFrom(params("symbol=M%26M"))).toEqual({ symbol: "M&M" });
  });

  it("names the context in plain words and maps the Signals inbox ids", () => {
    expect(askContextLabel({ symbol: "NIFTY", timeframe: "15min" })).toBe("NIFTY · 15min");
    expect(askContextLabel({ symbol: "SBIN", object: { kind: "trade", id: 7 } })).toBe("Your trade · SBIN");
    expect(signalObject("history:12")).toEqual({ kind: "signal", id: 12 });
    expect(signalObject("trade:7")).toEqual({ kind: "trade", id: 7 });
    expect(signalObject("action:3")).toBeUndefined();
  });

  it("opens in place beside the page, with the context as the panel's params", () => {
    expect(DEFAULT_REGISTRY.resolve("/copilot?symbol=SBIN&ctx=trade:7&from=orders")).toEqual({ kind: "copilot", params: { symbol: "SBIN", ctx: "trade:7", from: "orders" } });
    expect(DEFAULT_REGISTRY.resolve("/copilot?symbol=NIFTY")).toEqual({ kind: "copilot", params: { symbol: "NIFTY" } });
  });

  it("the chip shows what the question is about and can be dropped", () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter>
      <AboutChip ctx={{ symbol: "NIFTY", object: { kind: "position", id: 1 } }} onClear={() => undefined} />
    </MemoryRouter></I18nextProvider>);
    expect(html).toContain("Your position · NIFTY");
    expect(html).toContain("data-ask-context-clear");
    expect(html).not.toMatch(/>1</);
  });
});
