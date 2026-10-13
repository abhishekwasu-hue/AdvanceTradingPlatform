import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RiskCheckPanel from "../proposals/RiskCheckPanel";
import { portfolioT } from "./i18n";
import { PORTFOLIO_RISK_EN } from "./locales/en";
import { bucketLabel, isRiskView, limitLines, overLimit, type PortfolioRiskView as View } from "./model";
import { PortfolioRiskView } from "./PortfolioRiskCard";
import sample from "./riskSample.json";

/** N1 F3: the portfolio risk card. `riskSample.json` is the server's own payload (backend
 * tests/test_n1_f3_portfolio_cards.py fails when its keys drift from GET /api/portfolio/risk). */
const view = sample as unknown as View;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|must|recommend\w*|advice|advise|buy|sell|reduce|guarantee\w*|best)\b/i;
const SOURCES = import.meta.glob<string>(["./*.ts", "./*.tsx", "!./*.test.tsx"], { query: "?raw", import: "default", eager: true });

function lookup(path: string): unknown {
  return path.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined), PORTFOLIO_RISK_EN);
}

function strings(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") return Object.values(node).flatMap(strings);
  return [];
}

describe("portfolio risk i18n (namespace portfolioRisk)", () => {
  it("has an English string for every key the code asks for", () => {
    const missing: string[] = [];
    for (const [file, src] of Object.entries(SOURCES)) {
      for (const m of src.matchAll(/\bt\(\s*"([A-Za-z0-9_.]+)"/g)) if (typeof lookup(m[1]) !== "string") missing.push(`${file}: ${m[1]}`);
    }
    expect(missing).toEqual([]);
  });

  it("is English only and never advice", () => {
    const all = strings(PORTFOLIO_RISK_EN);
    expect(all.filter((s) => /[ऀ-ॿ]/.test(s))).toEqual([]);
    expect(all.filter((s) => ADVICE.test(s.replace("Not a recommendation", "")))).toEqual([]);
  });
});

describe("the model", () => {
  it("names buckets and limits", () => {
    expect(isRiskView(view)).toBe(true);
    expect(isRiskView({ detail: "x" })).toBe(false);
    expect(bucketLabel("INDEX", portfolioT)).toBe("All indices (one bucket)");
    expect(bucketLabel("UNCLASSIFIED:NEWCO", portfolioT)).toBe("NEWCO (no sector on record)");
    expect(limitLines(null, portfolioT)).toEqual([]);
    expect(limitLines({ max_sector_pct: 0, max_correlated_risk_pct: 0, max_open_positions: 0 }, portfolioT)).toEqual(["No limits set (0 = no limit)."]);
    expect(limitLines(view.limits, portfolioT)).toHaveLength(3);
    const it_ = view.buckets[0];
    expect(overLimit(it_, view.limits)).toEqual({ sector: false, risk: false });
    expect(overLimit({ ...it_, gross_pct: 35 }, view.limits).sector).toBe(true);
    expect(overLimit({ ...it_, risk_pct: 2.5 }, null)).toEqual({ sector: false, risk: false });
  });
});

describe("the card (server sample)", () => {
  it("shows exposure, open risk by bucket, beta and the user's limits", () => {
    const html = renderToStaticMarkup(<PortfolioRiskView view={view} />);
    const out = text(html);
    expect(out).toContain("4,12,500");
    expect(out).toContain("41.25% of capital");
    expect(out).toContain("All indices (one bucket)");
    expect(out).toContain("NEWCO (no sector on record)");
    expect(out).toContain("Beta unknown (counted as 1): NEWCO");
    expect(out).toContain("One bucket's open risk: 2% of capital");
    expect(out).toContain("Not a recommendation");
    expect(html).not.toContain('data-testid="one-sided"');
    expect(ADVICE.test(out.replace("Not a recommendation", ""))).toBe(false);
  });

  it("shows the one-sided warning and an empty book", () => {
    const warned = { ...view, direction: { ...view.direction, warning: "All 3 directional positions are bullish: one large move against them hits them together (correlated risk, no diversification)." } };
    expect(renderToStaticMarkup(<PortfolioRiskView view={warned} />)).toContain('data-testid="one-sided"');
    expect(text(renderToStaticMarkup(<PortfolioRiskView view={{ ...view, positions: 0, buckets: [], limits: null }} />))).toContain("No open PAPER positions.");
  });

  it("puts the portfolio impact line on the proposal card", () => {
    const line = "Portfolio after this entry (at most): INDEX exposure 12% of capital, open risk in INDEX 0.6%, all open risk 1.5%, open positions 5.";
    const html = renderToStaticMarkup(<RiskCheckPanel a={{ risk_check: null, strike_breach: null, portfolio_impact: {
      line, bucket: "INDEX", bucket_gross_pct: 12, bucket_risk_pct: 0.6, total_risk_pct: 1.5, positions: 5, entry_risk: 1000,
      limits: { max_sector_pct: 0, max_correlated_risk_pct: 0, max_open_positions: 0 } } }} />);
    expect(html).toContain('data-testid="portfolio-impact"');
    expect(text(html)).toContain(line);
    expect(renderToStaticMarkup(<RiskCheckPanel a={{ risk_check: null, strike_breach: null, portfolio_impact: null }} />)).toBe("");
  });
});
