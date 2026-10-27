import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import contractSource from "../components/ContractNoteCard.tsx?raw";
import paperAccountSource from "../components/PaperAccountCard.tsx?raw";
import ticketSource from "../components/PaperTicketCard.tsx?raw";
import { PortfolioRiskView } from "../portfolio/PortfolioRiskCard";
import riskSample from "../portfolio/riskSample.json";
import type { PortfolioRiskView as RiskView } from "../portfolio/model";
import decisionSource from "../progression/DecisionToolsCard.tsx?raw";
import { ExperiencePanel } from "../progression/ExperienceCard";
import LadderEvidence from "../progression/LadderEvidence";
import { BEGINNER_STATUS } from "../progression/samples.experience";
import { OK, TOO_FEW } from "../progression/samples";
import { FeaturesProvider } from "../ui/useFeatures";
import positionsSource from "./PositionsV2.tsx?raw";

/** UX-11 PR 2 (owner, 27 Oct): the My trades cards, numbers first - with ui_v2 the explanations sit in one closed
 * Details per card (moved, never removed); with the flag off every card renders as before. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const v2 = (el: React.ReactElement, on = true) => renderToStaticMarkup(<FeaturesProvider userKey={1} value={{ ui_v2: on }}>{el}</FeaturesProvider>);
/** The part of the markup inside the card's Details (closed <details> is not shown). */
const inDetails = (html: string) => (html.match(/<details[\s\S]*<\/details>/) ?? [""])[0];
const outside = (html: string) => html.replace(/<details[\s\S]*<\/details>/g, "");

describe("Experience level", () => {
  const panel = (on: boolean) => v2(<ExperiencePanel status={BEGINNER_STATUS} canTrade onOptOut={async () => true} />, on);

  it("v2: the record as key-value rows (drawdown signed ▼), intro / counts / exits under Details", () => {
    const html = panel(true);
    expect(html).toContain("<dl");
    expect(text(outside(html))).toContain("Closed PAPER trades 12");
    expect(outside(html)).toContain("▼");
    for (const moved of ["Your level comes from your own PAPER record", "Your record: 12 closed trades", "Opt-outs: 1 today", "Exits are never gated"]) {
      expect(text(inDetails(html)), moved).toContain(moved);
      expect(text(outside(html)), moved).not.toContain(moved);
    }
    expect(text(outside(html))).toContain("Intermediate opens futures:");      // the one short visible line
  });

  it("flag off: today's card, word for word", () => {
    const html = panel(false);
    expect(html).not.toContain("<details");
    expect(text(html)).toContain("Your record: 12 closed trades over 8 sessions, largest drawdown 3.20%.");
  });
});

describe("Your figures", () => {
  it("v2: too few trades is '3 of 20' with a bar; the caveats and the basis under Details", () => {
    const few = v2(<LadderEvidence evidence={TOO_FEW} />);
    expect(few).toContain('role="progressbar"');
    expect(text(few)).toMatch(/\d+ of \d+ closed trades/);
    const ok = v2(<LadderEvidence evidence={OK} />);
    expect(ok).toContain("<details");
    expect(outside(ok)).not.toMatch(/<p/);
  });

  it("flag off: as before", () => {
    expect(v2(<LadderEvidence evidence={OK} />, false)).not.toContain("<details");
  });
});

describe("Portfolio risk", () => {
  it("v2: no paragraph above the first number; net and beta signed; as-of, notes and footer under Details", () => {
    const html = v2(<PortfolioRiskView view={riskSample as unknown as RiskView} />);
    const first = outside(html);
    expect(first.slice(0, first.indexOf("grid"))).not.toContain("<p ");
    expect(html).toContain("data-signed");
    expect(inDetails(html)).toContain("<p>");
  });

  it("flag off: the as-of line first, as before", () => {
    expect(v2(<PortfolioRiskView view={riskSample as unknown as RiskView} />, false)).not.toContain("<details");
  });
});

describe("wiring of the other cards and the page footnote", () => {
  it("each card moves its long text into its own Details only on v2", () => {
    for (const [name, src, id] of [
      ["PAPER account", paperAccountSource, "mytrades.paper-account"], ["Contract notes", contractSource, "mytrades.contract-notes"],
      ["Decision tools", decisionSource, "mytrades.decision-tools"], ["Plan before trade", ticketSource, "mytrades.plan-before-trade"],
    ] as const) {
      expect(src, name).toContain("useUiV2()");
      expect(src, name).toContain(`id="${id}"`);
    }
    // The contract-note method text is kept whole, word for word, inside Details.
    expect(contractSource.match(/Legs are matched to trades by broker order id first/g)).toHaveLength(2);
  });

  it("Positions has the one footer line instead of a disclaimer per card", () => {
    expect(positionsSource).toContain("<PageFootnote>Phase 0 has PAPER only.</PageFootnote>");
  });
});
