import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AdvancedFold } from "./AdvancedFold";
import { OptimiserFields } from "../backtest/OptimiserFields";
import { optimisableParams } from "../backtest/optimiserGrid";
import backtestSource from "../pages/BacktestPage.tsx?raw";
import librarySource from "../pages/StrategiesPage.tsx?raw";
import libraryChipsSource from "./LibraryChips.tsx?raw";

describe("SB-UX SU7: the Advanced fold", () => {
  it("is closed by default, with a 44 px summary on a phone", () => {
    const html = renderToStaticMarkup(<AdvancedFold id="x" title="Advanced settings"><p>inside</p></AdvancedFold>);
    expect(html).toMatch(/^<details data-advanced="x"/);
    expect(html).not.toMatch(/<details[^>]* open/);
    expect(html).toContain("min-h-11");
    expect(html).toContain("Advanced settings");
  });

  it("renders the children in place when off (the flag-off page is unchanged)", () => {
    expect(renderToStaticMarkup(<AdvancedFold id="x" on={false}><p>inside</p></AdvancedFold>)).toBe("<p>inside</p>");
  });

  it("the optimiser shows fields, never a JSON box", () => {
    const html = renderToStaticMarkup(<OptimiserFields params={optimisableParams({ ema_fast: 9 })} rows={[{ key: "ema_fast", from: "", to: "", step: "" }]}
      onRows={() => undefined} metric="net_pnl" onMetric={() => undefined} onRun={() => undefined} busy={false} ready />);
    for (const label of ["Setting", "From", "To", "Step", "Rank by"]) expect(html).toContain(`>${label}</label>`);
    expect(html).toContain("Fast EMA: fill From, To and Step.");
    expect(html).not.toMatch(/JSON|font-mono/);
  });

  it("no raw JSON, no fixed min width and every wide table scrolls (Backtest, Library)", () => {
    expect(backtestSource).not.toMatch(/JSON\.(parse|stringify)|min-w-\[260px\]|Grid as JSON/);
    expect(librarySource).not.toMatch(/JSON\.stringify/);
    expect(librarySource).toContain("<LibraryChips ");      // the chips (data-rule-chips) live in LibraryChips since STRAT-X
    expect(libraryChipsSource).toContain("data-rule-chips");
    const tables = backtestSource.match(/<table /g)?.length ?? 0;
    const wrapped = (backtestSource.match(/(?:overflow-x-auto">|<ScrollTable>)\s*<table /g) ?? []).length;
    expect(wrapped).toBe(tables);
  });
});
