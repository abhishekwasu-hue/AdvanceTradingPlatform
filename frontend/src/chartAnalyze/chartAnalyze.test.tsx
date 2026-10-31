import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { CANNOT_ALERT, alertCondition, bandCondition, draftErrors, ruleBody, ruleTimeframe } from "./alerts";
import { errorCode, isFeatureOff } from "./api";
import { firstImage } from "./ChartAnalyzePageV2";
import { DISCLAIMER, IMAGE_ONLY_LABEL, fixtureResult } from "./fixtures";
import ResultViewV2, { FALLBACK_DISCLAIMER, price, statusText } from "./ResultViewV2";
import { legendFor, shapesFor } from "./shapes";
import type { SetupStatus } from "./types";

const noop = () => undefined;
const render = (status: SetupStatus, onAlert = noop) =>
  renderToStaticMarkup(<ResultViewV2 result={fixtureResult(status)} imageSrc="blob:fixture" onAlert={onAlert} onExport={noop} onDelete={noop} />);

describe("Analyze a chart: the result screen renders every block", () => {
  for (const status of ["formed", "forming", "none"] as const) {
    it(`a ${status} setup: blocks 1-8, the label and the disclaimer`, () => {
      const html = render(status);
      for (const block of ["ca-annotated", "ca-story", "ca-setup", "ca-evidence", "ca-scenarios", "ca-htf", "ca-levels"]) {
        expect(html, block).toContain(`data-block="${block}"`);
      }
      expect(html.includes('data-block="ca-wait"')).toBe(status !== "formed");       // only when the setup is not formed
      expect(html).toContain(IMAGE_ONLY_LABEL);
      expect(html).toContain(DISCLAIMER);
      expect(html).toContain(`data-setup="${status}"`);
      expect(html).toContain("Listed, not scored.");
      expect(html).toContain("≈ 120.5");                                               // image-only prices are marked approximate
      expect(html).toContain("Set an alert on this area");
      expect(html).toContain("Download / share the result image");
      expect(html).toContain("Delete");
      expect(html).not.toContain("Open in Chart tab");                                 // hybrid only (PR-B)
    });
  }

  it("never shows an order button or a buy / sell call", () => {
    for (const status of ["formed", "forming", "none"] as const) {
      const buttons = [...render(status).matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, ""));
      for (const label of buttons) expect(label).not.toMatch(/\b(buy|sell|order|trade|place)\b/i);
    }
  });

  it("the setup status reads formed / forming - waiting for X / no setup", () => {
    expect(statusText(fixtureResult("formed"))).toBe("Formed");
    expect(statusText(fixtureResult("forming"))).toBe("Forming — waiting for a close back above the made-up area");
    expect(statusText(fixtureResult("none"))).toBe("No setup");
  });

  it("'What to wait for' offers an alert only for a price condition", () => {
    expect(render("forming")).toContain("Alert me when this happens");
    const none = render("none");
    expect(none).not.toContain("Alert me when this happens");
    expect(none).toContain(CANNOT_ALERT.replace("'", "&#x27;"));
  });

  it("the evidence checklist marks present and absent items", () => {
    const html = render("forming");
    expect(html).toContain('data-present="true"');
    expect(html).toContain('data-present="false"');
    expect(html).toContain('aria-label="absent"');
  });

  it("the label and the disclaimer always show: the item's own when the result lacks them, else a fixed line", () => {
    const bare = { ...fixtureResult("formed"), label: null, disclaimer: "" };
    const withItem = renderToStaticMarkup(<ResultViewV2 result={bare} itemLabel={IMAGE_ONLY_LABEL} itemDisclaimer={DISCLAIMER} imageSrc={null}
      onAlert={noop} onExport={noop} onDelete={noop} />);
    expect(withItem).toContain(IMAGE_ONLY_LABEL);
    expect(withItem).toContain(DISCLAIMER);
    const alone = renderToStaticMarkup(<ResultViewV2 result={bare} imageSrc={null} onAlert={noop} onExport={noop} onDelete={noop} />);
    expect(alone).toContain(FALLBACK_DISCLAIMER);
  });

  it("says so when the stored image is gone instead of drawing nothing", () => {
    const html = renderToStaticMarkup(<ResultViewV2 result={fixtureResult("formed")} imageSrc={null} imageNote="The chart image is no longer stored."
      onAlert={noop} onExport={noop} onDelete={noop} />);
    expect(html).toContain("The chart image is no longer stored.");
  });

  it("prices: approximate with ≈, exact without", () => {
    expect(price(24812.35, true)).toBe("≈ 24,812.35");
    expect(price(24812.35, false)).toBe("24,812.35");
    expect(price(null, true)).toBe("");
  });
});

describe("Analyze a chart: the overlay", () => {
  it("draws areas, lines with touches, setup lines, swings and marks in the image's pixel space", () => {
    const r = fixtureResult("formed");
    const shapes = shapesFor(r.annotations, r.image.width);
    expect(shapes.filter((s) => s.kind === "rect")).toHaveLength(2);
    expect(shapes.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text)).toEqual(["HL", "HH", "LH"]);
    const setup = shapes.filter((s) => s.kind === "line" && s.dashed);
    expect(setup.map((s) => (s as { label?: string }).label)).toEqual(["Trigger", "Invalidation", "Next area"]);
    for (const s of setup) if (s.kind === "line") expect([s.x0, s.x1]).toEqual([0, r.image.width]);
    const buyer = shapes.find((s) => s.kind === "rect" && s.token === "up");
    expect(buyer).toMatchObject({ x: 100, y: 240, w: 500, h: 30, label: "Buyer area · tested" });
    expect(shapes.some((s) => s.kind === "line" && s.token === "warn")).toBe(true);    // the RSI divergence
  });

  it("the legend lists only what is drawn", () => {
    expect(legendFor(fixtureResult("none").annotations).map((r) => r.text)).not.toContain("Trigger");
    expect(legendFor(fixtureResult("formed").annotations).map((r) => r.text)).toContain("RSI divergence");
  });

  it("the overlay SVG uses the token colours only", () => {
    const html = render("formed");
    expect(html).toMatch(/rgb\(var\(--up\) \/ 0\.16\)/);
    expect(html).not.toMatch(/#[0-9a-f]{6}/i);
  });
});

describe("Analyze a chart: alerts", () => {
  it("builds ScreenQL price conditions (pinned by the backend's ScreenQL test)", () => {
    expect(alertCondition({ kind: "above", price: 120.5 })).toBe("close > 120.5");
    expect(alertCondition({ kind: "below", price: 101 })).toBe("close < 101");
    expect(alertCondition({ kind: "between", low: 101, high: 98 })).toBe("close >= 98 and close <= 101");
    expect(alertCondition({ kind: "above" })).toBeNull();
    expect(alertCondition(null)).toBeNull();
    expect(bandCondition(Number.NaN, 5)).toBeNull();
  });

  it("maps the picture's timeframe to a rule timeframe", () => {
    expect(ruleTimeframe("15m")).toBe("15m");
    expect(ruleTimeframe("15 min")).toBe("15m");
    expect(ruleTimeframe("1H")).toBe("1h");
    expect(ruleTimeframe("D")).toBe("1d");
    expect(ruleTimeframe("7m")).toBe("15m");
    expect(ruleTimeframe(null)).toBe("15m");
  });

  it("the rule is an ordinary instrument alert, never an order", () => {
    const body = ruleBody({ symbol: " demo ", condition: "close > 1", timeframe: "15m", name: "Alert" });
    expect(body).toMatchObject({ kind: "instrument", symbol: "DEMO", condition: "close > 1", base_tf: "15m" });
    expect(Object.keys(body)).not.toContain("order");
    expect(draftErrors({ symbol: "", condition: "", timeframe: "15m", name: "" })).toHaveLength(2);
  });
});

describe("Analyze a chart: API errors and inputs", () => {
  it("reads the machine code and the feature-off answer", () => {
    const unreadable = new ApiError(422, "Please upload a screenshot instead", "", JSON.stringify({ detail: { message: "Please upload a screenshot instead", code: "link_unreadable", reason: "x" } }));
    expect(errorCode(unreadable)).toBe("link_unreadable");
    expect(errorCode(new Error("x"))).toBe("");
    expect(isFeatureOff(new ApiError(503, "off", "", JSON.stringify({ detail: "The 'chart_analyze' feature is currently disabled by the platform operator" })))).toBe(true);
    expect(isFeatureOff(new ApiError(503, "engine", "", JSON.stringify({ detail: { code: "engine_not_installed" } })))).toBe(false);
  });

  it("takes the first PNG or JPEG from a paste or a drop", () => {
    const gif = { type: "image/gif" } as File;
    const png = { type: "image/png" } as File;
    expect(firstImage([gif, png])).toBe(png);
    expect(firstImage([gif])).toBeNull();
    expect(firstImage(null)).toBeNull();
  });
});

describe("Analyze a chart: the page behind its flag", () => {
  it("is hidden while the flag is off (A1: it first asks the status once, so nothing of the page shows meanwhile)", async () => {
    vi.resetModules();
    vi.doMock("../ui/useFeatures", () => ({ useFeatures: () => ({ loaded: true, on: () => false }) }));
    const { default: Page } = await import("./ChartAnalyzePageV2");
    const html = renderToStaticMarkup(<MemoryRouter><Page /></MemoryRouter>);
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain("Analyze a chart");
    expect(html).not.toContain("Coming soon");
    vi.doUnmock("../ui/useFeatures");
  });

  it("with the flag on: upload, link, the two modes (AI only / Advanced analysis) and Analyze; no order button", async () => {
    vi.resetModules();
    vi.doMock("../ui/useFeatures", () => ({ useFeatures: () => ({ loaded: true, on: (f: string) => f === "chart_analyze" }) }));
    vi.doMock("../ui/PageIntro", () => ({ PageIntro: () => null }));
    const { default: Page } = await import("./ChartAnalyzePageV2");
    const html = renderToStaticMarkup(<MemoryRouter><Page /></MemoryRouter>);
    expect(html).toContain("Choose an image");
    expect(html).toContain("Or paste a chart link");
    expect(html).toContain("AI only");
    expect(html).toContain("Advanced analysis (code + AI)");
    expect(html).not.toContain("Coming soon");                                       // M59 CR-4: hybrid is built (behind its flag)
    expect(html).toMatch(/<button[^>]*>Analyze<\/button>/);
    expect(html).not.toMatch(/\b(Buy|Sell|Place order)\b/);
    vi.doUnmock("../ui/useFeatures");
    vi.doUnmock("../ui/PageIntro");
  });
});

describe("Analyze a chart: the engine is not installed (A1)", () => {
  const missing = { installed: false, message: "Analysis engine not installed" };
  const loadUnavailable = async () => (await import("./ChartAnalyzePageV2")).EngineUnavailable;

  it("a user sees one plain line, no upload or link input, no reason and never 'Coming soon'", async () => {
    const EngineUnavailable = await loadUnavailable();
    const html = renderToStaticMarkup(<EngineUnavailable engine={missing} />);
    expect(html).toContain("Chart analysis is not available right now");
    expect(html).not.toMatch(/Coming soon|not open for your account|Choose an image|paste a chart link|type="file"|<input/i);
    expect(html).not.toContain("For admins");
    expect(html).not.toMatch(/private package|ATP_PRIVATE_REF|deploy key/i);
  });

  it("the admin also sees why and the owner's install steps", async () => {
    const EngineUnavailable = await loadUnavailable();
    const html = renderToStaticMarkup(<EngineUnavailable engine={{ ...missing, reason: "the private package is not installed on this server",
      owner_steps: "The owner adds the host's read-only deploy key and ATP_PRIVATE_REF, then runs the deploy." }} />);
    expect(html).toContain("Chart analysis is not available right now");
    expect(html).toContain("For admins: the analysis service is missing");
    expect(html).toContain("Why: the private package is not installed on this server.");
    expect(html).toContain("ATP_PRIVATE_REF");
    expect(html).not.toMatch(/<input/);
  });

  it("the page maps installed=false to that state, not to the generic Coming soon", async () => {
    const fs = await import("node:fs");
    const src = fs.readFileSync(new URL("./ChartAnalyzePageV2.tsx", import.meta.url), "utf8");
    const unavailable = src.indexOf("if (engineMissing && !off) return <EngineUnavailable engine={status.engine} />;");
    expect(unavailable).toBeGreaterThan(0);
    expect(src.indexOf("<ComingSoon")).toBeGreaterThan(unavailable);      // "Coming soon" only for a flag that is off
    expect(src).toContain("if (!on) return <ComingSoon flag={FLAG} />;");
  });
});
