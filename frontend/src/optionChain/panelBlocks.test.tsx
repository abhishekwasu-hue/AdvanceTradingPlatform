import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { leg, liveChain } from "./chain.fixture";
import { BLOCK_KEY, Hideable, HideableBlock, LegTradeButton, readShown, writeShown } from "./panelBlocks";
import OiDiffTableV2, { NetDiffTableBody } from "./OiDiffTableV2";
import { netDiffRows } from "./oiDiff";
import ChainTableV2 from "./ChainTableV2";
import { DEFAULT_COLUMNS } from "./model";
import { TradeOiBannerView, bannerLines, bannerSummary } from "./TradeOiBannerV2";
import SummaryStripV2 from "./SummaryStripV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import tableSource from "./ChainTableV2.tsx?raw";
import type { ChainRow, NetDiffRow } from "./types";

// The unit tests run without a DOM: a small in-memory localStorage.
const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); } });
});

describe("owner 14 Nov: remembered show / hide blocks of the Terminal's chain panel", () => {
  it("reads the fallback until a choice is stored, then the choice", () => {
    expect(readShown("x", true)).toBe(true);
    writeShown("x", false);
    expect(store.get(BLOCK_KEY + "x")).toBe("0");
    expect(readShown("x", true)).toBe(false);
  });

  it("a bar over the block: shown by default, hidden once he hid it; `Hideable` off renders the block as it is", () => {
    const block = () => renderToStaticMarkup(<HideableBlock id="oi-diff" label="OI difference by strike"><p data-inner>table</p></HideableBlock>);
    expect(block()).toContain("data-inner");
    expect(block()).toMatch(/aria-expanded="true"[^>]*data-hideable-toggle="oi-diff"/);
    writeShown("oi-diff", false);
    expect(block()).not.toContain("data-inner");
    expect(block()).toContain(">Show<");
    expect(renderToStaticMarkup(<Hideable on={false} id="a" label="A"><p>x</p></Hideable>)).toBe("<p>x</p>");
  });
});

describe("owner 14 Nov: a visible B/S button beside every strike opens the existing action sheet", () => {
  it("the button names the contract and calls the chain's onPickLeg (no order path of its own)", () => {
    const pick = vi.fn();
    const b = LegTradeButton({ underlying: "NIFTY", strike: 25250, side: "put", onPick: pick }) as ReactElement<{
      "aria-label": string; className: string; onClick: (e: { stopPropagation: () => void }) => void }>;
    expect(b.props["aria-label"]).toBe("Buy or sell NIFTY 25,250 PE");
    expect(renderToStaticMarkup(b)).toMatch(/>B\/S<\/button>$/);
    expect(b.props.className).toMatch(/h-11 min-w-11/);   // 44 px on a phone
    const stop = vi.fn();
    b.props.onClick({ stopPropagation: stop });
    expect(pick).toHaveBeenCalledTimes(1);
    expect(stop).toHaveBeenCalled();                      // the chain row's own tap (its details) does not fire
  });

  it("the OI-difference table: CE (left) and PE (right) buttons in the sticky Strike cell, the hint above; none without trading", () => {
    const rows: NetDiffRow[] = [24450, 24500].map((strike) => ({ strike, ce_chg: 1, ce_oi: 1, ce_ltp: 1, pe_ltp: 1, pe_oi: 1, pe_chg: 1, diff: 0, dominance: "Neutral" }));
    const nd = { status: "ready" as const, since: "2026-11-11T03:50:00+00:00", step: 50, atm: 24500, window: 6, pcr: 1, rows };
    const chainRows: ChainRow[] = rows.map((r) => ({ strike: r.strike, call: leg({}), put: leg({}) }));
    const html = renderToStaticMarkup(<NetDiffTableBody rows={netDiffRows(rows)} atm={24500} chainRows={chainRows} onPickLeg={() => {}} underlying="NIFTY" />);
    const th = html.match(/data-oi-diff-row="24500"[\s\S]*?<th scope="row"[^>]*sticky[^>]*>([\s\S]*?)<\/th>/)![1];
    expect([...th.matchAll(/data-leg-trade="([^"]+)"/g)].map((x) => x[1])).toEqual(["24500|CE", "24500|PE"]);
    const shown = renderToStaticMarkup(<OiDiffTableV2 live={liveChain({ net_diff: nd })} centreKey="k" hideable="oi-diff" />);
    expect(shown).toContain("data-oi-diff-scroll");
    writeShown("oi-diff", false);
    const hidden = renderToStaticMarkup(<OiDiffTableV2 live={liveChain({ net_diff: nd })} centreKey="k" hideable="oi-diff" />);
    expect(hidden).toContain("OI difference by strike");
    expect(hidden).not.toContain("data-oi-diff-scroll");
    expect(th).toMatch(/Buy or sell NIFTY 24,500 CE[\s\S]*24,500[\s\S]*Buy or sell NIFTY 24,500 PE/);
    expect(renderToStaticMarkup(<NetDiffTableBody rows={netDiffRows(rows)} atm={24500} chainRows={chainRows} />)).not.toContain("data-leg-trade");
  });

  it("the chain table: the same buttons; a tap opens the leg's actions, not the row's details", () => {
    const live = liveChain();
    const html = renderToStaticMarkup(<ChainTableV2 live={live} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={() => {}} onPickLeg={() => {}} />);
    expect(html).toContain("data-trade-hint");
    for (const r of live.rows) expect(html).toContain(`data-leg-trade="${r.strike}|CE"`);
    expect(tableSource).toContain('onPick={() => onPickLeg(row, "call")}');
    expect(tableSource).toContain('onPick={() => onPickLeg(row, "put")}');
    expect(renderToStaticMarkup(<ChainTableV2 live={live} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={() => {}} />)).not.toContain("data-leg-trade");
  });
});

describe("owner 14 Nov: the compact header", () => {
  it("the banner collapses to one line of Trade's own signal and PCR; shown, its content is as before", () => {
    const live = liveChain();
    const state = { banner: null, history: null, loaded: true };
    const onShown = vi.fn();
    const closed = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live} state={state} interval={5} onInterval={() => {}} collapse={{ shown: false, onShown }} />);
    const l = bannerLines("NIFTY", null, live, "2026-11-14");
    expect(closed).toContain("data-banner-summary");
    expect(closed).not.toContain("data-banner-message");
    expect(bannerSummary(l)).toBe(`${l.message} · ${l.pcr}`);
    expect(bannerSummary({ ...l, signal: "BEARISH (Weakening)" })).toBe(`Signal: BEARISH (Weakening) · ${l.pcr}`);
    const open = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live} state={state} interval={5} onInterval={() => {}} collapse={{ shown: true, onShown }} />);
    const plain = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live} state={state} interval={5} onInterval={() => {}} />);
    // the shown banner = the plain banner plus the toggle bar
    expect(open.replace(/<button[^>]*data-banner-toggle[\s\S]*?<\/button>/, "")).toBe(plain);
  });

  it("the compact strip shows spot, ATM, PCR and ATM IV; the rest behind a remembered More", () => {
    const live = liveChain();
    const keys = () => [...renderToStaticMarkup(<SummaryStripV2 summary={live.summary} expiry={live.expiry} loading={false} dense asOf={live.fetched_at} />)
      .matchAll(/data-summary="([^"]+)"/g)].map((x) => x[1]);
    expect(keys()).toEqual(["spot", "atm", "pcr", "atm-iv"]);
    writeShown("summary-more", true);
    expect(keys()).toEqual(expect.arrayContaining(["futures", "max-pain", "oi-walls", "as-of"]));
  });

  it("the page wires the panel: compact notes, banner collapsible, OI difference and analyses hideable (embedded only)", () => {
    expect(pageSource).toContain("collapsible={embedded}");
    expect(pageSource).toContain('hideable={embedded ? "oi-diff" : undefined}');
    expect(pageSource).toContain('<Hideable on={embedded} id="analysis"');
    expect(pageSource).toContain("data-chain-notes data-compact");
  });
});
