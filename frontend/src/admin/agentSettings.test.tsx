import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import { changedValues, fieldErrorsOf, PositionsList, SettingsForm, type AgentField, type AgentSettings } from "./AgentSettingsCard";

/** SRV2 P2 + P4: the owner's bot settings form (only what he changed is sent) and the read-only positions list. */
const fields: AgentField[] = [
  { key: "on", label: "Entries on", group: "entry", type: "bool", value: true, saved: false },
  { key: "depth", label: "Depth", group: "entry", type: "int", unit: "strikes", strikes: true, value: 2, saved: true },
  { key: "count", label: "Exits per day", group: "exit", type: "int", value: 1, saved: false },
];
const settings: AgentSettings = { installed: true, symbol: "NIFTY", symbols: ["NIFTY", "BANKNIFTY"], signal_symbols: ["NIFTY"], fields };

describe("SRV2 P2: the bot settings form", () => {
  it("sends only the fields he changed, numbers as numbers; a blank number is not sent", () => {
    expect(changedValues(fields, {})).toEqual({});
    expect(changedValues(fields, { depth: "3", on: true })).toEqual({ depth: 3 });
    expect(changedValues(fields, { depth: "", count: "x" })).toEqual({});
    expect(changedValues(fields, { on: false, count: "2" })).toEqual({ on: false, count: 2 });
  });

  it("shows the bot's own labels in Entry / Exit tabs and a disabled Save until something changes", () => {
    const html = renderToStaticMarkup(<SettingsForm settings={settings} />);
    expect(html).toContain("Entries on");
    expect(html).toContain("Depth<span class=\"text-fg-muted\"> (strikes)</span>");
    expect(html).toContain(">Entry<");
    expect(html).toContain(">Exit<");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*Save NIFTY/s);
  });

  it("reads the server's per-field sentences from a refused save", () => {
    const e = new ApiError(422, "Unprocessable", "", JSON.stringify({ detail: { errors: { count: "Must be 1 or more." } } }));
    expect(fieldErrorsOf(e)).toEqual({ count: "Must be 1 or more." });
    expect(fieldErrorsOf(new ApiError(500, "x", "", "not json"))).toBeNull();
    const html = renderToStaticMarkup(<SettingsForm settings={settings} errors={{ depth: "Too deep." }} />);
    expect(html).toContain("Too deep.");
  });

  it("an older package without the field list says so instead of an empty form", () => {
    expect(renderToStaticMarkup(<SettingsForm settings={{ ...settings, fields: [] }} />)).toContain("not installed on this server yet");
  });
});

describe("SRV2 P4: the bot's positions, read-only", () => {
  it("shows legs, P&L coloured by sign and open / closed with the reason; no buttons", () => {
    const html = renderToStaticMarkup(<PositionsList positions={[
      { id: 1, symbol: "NIFTY", direction: "bullish", status: "OPEN", opened_at: "2026-11-21T09:45:00+05:30", closed_at: null, pnl: 1250, exit_reason: null, mode: "PAPER",
        legs: [{ strike: 24000, option_type: "PE", side: "SELL", qty: 75, entry_price: 80, exit_price: null },
          { strike: 23900, option_type: "PE", side: "BUY", qty: 75, entry_price: 50, exit_price: null }] },
      { id: 2, symbol: "BANKNIFTY", direction: "bearish", status: "CLOSED", opened_at: "2026-11-20T10:00:00+05:30", closed_at: "2026-11-20T11:00:00+05:30", pnl: -800,
        exit_reason: "SL", mode: "PAPER", legs: [] },
    ]} />);
    expect(html).toContain("−24000PE +23900PE");
    expect(html).toMatch(/text-up[^>]*>\+₹1,250/);
    expect(html).toMatch(/text-down[^>]*>−₹800/);
    expect(html).toContain("Closed · SL");
    expect(html).not.toContain("<button");
    expect(renderToStaticMarkup(<PositionsList positions={[]} />)).toContain("No positions yet.");
  });
});
