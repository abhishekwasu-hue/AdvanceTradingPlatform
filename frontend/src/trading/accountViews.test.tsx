import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AccountFilterV2, accountLabels, inAccount } from "./accountViews";
import { heldRows } from "./model";
import { PositionRowV2, type RowActions } from "./PositionsV2";
import positionsSource from "./PositionsV2.tsx?raw";
import viewsSource from "./accountViews.tsx?raw";
import { exposure, trade } from "./trading.fixture";

/** M45 MB-7: the simulated PAPER account of each open position (badge) and "All accounts / <label>" (filter). */

const noop = () => undefined;

function row(p = trade(), actions: Partial<RowActions> = {}) {
  const [r] = heldRows([p], exposure());
  const a: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, ...actions };
  return renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} /></ul></MemoryRouter>);
}

describe("M45 MB-7 account views", () => {
  it("labels: distinct, sorted, only from rows that carry one", () => {
    const list = [trade({ id: 1, account_label: "Family" }), trade({ id: 2, account_label: null }), trade({ id: 3, account_label: "Alpha" }),
      trade({ id: 4, account_label: "Family" }), trade({ id: 5 })];
    expect(accountLabels(list)).toEqual(["Alpha", "Family"]);
    expect(accountLabels([trade({ id: 9 })])).toEqual([]);
    expect(accountLabels(null)).toEqual([]);
    expect(inAccount(list[0], null)).toBe(true);
    expect(inAccount(list[0], "Family")).toBe(true);
    expect(inAccount(list[1], "Family")).toBe(false);
  });

  it("filter: hidden with no labelled row; All accounts plus each label, 44px", () => {
    expect(renderToStaticMarkup(<AccountFilterV2 labels={[]} value={null} onChange={noop} />)).toBe("");
    const html = renderToStaticMarkup(<AccountFilterV2 labels={["Alpha", "Family"]} value="Family" onChange={noop} />);
    expect(html).toContain('data-testid="account-filter"');
    expect(html).toMatch(/<option value="">All accounts<\/option><option value="Alpha">Alpha<\/option><option value="Family" selected="">Family<\/option>/);
    expect(html).toContain("h-11");
    expect(viewsSource).not.toMatch(/#[0-9a-f]{3,6}\b|text-\[|md:h-9/i);
  });

  it("badge: only with the flag and a label; the row is otherwise exactly as before", () => {
    const labelled = trade({ account_label: "Family" });
    expect(row(labelled, { accounts: true })).toContain('data-testid="account-badge"');
    expect(row(labelled, { accounts: true })).toContain(">Family<");
    expect(row(labelled)).not.toContain("account-badge");
    expect(row(trade(), { accounts: true })).not.toContain("account-badge");
    expect(row(labelled)).toBe(row(trade()));
  });

  it("the page reads the flag and filters the listed rows, keeping every exit path", () => {
    expect(positionsSource).toContain('const accountsOn = features.on("multi_account");');
    expect(positionsSource).toContain("const labels = accountsOn ? accountLabels(list) : [];");
    expect(positionsSource.match(/<PositionList rows=\{shownRows\}/g)).toHaveLength(2);
    expect(positionsSource.match(/\{accountFilter\}/g)).toHaveLength(2);
  });
});
