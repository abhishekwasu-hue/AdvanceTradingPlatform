import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import OiViewV2 from "./OiViewV2";
import { OiTabView } from "./OiTabV2";
import { OI_BANNER, OI_HISTORY, OI_STRIKES } from "./oiTab.fixture";
import { ViewToggle } from "./OptionChainPageV2";
import pageSource from "./OptionChainPageV2.tsx?raw";

describe("UI-28 U6.1: the OI view in the v2 option chain", () => {
  it("is the third view beside Chain and Charts", () => {
    const html = renderToStaticMarkup(<ViewToggle view="oi" onView={() => {}} />);
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*OI/);
    expect(pageSource).toContain('{view === "oi" && (');
    expect(pageSource).toContain("<OiViewV2 underlying={symbol} />");
  });
  it("reads the OI first; the settings and alerts fold under one closed Details", () => {
    const html = renderToStaticMarkup(<OiViewV2 underlying="nifty" />);
    expect(html).toContain('data-oi-view="NIFTY"');
    expect(html).toContain("Reading the OI");
    expect(html).toContain("Settings and alerts");
    expect(html).not.toMatch(/<details[^>]*open/);
  });
  it("M61: shows the summary, the OI history and OI by strike once the readings are in", () => {
    const html = renderToStaticMarkup(<OiTabView underlying="NIFTY" interval={5} onInterval={() => {}} statusOpen={false} nextOpen={null}
      state={{ banner: OI_BANNER, history: OI_HISTORY, strikes: OI_STRIKES, error: null, loading: false }} />);
    expect(html).toContain('aria-label="OI summary"');
    expect(html).toContain('aria-label="NIFTY OI history"');
    expect(html).toContain('aria-label="NIFTY OI by strike"');
  });
});
