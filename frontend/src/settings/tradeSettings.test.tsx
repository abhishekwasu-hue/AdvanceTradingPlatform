import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { navItemFor, navEntryFor } from "../shell/navModel";
import { SETTINGS_ROWS } from "./model";
import { SETTINGS_PATH, settingsSectionAt } from "./paths";
import { SaveAsDefault } from "./SaveAsDefault";
import {
  NOT_SET, changesOf, draftOf, eventActionOf, parseField, partsOf, saveAsDefault, saveTradeSettings, setCount, valueWords,
  type TradeField, type TradeSection, type TradeSettingsView,
} from "./tradeSettings";
import TradeSettingsV2 from "./TradeSettingsV2";

const field = (key: string, o: Partial<TradeField> = {}): TradeField => ({
  key, label: key.split(".")[1], kind: "whole", help: "What it does.", unit: "lots", choices: [], low: 1, high: 1e9, low_open: false,
  term: null, fact: false, wired: true, scope: "user", value: null, saved: false, ...o,
});

const PRODUCT = field("order_defaults.product", { label: "Product", kind: "choice", unit: "", term: "mis",
  choices: [{ value: "MIS", label: "MIS (intraday)" }, { value: "NRML", label: "NRML (carry forward)" }] });
const LOTS = field("position_size.lots", { label: "Lots", term: "lot" });
const ROLL = field("options.expiry_day_roll", { label: "On an expiry day, open the next expiry", kind: "switch", unit: "" });
const SQUARE = field("exits.square_off_time", { label: "Square-off time", kind: "time", unit: "", wired: false });
const FACT = field("order_defaults.freeze_quantity", { label: "Freeze quantity", kind: "fact", unit: "", fact: true });
const CEILING = field("risk_limits.ceiling_daily_loss_pct", { label: "Team ceiling: daily loss", kind: "number", unit: "%", scope: "organisation", wired: false });

const section = (id: string, fields: TradeField[], today: TradeSection["today"] = null): TradeSection => ({ id, title: id, fields, today });

const VIEW = (admin: boolean): TradeSettingsView => ({
  admin, page: "/settings/trade-settings",
  sections: [
    section("order_defaults", [PRODUCT, FACT]),
    section("position_size", [LOTS]),
    section("exits", admin ? [SQUARE] : [], { label: "My trading defaults", path: "/settings/trading-defaults" }),
    section("options", [ROLL]),
    section("risk_limits", admin ? [CEILING] : [], { label: "Risk limits", path: "/settings/risk-limits" }),
    section("automation", []),
    section("paper_account", []),
  ],
});

const html = (view: TradeSettingsView) =>
  renderToStaticMarkup(<MemoryRouter><TradeSettingsV2 initial={view} load={() => new Promise(() => undefined)} /></MemoryRouter>);

describe("M68 PR-0: Settings › Trade settings", () => {
  it("lives at /settings/trade-settings, in the Settings index, search and nav (lights up Settings)", () => {
    expect(SETTINGS_PATH["trade-settings"]).toBe("/settings/trade-settings");
    expect(settingsSectionAt("/settings/trade-settings")?.old).toBe("/settings");
    expect(SETTINGS_ROWS.find((r) => r.id === "trade")?.to).toBe("/settings/trade-settings");
    expect(navItemFor("settings-trade-settings")?.path).toBe("/settings/trade-settings");
    expect(navEntryFor("settings-trade-settings")).toBe("settings");
  });

  it("renders every field as Not set until saved, no value pre-filled", () => {
    const out = html(VIEW(false));
    expect(out).toContain("Every choice starts as Not set");
    expect(out.match(/data-value="true">Not set</g)?.length).toBe(3);                 // product, lots, expiry-day roll
    expect(out).not.toContain("Saved:");
    expect(out).toMatch(/data-input="position_size.lots"[^>]*value=""|value=""[^>]*data-input="position_size.lots"/);
    expect(out).toContain('data-combobox=""');                                     // a choice starts on Not set
  });

  it("hides fields not wired yet from a trader; a platform admin sees them under Advanced", () => {
    const user = html(VIEW(false));
    expect(user).not.toContain("exits.square_off_time");
    expect(user).not.toContain("Advanced (admin)");
    expect(user).toContain('href="/settings/trading-defaults"');                   // a section with nothing here yet says where
    expect(user).toContain("Nothing to choose here yet.");
    expect(user).toContain('data-fact="order_defaults.freeze_quantity"');          // a fact is information, not a control
    expect(user).not.toContain('data-input="order_defaults.freeze_quantity"');
    const admin = html(VIEW(true));
    expect(admin).toContain("Advanced (admin): not wired yet · 1");
    expect(admin).toContain('data-input="exits.square_off_time"');
    expect(admin).toContain("Set by your admin for the team");                     // a team ceiling is not saved here
    expect(admin).not.toContain('data-input="risk_limits.ceiling_daily_loss_pct"');
  });

  it("parts and counts: facts apart, unwired only for an admin", () => {
    const s = section("exits", [SQUARE, FACT, LOTS]);
    expect(partsOf(s, false)).toEqual({ main: [LOTS], facts: [FACT], advanced: [] });
    expect(partsOf(s, true).advanced).toEqual([SQUARE]);
    expect(setCount(s, false)).toBe("0 of 1 set");
    expect(setCount(section("x", [{ ...LOTS, saved: true, value: 2 }, SQUARE]), true)).toBe("1 of 2 set");
    expect(setCount(section("x", [FACT]), false)).toBeNull();
  });

  it("saving one section sends only the fields changed there; untouched fields are never sent", () => {
    const s = section("order_defaults", [PRODUCT, { ...LOTS, key: "order_defaults.x", saved: true, value: 3 }, ROLL]);
    const draft = { ...draftOf(s), "order_defaults.product": "NRML" };
    expect(changesOf(s, draft, s.fields.map((f) => f.key))).toEqual({ values: { "order_defaults.product": "NRML" }, problem: null });
    // clearing a saved field sends null (back to Not set); clearing a never-saved one sends nothing
    expect(changesOf(s, { ...draftOf(s), "order_defaults.x": "" }, s.fields.map((f) => f.key)).values).toEqual({ "order_defaults.x": null });
    expect(changesOf(s, { ...draftOf(s), "options.expiry_day_roll": "off" }, s.fields.map((f) => f.key)).values)
      .toEqual({ "options.expiry_day_roll": false });
    expect(changesOf(s, { ...draftOf(s), "order_defaults.x": "1.5" }, s.fields.map((f) => f.key)))
      .toEqual({ values: {}, problem: "Lots: enter a whole number." });
    // a field outside the keys shown (another part) is never sent
    expect(changesOf(s, draft, ["options.expiry_day_roll"]).values).toEqual({});
  });

  it("parses each kind in plain words", () => {
    expect(parseField(LOTS, "0").problem).toBe("Lots: enter at least 1.");
    expect(parseField(LOTS, "2").value).toBe(2);
    expect(parseField(SQUARE, "3pm").problem).toBe("Square-off time: enter a time like 09:30.");
    expect(parseField(SQUARE, "15:10").value).toBe("15:10");
    expect(parseField(PRODUCT, "CNC").problem).toBe("Product: choose one of the options.");
    expect(parseField({ ...LOTS, kind: "number", low: 0, low_open: true, unit: "%" }, "0").problem).toBe("Lots: enter more than 0.");
    expect(valueWords(LOTS)).toBe(NOT_SET);
    expect(valueWords({ ...PRODUCT, value: "NRML" })).toBe("NRML (carry forward)");
    expect(valueWords({ ...ROLL, value: false })).toBe("Off");
    expect(valueWords({ ...LOTS, value: 2 })).toBe("2 lots");
    expect(valueWords({ ...LOTS, unit: "₹", kind: "number", value: 500000 })).toBe("₹5,00,000");
  });

  it("the save call sends exactly the values given", async () => {
    const fetcher = vi.fn().mockResolvedValue(VIEW(false));
    await saveTradeSettings({ "position_size.lots": 2 }, fetcher);
    expect(fetcher).toHaveBeenCalledWith("/me/trade-settings", { method: "PUT", body: JSON.stringify({ values: { "position_size.lots": 2 } }) });
  });
});

describe("M68 TS-3: Save as my default", () => {
  it("writes one field, never an empty one", async () => {
    const fetcher = vi.fn().mockResolvedValue(VIEW(false));
    expect(await saveAsDefault("order_defaults.product", "NRML", fetcher)).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ values: { "order_defaults.product": "NRML" } });
    for (const empty of [null, undefined, "", "  ", Number.NaN]) expect(await saveAsDefault("position_size.lots", empty, fetcher)).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("renders an unticked tick, disabled while the field is empty", () => {
    const on = renderToStaticMarkup(<SaveAsDefault fieldKey="order_defaults.product" value="MIS" />);
    expect(on).toContain("Save as my default");
    expect(on).toContain('data-save-default="order_defaults.product"');
    expect(on).not.toContain("checked");
    expect(on).not.toContain("disabled");
    expect(renderToStaticMarkup(<SaveAsDefault fieldKey="position_size.lots" value="" />)).toContain("disabled");
  });
});

describe("M68 PR-5 Risk limits for the forms", () => {
  const field = (key: string, value: unknown, saved: boolean, scope: "user" | "organisation" = "user"): TradeField => ({
    key, label: key, kind: scope === "organisation" ? "number" : "choice", help: "", unit: scope === "organisation" ? "%" : "",
    choices: [{ value: "BLOCK", label: "Pause new entries" }, { value: "SIZE_CUT", label: "Cut the size" }],
    low: 0, high: 100, low_open: true, term: null, fact: false, wired: true, scope, value, saved });
  const view = (fields: TradeField[]): TradeSettingsView => ({ admin: false, page: "/settings/trade-settings",
    sections: [{ id: "risk_limits", title: "Risk limits", today: null, fields }] });

  it("a market event form starts from his own saved action only, else asks (TS1-058)", () => {
    expect(eventActionOf(view([field("risk_limits.event_default_action", "BLOCK", true)]))).toBe("BLOCK");
    expect(eventActionOf(view([field("risk_limits.event_default_action", null, false)]))).toBeUndefined();
    expect(eventActionOf(null)).toBeUndefined();
  });

  it("a team ceiling shows read-only as his admin's value, or no ceiling (TS1-048)", () => {
    const html = (f: TradeField) => renderToStaticMarkup(<MemoryRouter><TradeSettingsV2 initial={view([f])} /></MemoryRouter>);
    expect(html(field("risk_limits.ceiling_risk_per_trade_pct", 2, true, "organisation"))).toContain("Your admin: 2");
    expect(html(field("risk_limits.ceiling_risk_per_trade_pct", null, false, "organisation"))).toContain("No ceiling");
  });
});
