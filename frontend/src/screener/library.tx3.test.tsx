import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LibraryTools } from "./LibraryTools";
import { greyed, isDigestState, isRegime, regimeText } from "./library";
import { SAMPLE_DIGEST, SAMPLE_MIRRORS, SAMPLE_REGIME } from "./librarySample";
import { ScreenGallery } from "./ScreenGallery";
import sample from "./taxonomySample.json";
import type { Taxonomy } from "./taxonomy";

const tax = sample as unknown as Taxonomy;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'");

describe("U9 TX3 regime helpers", () => {
  it("guards the responses, names today's regime and greys only a clear mismatch", () => {
    expect(isRegime(SAMPLE_REGIME) && isDigestState(SAMPLE_DIGEST)).toBe(true);
    expect(isRegime({ detail: "off" }) || isDigestState(null)).toBe(false);
    expect(regimeText(SAMPLE_REGIME, tax)).toBe("Range-bound market · High volatility");
    expect(regimeText({ ...SAMPLE_REGIME, trend: { ...SAMPLE_REGIME.trend, value: null } }, tax)).toBe("trend unclear · High volatility");
    expect(greyed(SAMPLE_REGIME, "starter.momentum")?.reason).toContain("designed for a trending market");
    expect(greyed(SAMPLE_REGIME, "starter.oversold")).toBeNull();
    expect(greyed(null, "starter.momentum")).toBeNull();
  });
});

describe("ScreenGallery with today's regime (U9 TX3)", () => {
  it("greys the screens designed for another regime, says why, keeps Open, and names today's reading", () => {
    const html = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} regime={SAMPLE_REGIME} />);
    expect(html.match(/data-greyed="true"/g)).toHaveLength(2);
    expect(html).toContain('data-testid="regime-banner"');
    const t = text(html);
    expect(t).toContain("Today (NIFTY 50, session 2026-10-09): Range-bound market · High volatility.");
    expect(t).toContain("Not today's regime: designed for a trending market");
    expect(html.match(/aria-label="Open [^"]+ in the builder"/g)).toHaveLength(3);         // greyed screens still open
    expect(html).not.toMatch(/aria-label="Open [^"]+ in the builder" disabled/);
    const off = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} />);
    expect(off).not.toContain("data-greyed");
    expect(off).not.toContain("regime-banner");
  });
});

describe("LibraryTools daily digest tab (U9 TX3)", () => {
  it("shows the settings, the pinned screens and the last messages, with no advice words", () => {
    const html = renderToStaticMarkup(<LibraryTools taxonomy={tax} mirrors={SAMPLE_MIRRORS} watchLists={null} symbolsText="" digest={SAMPLE_DIGEST} initialTab="digest" />);
    const t = text(html);
    expect(t).toContain("Symbols (3)");                                                       // the digest's own list
    expect(t).toContain("Pin up to 5 screens (2 pinned)");
    expect(html).toContain('value="16:00"');
    expect(t).toContain("Momentum: RELIANCE, TCS");
    expect(t).toContain("last: 2026-10-09");
    expect(t).not.toMatch(/\b(buy|sell|recommended|best|top pick|target)\b/i);
  });

  it("shows only the digest tab when the TX2 tools are off", () => {
    const html = renderToStaticMarkup(<LibraryTools taxonomy={tax} mirrors={null} watchLists={null} symbolsText="" digest={SAMPLE_DIGEST} tx2={false} />);
    expect(text(html)).toContain("Daily digest");
    expect(text(html)).not.toContain("Confluence");
    expect(html).toContain('data-testid="digest-tab"');
  });
});
