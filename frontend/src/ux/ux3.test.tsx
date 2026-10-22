import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import catalogJson from "../../e2e/fixtures/broker_catalog.json";
import { ADVANCED_FIELD_KEYS, ConnectPanelView, splitFields, stepFor, type ConnectPanelViewProps } from "../broker/ConnectPanel";
import { NEEDS_BROKER_FOR_DATA, PlainDataSourceBar, sampleHiddenFor, type CandleSourceState } from "../components/DataSource";
import { signalsToday } from "../dashboard/model";
import { sourceCaption, type SignalFeedItem } from "../signals/feed";
import { SETUP_FALLBACK, hasPrice, isStructureText, reasonWords, setupWords } from "../signals/signalText";
import type { AiAction, BrokerCatalogEntry } from "../types";

/** UX3 (Master Order 3, the remaining audit fixes): the signal-language formatter, the broker panel's Advanced fold and
 * the plain data bar (no sample data for users). */

const CATALOG = catalogJson as BrokerCatalogEntry[];
const entryOf = (broker: string) => CATALOG.find((e) => e.broker === broker)!;
const noop = () => undefined;
const panel = (entry: BrokerCatalogEntry, over: Partial<ConnectPanelViewProps> = {}) => renderToStaticMarkup(
  <ConnectPanelView entry={entry} step={stepFor(entry, "connect")} values={{}} onChange={noop} onSubmit={noop} busy={false} result={null} {...over} />,
);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("UX3 fix 10: signal text in structure words", () => {
  it("knows a price from a small number", () => {
    for (const p of ["Entry 24,790.00", "above 24790", "near 1,35,000.50", "at 99.5"]) expect(hasPrice(p), p).toBe(true);
    for (const n of ["Price above EMA 20", "15m opening range", "RSI divergence on 2 swings"]) expect(hasPrice(n), n).toBe(false);
  });

  it("keeps a structure setup, replaces a call or a price", () => {
    expect(setupWords("Opening-range breakout, retest held")).toBe("Opening-range breakout, retest held");
    expect(setupWords("Buy the dip")).toBe(SETUP_FALLBACK);
    expect(setupWords("Target 24,930 in sight")).toBe(SETUP_FALLBACK);
    expect(setupWords("  ")).toBe(SETUP_FALLBACK);
    expect(isStructureText("The pullback reached a seller area; a handover is forming")).toBe(true);   // "seller" is a structure word
  });

  it("keeps only the reasons in structure words, once each", () => {
    expect(reasonWords(["Higher low held", "Stop below 24,720", "Sell on the retest", "Higher low held", "Range broke on volume"]))
      .toEqual(["Higher low held", "Range broke on volume"]);
  });

  it("the Dashboard's signal rows carry no levels and no call words", () => {
    const now = Date.parse("2026-10-19T05:30:00Z");
    const action = (evidence: Record<string, unknown>) => ({ id: 1, action: "ENTRY", status: "PROPOSED", created_at: "2026-10-19T05:12:00Z", evidence } as unknown as AiAction);
    const [row] = signalsToday([action({ symbol: "NIFTY 50", direction: "LONG", proposal_price: 24790, stop_loss: 24720, target1: 24930,
      strategy_id: "orb_nifty", source: "deployment", signal: { reasons: ["Buy above 24,800"] } })], now).rows;
    expect(row).not.toHaveProperty("levels");
    expect(row.setup).toBe("Orb nifty");                     // the call-word reason is left out; the strategy's name instead
    expect(JSON.stringify(row)).not.toMatch(/24,?[79]\d0|Entry|Stop|Target/);
  });

  it("an internal bot number is never the caption", () => {
    const item = (name: string) => ({ source: { kind: "autopilot", name } } as Pick<SignalFeedItem, "source">);
    expect(sourceCaption(item("Deployment #4"))).toBe("Paper bot");
    expect(sourceCaption(item("Morning ORB"))).toBe("Paper bot · Morning ORB");
  });
});

describe("UX3 fix 6: the broker panel's Advanced fold", () => {
  it("folds the Fyers Redirect URL and the TOTP secret, nothing else", () => {
    expect([...ADVANCED_FIELD_KEYS].sort()).toEqual(["redirect_uri", "totp_secret"]);
    const fyers = splitFields(entryOf("fyers").fields, ADVANCED_FIELD_KEYS);
    expect(fyers.main.map((f) => f.key)).toEqual(["api_key", "api_secret"]);
    expect(fyers.advanced.map((f) => f.key)).toEqual(["redirect_uri"]);
    expect(splitFields(entryOf("angel_one").fields, ADVANCED_FIELD_KEYS).advanced.map((f) => f.key)).toEqual(["totp_secret"]);
    expect(splitFields(entryOf("upstox").fields, ADVANCED_FIELD_KEYS).advanced).toEqual([]);
    // A broker whose only field would be folded keeps it in view.
    expect(splitFields([{ key: "totp_secret", label: "TOTP secret", kind: "secret" }], ADVANCED_FIELD_KEYS).main.map((f) => f.key)).toEqual(["totp_secret"]);
  });

  it("renders the folded field inside a closed Advanced fold, after the main fields", () => {
    const html = panel(entryOf("fyers"), { fold: ADVANCED_FIELD_KEYS });
    const fold = html.match(/<details[^>]*data-advanced-fields[^>]*>[\s\S]*?<\/details>/)?.[0] ?? "";
    expect(fold).toContain('data-field="redirect_uri"');
    expect(fold).not.toMatch(/<details[^>]*\bopen\b/);
    expect(text(fold)).toContain("Advanced (1 more field)");
    expect(html.replace(fold, "")).not.toContain('data-field="redirect_uri"');
    expect(html.indexOf('data-field="api_secret"')).toBeLessThan(html.indexOf("data-advanced-fields"));
  });

  it("says where the missing field is, and Connect stays off until it is filled", () => {
    const some = panel(entryOf("fyers"), { fold: ADVANCED_FIELD_KEYS, values: { api_key: "a", api_secret: "b" } });
    expect(some).toContain("One more field is under Advanced.");
    expect(some).toMatch(/<button type="submit" disabled=""/);
    const all = panel(entryOf("fyers"), { fold: ADVANCED_FIELD_KEYS, values: { api_key: "a", api_secret: "b", redirect_uri: "https://x" } });
    expect(all).not.toContain("One more field is under Advanced.");
    expect(all).not.toMatch(/<button type="submit" disabled=""/);
  });

  it("without the fold (ui_v2 off) the panel is as before", () => {
    const html = panel(entryOf("fyers"));
    expect(html).not.toContain("data-advanced-fields");
    expect(html).toContain('data-field="redirect_uri"');
  });
});

describe("UX3 fix 7: no sample data for users", () => {
  it("only the platform admin keeps sample data, and only with ui_v2", () => {
    expect(sampleHiddenFor(true, "OWNER")).toBe(true);
    expect(sampleHiddenFor(true, "USER")).toBe(true);
    expect(sampleHiddenFor(true, null)).toBe(true);
    expect(sampleHiddenFor(true, "SUPER_ADMIN")).toBe(false);
    expect(sampleHiddenFor(false, "OWNER")).toBe(false);
  });

  const source = (over: Partial<CandleSourceState>): CandleSourceState => ({
    mode: "broker", setMode: noop, broker: "upstox", setBroker: noop, lookbackDays: 5, setLookbackDays: noop, sources: [], usable: false, loading: false,
    label: "broker:upstox", fetch: () => Promise.reject(new Error("unused")), fetchChains: () => Promise.reject(new Error("unused")), sampleHidden: true, ...over,
  });

  it("with no broker: one sentence and the way to the Broker page, no Sample switch", () => {
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({})} /></MemoryRouter>);
    expect(text(html)).toContain(NEEDS_BROKER_FOR_DATA);
    expect(html).toContain('href="/broker"');
    expect(html).not.toMatch(/sample|deterministic|backend|lookback/i);
  });

  it("with a broker: where the prices come from, in plain words", () => {
    const sources = [{ broker: "upstox", account_label: "primary", usable: true }] as CandleSourceState["sources"];
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({ usable: true, sources })} /></MemoryRouter>);
    expect(text(html)).toContain("Prices from your broker");
    expect(text(html)).toContain("History");
    expect(html).not.toContain("<select");                  // one broker: nothing to pick
    expect(html).not.toMatch(/sample|deterministic|backend|lookback|cached/i);
  });
});
