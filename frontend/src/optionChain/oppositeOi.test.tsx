import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { alertStateWords, oppositeWords } from "../oi/format";
import type { OiBannerRow, OiHistoryResponse, OiSettingsResponse } from "../oi/types";
import { liveChain } from "./chain.fixture";
import { OppositeOiBellView, REFERENCE, bellBody, bellOn, settingOf } from "./OppositeOiAlertV2";
import source from "./OppositeOiAlertV2.tsx?raw";
import { TradeOiBannerView } from "./TradeOiBannerV2";

/** Owner order 13 Nov: the "Alert me on opposite OI moves" bell (per symbol, OFF by default), its settings with references
 *  to edit before use, the alert words, and the History row mark. */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;

const settings = (own: Record<string, unknown> = {}, effective: Record<string, unknown> = {}): OiSettingsResponse => ({
  underlying: "NIFTY", enabled: true, exchange: "NSE", updated_at: null,
  overrides: { atm_range: 6, ...(Object.keys(own).length ? { alerts: own } : {}) },
  effective: { alerts: { enabled: false, types: ["DIRECTION_CHANGE", "PCR_BAND"], opposite_window_minutes: 5, opposite_min_change_pct: 2,
    opposite_relative_k: 2, opposite_relative_windows: 6, ...effective } },
});

describe("opposite OI move alert", () => {
  it("is off until he turns it on for this symbol; an organisation default never turns it on", () => {
    expect(bellOn(null)).toBe(false);
    expect(bellOn(settings())).toBe(false);
    expect(bellOn(settings({}, { types: ["OPPOSITE_OI"] }))).toBe(false);           // only the effective (default) list
    expect(bellOn(settings({ types: ["OPPOSITE_OI"] }))).toBe(true);
  });

  it("turning it on keeps his other ticks and overrides and asks the readings to follow the symbol", () => {
    const on = bellBody(settings(), true);
    expect(on).toEqual({ enabled: true, overrides: { atm_range: 6, alerts: { types: ["DIRECTION_CHANGE", "PCR_BAND", "OPPOSITE_OI"] } } });
    const off = bellBody(settings({ types: ["PCR_BAND", "OPPOSITE_OI"], cooldown_minutes: 30 }), false);
    expect(off).toEqual({ overrides: { atm_range: 6, alerts: { types: ["PCR_BAND"], cooldown_minutes: 30 } } });
    expect(bellBody(settings({ types: ["OPPOSITE_OI"] }), true, { opposite_window_minutes: 15 }).overrides.alerts)
      .toEqual({ types: ["OPPOSITE_OI"], opposite_window_minutes: 15 });
  });

  it("settings take only numbers inside their range", () => {
    expect(settingOf("2.5", 0, 100)).toBe(2.5);
    expect(settingOf("", 0, 100)).toBeNull();
    expect(settingOf("abc", 0, 100)).toBeNull();
    expect(settingOf("101", 0, 100)).toBeNull();
    expect(settingOf("6.5", 1, 50, true)).toBeNull();
    expect(REFERENCE).toEqual({ window: 5, pct: 2, k: 2, m: 6 });
  });

  it("the bell: off shows only the button; on shows the window and the numbers, each a reference to edit", () => {
    const off = renderToStaticMarkup(<OppositeOiBellView symbol="NIFTY" settings={settings()} onToggle={noop} onSave={noop} />);
    expect(text(off)).toContain("Alert me on opposite OI moves");
    expect(off).toContain('aria-pressed="false"');
    expect(off).not.toContain("data-opposite-oi-settings");
    const on = renderToStaticMarkup(<OppositeOiBellView symbol="NIFTY" settings={settings({ types: ["OPPOSITE_OI"] })} onToggle={noop} onSave={noop} />);
    const t = text(on);
    expect(on).toContain('aria-pressed="true"');
    expect(t).toContain("Opposite OI move alert: on");
    expect(t).toContain("Both rising or both falling: no alert.");
    expect(t.match(/edit before use/g)?.length).toBe(3);
    expect(on).toMatch(/aria-checked="true" data-opposite-oi-window="5"/);
    expect(t).not.toMatch(/\bbuy\b|\bsell\b|target|stop.?loss|bullish|bearish/i);
    expect(source).not.toMatch(/[\u0900-\u097f]|\p{Extended_Pictographic}/u);
  });

  it("alert-log words carry the two changes and the window", () => {
    expect(oppositeWords("CE +1240000 PE -810000 5m")).toBe("Call OI +12.40 L, Put OI −8.10 L in 5 min");
    expect(alertStateWords("OPPOSITE_OI", "CE -500000 PE +250000 15m")).toBe("Call OI −5.00 L, Put OI +2.50 L in 15 min");
    expect(oppositeWords("odd")).toBe("odd");
  });

  it("a History row whose window closed with an opposite move says so under its Signal", () => {
    const row = (over: Partial<OiBannerRow & { opposite?: { call_change: number; put_change: number; window: number } }> = {}) => ({
      slot: "2026-11-11T10:25:00+05:30", data_as_of: "2026-11-11T04:56:00+00:00", direction: "BULLISH", message: "", fact_message: "",
      put_class: "Writing", call_class: "Flat/unclear", signal: "BULLISH (Strong)", diff: 1_250_000, delta_diff: -40_000, total_call_oi: 9_000_000,
      total_put_oi: 10_250_000, pcr: 1.14, pcr_band: "MILD_BULLISH", pcr_label: "", underlying_price: 24510, atm_strike: 24500, strike_step: 50,
      max_pain: 24500, expiry: "2026-11-13", dte: 2, first_of_day: false, ...over }) as OiBannerRow;
    const history: OiHistoryResponse = { underlying: "NIFTY", date: "2026-11-11", interval: 5, market_open: true, stale: false, age_minutes: 1,
      stale_after_minutes: 15, rows: [{ ...row(), opposite: { call_change: 1_240_000, put_change: -810_000, window: 5 } },
        row({ slot: "2026-11-11T10:20:00+05:30" })] };
    const html = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={{ ...liveChain(), expiry: "2026-11-13" }}
      state={{ banner: null, history, loaded: true }} interval={5} onInterval={noop} now={Date.parse("2026-11-11T05:00:00Z")} bell={<i data-bell />} />);
    expect(html.match(/data-history-opposite/g)?.length).toBe(1);
    expect(text(html)).toContain("Opposite OI move: Call +12.40 L · Put −8.10 L");
    expect(html).toContain("data-bell");
  });
});
