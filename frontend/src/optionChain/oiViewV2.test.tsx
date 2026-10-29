import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import OiViewV2 from "./OiViewV2";
import { ViewToggle } from "./OptionChainPageV2";
import pageSource from "./OptionChainPageV2.tsx?raw";

describe("UI-28 U6.1: the OI view in the v2 option chain", () => {
  it("is the third view beside Chain and Charts", () => {
    const html = renderToStaticMarkup(<ViewToggle view="oi" onView={() => {}} />);
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*OI/);
    expect(pageSource).toContain('{view === "oi" && (');
    expect(pageSource).toContain("<OiViewV2 underlying={symbol} />");
  });
  it("shows the banner, OI history and OI by strike; the settings and alerts fold under one closed Details", () => {
    const html = renderToStaticMarkup(<OiViewV2 underlying="nifty" />);
    expect(html).toContain('data-oi-view="NIFTY"');
    expect(html).toContain('aria-label="NIFTY OI history"');
    expect(html).toContain('aria-label="NIFTY OI by strike"');
    expect(html).toContain("Settings and alerts");
    expect(html).not.toMatch(/<details[^>]*open/);
  });
});
