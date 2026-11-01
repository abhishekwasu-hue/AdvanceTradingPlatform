import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { APPROVAL_MODE_KEY, ENTRY_MODE_CHOICES, approvalModeOf, type TradeField, type TradeSettingsView } from "./tradeSettings";
import { entryModeText } from "../paperbot/PaperBotV2";

/** M68 PR-6 (TS1-060 / 061): no bot form pre-selects "New entries"; his saved Trade settings value starts it; "Ask me
 *  first" needs no organisation flag. */

const field = (value: unknown, saved = true): TradeField => ({
  key: APPROVAL_MODE_KEY, label: "New entries", kind: "choice", help: "", unit: "", choices: [], low: null, high: null, low_open: false,
  term: null, fact: false, wired: true, scope: "user", value, saved,
});
const view = (fields: TradeField[]): TradeSettingsView => ({ admin: false, page: "/settings/trade-settings",
  sections: [{ id: "automation", title: "Automation", fields, today: null }] });
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
const read = (path: string) => code(readFileSync(new URL(path, import.meta.url), "utf8"));

describe("M68 PR-6: New entries is his own choice", () => {
  it("only a value he saved counts; Not set stays undefined", () => {
    expect(approvalModeOf(null)).toBeUndefined();
    expect(approvalModeOf(view([field(null, false)]))).toBeUndefined();
    expect(approvalModeOf(view([field("APPROVAL")]))).toBe("APPROVAL");
    expect(approvalModeOf(view([field("AUTO")]))).toBe("AUTO");
    expect(approvalModeOf(view([field("SOMETHING")]))).toBeUndefined();
    expect(ENTRY_MODE_CHOICES.map((c) => c.value)).toEqual(["AUTO", "APPROVAL"]);
  });

  it("the bot forms start with nothing chosen and ask; no flag gates Ask me first", () => {
    const full = read("../pages/DeploymentsPage.tsx");
    expect(full).toContain('useState<"" | EntryModeChoice>("")');
    expect(full).toContain('<option value="">Not set - choose</option>');
    expect(full).not.toContain('features.on("approval_only_entries")');
    const quick = read("../paperbot/PaperBotV2.tsx");
    expect(quick).toContain('useState<"" | EntryModeChoice>("")');
    expect(quick).toContain("fieldKey={APPROVAL_MODE_KEY}");
    expect(quick).not.toContain('approvalAllowed={features.on("approval_only_entries")}');
  });

  it("the bot card says which mode is in effect and whether it follows Trade settings", () => {
    expect(entryModeText({ entry_mode_in_effect: "APPROVAL", entry_mode_follows_settings: true })).toBe("Asks you first (from your Trade settings)");
    expect(entryModeText({ entry_mode_in_effect: "AUTO", entry_mode_follows_settings: false })).toBe("Places entries by itself (this bot's own choice)");
    expect(entryModeText({ entry_mode_in_effect: null, entry_mode_follows_settings: true }))
      .toBe("New entries not set - places nothing (from your Trade settings)");
    expect(entryModeText({})).toBeNull();
  });

  it("the full form stores a mode only when picked for this bot; empty = follow my Trade settings", () => {
    const full = read("../pages/DeploymentsPage.tsx");
    expect(full).toContain("...(entryMode ? { entry_mode: entryMode } : {})");
    expect(full).toContain("Follow my Trade settings");
  });
});
