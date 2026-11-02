import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import cardSource from "../../fno/IncompleteOrdersV2.tsx?raw";
import { IncompleteCardV2 } from "../../fno/IncompleteOrdersV2";
import { filledOf, type IncompleteItem } from "../../fno/incompleteApi";
import source from "./PartFillsV2.tsx?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { itemsHere, repriceWords } from "./PartFillsV2";

/** M76 MP-9 (E-2): per-leg part fills and the re-price helper - his confirm on the existing "Complete the rest" answer. */
const ITEM: IncompleteItem = {
  group_id: "g1", underlying: "NIFTY 50", strategy: "bull put spread", mode: "PAPER", state: "INCOMPLETE", message: "", failure: "No price",
  deadline: null, seconds_left: null, on_no_answer: null, rule: null, actions: ["complete_rest", "close_filled", "keep"],
  filled: [
    { trade_id: 1, symbol: "NIFTY31MAR24300PE", side: "BUY", role: "hedge", quantity: 150, entry_price: 35, open: true },
    { trade_id: 2, symbol: "NIFTY31MAR24400PE", side: "SELL", role: "sold", quantity: 75, entry_price: 60, open: true },
  ],
  pending: [{ symbol: "NIFTY31MAR24400PE", side: "SELL", role: "sold", quantity: 75, exchange: "NFO" }],
};

describe("part fills", () => {
  it("a part-filled leg reads N of M filled; its new last price is shown, or 'not known'", () => {
    expect(filledOf(ITEM, ITEM.pending[0])).toBe(75);
    const html = renderToStaticMarkup(<IncompleteCardV2 item={ITEM} secondsLeft={null} busy={false} onAnswer={() => undefined}
      ltp={{ NIFTY31MAR24400PE: 62.4 }} />);
    expect(html).toContain("75 of 150 filled");
    expect(html).toContain("last price 62.40");
    expect(renderToStaticMarkup(<IncompleteCardV2 item={ITEM} secondsLeft={null} busy={false} onAnswer={() => undefined} ltp={{}} />))
      .toContain("last price not known");
    // Positions' own card (no ltp given) is unchanged: no price line
    expect(renderToStaticMarkup(<IncompleteCardV2 item={ITEM} secondsLeft={null} busy={false} onAnswer={() => undefined} />)).not.toContain("last price");
  });

  it("the confirm names each leg's new last price and says nothing is sent twice", () => {
    const words = repriceWords(ITEM, { NIFTY31MAR24400PE: 62.4 });
    expect(words).toContain("NIFTY31MAR24400PE: last price 62.40");
    expect(words).toContain("Nothing is sent twice.");
    expect(repriceWords(ITEM, {})).toContain("last price not known");
  });

  it("only the chart's underlying (the server's aliases)", () => {
    expect(itemsHere([ITEM, { ...ITEM, group_id: "g2", underlying: "NIFTY BANK" }], "NIFTY").map((i) => i.group_id)).toEqual(["g1"]);
  });

  it("no new order path: the existing answer only, a confirm before Complete, a second tap sends nothing more", () => {
    expect(source).toContain("api.answer(item.group_id, action)");
    expect(source).not.toMatch(/request\(|\/order|basket\/execute|exit-now|placeOrder/);
    const fn = source.slice(source.indexOf("async function answer"), source.indexOf("const elapsed"));
    expect(fn.indexOf("inFlight.current.has")).toBeLessThan(fn.indexOf("confirmBox.ask"));
    expect(fn.indexOf("confirmBox.ask")).toBeLessThan(fn.indexOf("api.answer"));
    expect(fn).toMatch(/if \(action === "complete_rest"\)/);                  // only the entry asks; the exit choice is not held back
    expect(cardSource).toContain('onClick={() => onAnswer("close_filled")}');
    expect(workspaceSource).toContain("<PartFillsV2 symbol={pick.symbol} />");
  });
});
