import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import cardSource from "./PaperAccountCard.tsx?raw";
import { LadderStages, NumberTile, PaperBalanceTile } from "./PaperAccountCard";
import { LIVE_STEP_OFF, LIVE_STEP_ON, TILE, TILE_LABEL, TILE_ROW, TILE_VALUE } from "./paperLadder";
import { Signed } from "../ui/Signed";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

// Owner, 12 Nov (phone screenshot, My trades > Paper account and tools): "9,89,107.38" broke as "9,89,107.3 / 8" and the
// net P&L ran past the card edge. A figure stays on ONE line at 360 / 390; the tiles wrap to the next row instead.
describe("PAPER account tiles: a number never breaks inside (360 / 390)", () => {
  it("the value is one tabular line in a token size, never wrapped anywhere", () => {
    for (const cls of ["whitespace-nowrap", "font-tabular", "text-t20", "sm:text-t24"]) expect(TILE_VALUE).toContain(cls);
    const html = renderToStaticMarkup(<NumberTile label="Equity (open marks)" value="₹9,89,107.38" />);
    expect(html).not.toContain("overflow-wrap");
    expect(html).not.toContain("break-all");
    expect(html).toContain("whitespace-nowrap");
  });

  it("a tile is at least as wide as its number; the label does not widen it; the row wraps", () => {
    expect(TILE).toContain("min-w-max");
    expect(TILE).toContain("flex-1");
    expect(TILE_LABEL).toContain("w-0 min-w-full");
    expect(TILE_ROW).toContain("flex-wrap");
    expect(cardSource).toContain("<div className={TILE_ROW}");
    expect(cardSource).not.toContain("grid grid-cols-2 gap-3 sm:grid-cols-4");   // the fixed 2-column grid that broke them
  });

  it("labels are sentence case (no uppercase class) and short", () => {
    expect(TILE_LABEL).not.toContain("uppercase");
    for (const label of ['"Balance"', '"Equity (open marks)"', '"Net P&L (after charges)"', '"Closed / open"']) expect(cardSource).toContain(`label=${label}`);
    expect(cardSource).not.toContain("Equity (with open marks)");
  });

  it("money is the shared ₹ formatter with Indian grouping and paise", () => {
    expect(text(renderToStaticMarkup(<PaperBalanceTile balance={989107.38} v2 />))).toContain("₹9,89,107.38");
    expect(text(renderToStaticMarkup(<Signed value={-10892.6} paise />))).toContain("−₹10,892.60");
    expect(cardSource).toContain("formatINR(account.equity, { paise: true })");
    expect(cardSource).toContain("<Signed value={r.net} paise />");
  });

  it("colour by meaning: a loss red, a gain green (tokens)", () => {
    expect(renderToStaticMarkup(<Signed value={-10892.6} paise />)).toContain("text-down");
    expect(renderToStaticMarkup(<Signed value={972.5} paise />)).toContain("text-up");
    expect(renderToStaticMarkup(<NumberTile label="Net P&L (after charges)" value="−₹5.00" tone="down" />)).toContain("text-down");
    const below = renderToStaticMarkup(<PaperBalanceTile balance={-12450.5} v2 />);
    expect(below).toContain("text-down");
    expect(below).toContain("below zero - practice only");
  });
});

// LIVE-1 (owner, 11 Nov - BINDING): `live_trading` is the ONE switch; the ladder never shows a grant or approval stage.
describe("Progression ladder LIVE step follows the one live switch", () => {
  const stages = [
    { key: "PAPER", label: "PAPER", status: "current" as const },
    { key: "LIVE", label: "LIVE", status: "switch" as const },
  ];

  it("live_trading OFF: switched off by the admin", () => {
    const t = text(renderToStaticMarkup(<LadderStages stages={stages} liveOn={false} answered />));
    expect(t).toContain(LIVE_STEP_OFF);
    expect(LIVE_STEP_OFF).toBe("Live trading is switched off by the admin");
    expect(t).toContain("PAPER · current");
  });

  it("live_trading ON: available, choose LIVE on any order", () => {
    const html = renderToStaticMarkup(<LadderStages stages={stages} liveOn answered />);
    expect(text(html)).toContain(LIVE_STEP_ON);
    expect(LIVE_STEP_ON).toBe("Live trading available - choose LIVE on any order");
    expect(html).toContain("text-up");
  });

  it("an older server stage never shows its approval-only / not-yet-enabled words", () => {
    const old = [stages[0], { key: "LIVE_APPROVAL", label: "LIVE (approval-only)", status: "locked" as const, note: "not yet enabled" }];
    for (const on of [false, true]) {
      const t = text(renderToStaticMarkup(<LadderStages stages={old} liveOn={on} answered />));
      expect(t).not.toContain("approval-only");
      expect(t).not.toContain("not yet enabled");
    }
  });

  it("the LIVE step waits for the switch's answer (no wrong words while it loads)", () => {
    const t = text(renderToStaticMarkup(<LadderStages stages={stages} liveOn={false} answered={false} />));
    expect(t).not.toContain(LIVE_STEP_OFF);
    expect(t).toContain("PAPER · current");
  });

  it("the card reads the one switch (the features answer), not the ladder's criteria", () => {
    expect(cardSource).toContain("liveAvailableFrom(features)");
  });
});
