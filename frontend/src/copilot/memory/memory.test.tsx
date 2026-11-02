import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../../chartAnalyze/signalLanguage";
import sectionSource from "../../settings/SettingsSectionV2.tsx?raw";
import { MemoryForm } from "./CopilotMemoryV2";
import { EMPTY_MEMORY, MAX_INSTRUMENTS, drop, keep, type MemoryView } from "./model";

/** M72 PR-11 (CP-11): what the Copilot remembers - his own, shown, editable, deletable; suggestions only kept on his tap. */

const VIEW: MemoryView = {
  memory: { instruments: ["NIFTY"], timeframes: ["1d"], style: null },
  suggested: { instruments: [{ symbol: "NIFTY", count: 3, from: ["your trades"] }, { symbol: "TCS", count: 2, from: ["your watchlists"] }],
               timeframes: [{ timeframe: "15m", count: 1, from: ["your Copilot questions"] }] },
  styles: { swing: "Swing", intraday: "Intraday" },
};

describe("M72 PR-11 Copilot memory", () => {
  it("keeps a suggestion only on his tap, never twice, within the limits; drops what he removes", () => {
    expect(keep(EMPTY_MEMORY, { symbol: "TCS" }).instruments).toEqual(["TCS"]);
    expect(keep({ ...EMPTY_MEMORY, instruments: ["TCS"] }, { symbol: "TCS" }).instruments).toEqual(["TCS"]);
    const full = { ...EMPTY_MEMORY, instruments: Array.from({ length: MAX_INSTRUMENTS }, (_, i) => `S${i}`) };
    expect(keep(full, { symbol: "TCS" })).toBe(full);
    expect(keep(EMPTY_MEMORY, { timeframe: "1h" }).timeframes).toEqual(["1h"]);
    expect(drop({ instruments: ["A", "B"], timeframes: ["1d"], style: "swing" }, { symbol: "A" })).toEqual({ instruments: ["B"], timeframes: ["1d"], style: "swing" });
  });

  it("shows his saved items, suggestions he has not kept with their source, and the style Not set", () => {
    const html = renderToStaticMarkup(<MemoryForm view={VIEW} busy={false} onSave={() => undefined} onDelete={() => undefined} />);
    expect(html).toContain("Forget NIFTY") ;
    expect(html).toContain("+ TCS");
    expect(html).not.toContain("+ NIFTY");                                          // already kept
    expect(html).toContain('title="From your watchlists"');
    expect(html).toContain("+ 15 minutes");
    expect(html).toContain('data-combobox=""');                                      // style: Not set until he picks
    expect(hasCallWords(html.replace(/<[^>]+>/g, " "))).toBe(false);
  });

  it("sits in Settings › AI behind its flag", () => {
    expect(sectionSource).toContain('useFeatures().on("copilot_memory")');
    expect(sectionSource).toContain("<CopilotMemoryV2 />");
  });
});
