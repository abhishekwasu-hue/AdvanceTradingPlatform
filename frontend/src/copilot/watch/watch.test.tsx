import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../../chartAnalyze/signalLanguage";
import { notificationEventWords } from "../../ui/notificationWords";
import sectionSource from "../../settings/SettingsSectionV2.tsx?raw";
import pageSource from "../../pages/NotificationsPage.tsx?raw";
import { CopilotWatchForm } from "./CopilotWatchV2";
import { EMPTY_PREFS, WATCH_CHOICES, canTurnOn, normalise, parseNumber, type WatchSettings } from "./model";

/** M72 PR-7 (CP-7): the Copilot watch choices (Settings › Alerts) and the "Ask why" on its cards. */

const SETTINGS: WatchSettings = { prefs: EMPTY_PREFS, version: 0, telegram: { connected: false, reaches_you: false }, risk_free_rate_set: false };
const html = (s: WatchSettings) => renderToStaticMarkup(<MemoryRouter><CopilotWatchForm settings={s} busy={false} onSave={() => undefined} /></MemoryRouter>);

describe("M72 PR-7 Copilot watch", () => {
  it("every type is off and every number Not set; a type cannot be on without his number", () => {
    const out = html(SETTINGS);
    expect((out.match(/data-watch-kind=/g) ?? []).length).toBe(5);
    expect(out).not.toMatch(/checked=""/);
    expect((out.match(/placeholder="Not set"/g) ?? []).length).toBe(5);
    expect((out.match(/value="[^"]+"/g) ?? []).length).toBe(0);              // no pre-chosen number (M68)
    expect((out.match(/Set your number first/g) ?? []).length).toBe(4);       // the watchlist type has no number
    for (const c of WATCH_CHOICES.filter((c) => c.numbers.length)) expect(canTurnOn(EMPTY_PREFS, c)).toBe(false);
    expect(normalise({ ...EMPTY_PREFS, near_levels: true })).toEqual(EMPTY_PREFS);
    expect(normalise({ ...EMPTY_PREFS, greeks_jump: true, iv_jump_points: 2 }).greeks_jump).toBe(true);
  });

  it("his number is read as typed; out of range or not a number stays Not set", () => {
    expect(parseNumber(" 12.5 ", 100)).toBe(12.5);
    expect(parseNumber("", 100)).toBeNull();
    expect(parseNumber("0", 100)).toBeNull();
    expect(parseNumber("150", 100)).toBeNull();
    expect(parseNumber("abc", 100)).toBeNull();
  });

  it("delta waits for his own interest rate; Telegram waits for his own chat", () => {
    const out = html(SETTINGS);
    expect(out).toContain("Needs your interest rate");
    expect(out).toContain("Connect your own Telegram above first.");
    const ready = html({ ...SETTINGS, risk_free_rate_set: true, telegram: { connected: true, reaches_you: true },
                         prefs: { ...EMPTY_PREFS, near_levels: true, near_levels_room_left_pct: 20 } });
    expect(ready).not.toContain("Needs your interest rate");
    expect(ready).toContain('value="20"');
  });

  it("no trade-call words in any label; the card sits in Settings › Alerts and the feed shows Ask why", () => {
    const words = WATCH_CHOICES.flatMap((c) => [c.label, c.line, ...c.numbers.map((n) => n.label)]);
    for (const w of words) expect(hasCallWords(w), w).toBe(false);
    expect(sectionSource).toContain('useFeatures().on("copilot_watch")');
    expect(sectionSource).toContain("<CopilotWatchV2");
    expect(pageSource).toContain('label="Ask why"');
    expect(notificationEventWords("COPILOT_WATCH")).toBe("Copilot watch");
  });
});
