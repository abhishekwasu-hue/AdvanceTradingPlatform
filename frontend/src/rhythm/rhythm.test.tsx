import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import dashboardSource from "../dashboard/DashboardV2.tsx?raw";
import { searchSettings } from "../settings/model";
import sectionSource from "../settings/SettingsSectionV2.tsx?raw";
import { RhythmBriefV2, RhythmSectionV2 } from "./DailyRhythmCardV2";
import { CLOSING, EMPTY_WEEK, PRE_MARKET, linesOnly } from "./fixtures";
import { DEFAULT_PREFS, KIND_ORDER, TAB_LABELS, TELEGRAM_CHOICES, cleanBrief } from "./model";
import { telegramNote } from "./RhythmTelegramV2";
import type { RhythmBrief } from "./types";

/** MO4: the daily rhythm card (Dashboard) and the Telegram choice (Settings → Alerts). */

const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const text = (out: string) => out.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, "\"").replace(/&#x27;/g, "'");

describe("MO4 the brief card", () => {
  it("shows every section's lines, its page and the note (an older server: lines only)", () => {
    const out = html(<RhythmBriefV2 brief={linesOnly(PRE_MARKET)} phase="pre_market" kind="pre_market" onKind={() => undefined} />);
    for (const s of PRE_MARKET.sections) {
      expect(out).toContain(`data-rhythm-section="${s.id}"`);
      for (const line of s.lines) expect(text(out)).toContain(line);
      if (s.link) expect(out).toContain(`href="${s.link.to}"`);
    }
    expect(out).toContain(PRE_MARKET.subtitle);
    expect(text(out)).toContain("it does not tell you what to trade");
  });

  it("four tabs, the current digest selected and the moment's one marked", () => {
    const out = html(<RhythmBriefV2 brief={CLOSING} phase="closing" kind="closing" onKind={() => undefined} />);
    expect((out.match(/role="tab"/g) ?? []).length).toBe(4);
    expect(out).toMatch(/aria-selected="true"[^>]*data-rhythm-tab="closing"/);
    expect((out.match(/aria-label="now"/g) ?? []).length).toBe(1);
    expect(KIND_ORDER.map((k) => TAB_LABELS[k])).toEqual(["Morning", "Pulse", "Wrap", "Week"]);
  });

  it("an empty section says so in plain words - never a sample figure", () => {
    const out = html(<RhythmBriefV2 brief={EMPTY_WEEK} phase="weekly" kind="weekly" onKind={() => undefined} />);
    expect(text(out)).toContain("The week's closing data is not in yet.");
    expect(text(out)).toContain("A quiet week is fine when nothing met your plan.");
    expect(out).not.toMatch(/₹|\d+%/);
  });

  it("structure words only: no buy / sell / target / stop wording in any fixture", () => {
    for (const brief of [PRE_MARKET, CLOSING, EMPTY_WEEK]) {
      const out = text(html(<RhythmBriefV2 brief={brief} phase={brief.kind} kind={brief.kind} onKind={() => undefined} />));
      expect(hasCallWords(out)).toBe(false);
    }
  });

  it("a setup line carries no price or level", () => {
    for (const line of PRE_MARKET.sections.find((s) => s.id === "setups")!.lines) {
      const symbol = line.split(":")[0];
      expect(line.replace(symbol, "")).not.toMatch(/\d/);
    }
  });

  it("a line that reads as a call is left out on render", () => {
    const bad: RhythmBrief = { ...PRE_MARKET, sections: [{ id: "setups", title: "Setups from your lists", empty: "None.", link: null,
      lines: ["ABC: buy above the high", "ABC: a buyer-side setup from your strategy."] }] };
    expect(cleanBrief(bad).sections[0].lines).toEqual(["ABC: a buyer-side setup from your strategy."]);
    const out = text(html(<RhythmSectionV2 section={cleanBrief(bad).sections[0]} />));
    expect(out).not.toContain("buy above");
  });

  it("never rewards trade count: the closing wrap praises staying inside one's own limits", () => {
    const day = CLOSING.sections.find((s) => s.id === "day")!.lines.join(" ");
    expect(day).toContain("inside your own daily limits");
    expect(day).toContain("not the number of trades");
    expect(day.toLowerCase()).not.toMatch(/streak|trade more|great job|keep trading/);
  });
});

describe("MO4 wiring", () => {
  it("the Dashboard shows the card only with the daily_rhythm flag (the v2 Dashboard is ui_v2 already)", () => {
    expect(dashboardSource).toContain('{features.on("daily_rhythm") && <DailyRhythmCardV2 />}');
  });

  it("Settings → Alerts holds the Telegram choice behind the flag, re-read after Telegram connects", () => {
    expect(sectionSource).toContain('const rhythm = useFeatures().on("daily_rhythm");');
    expect(sectionSource).toContain("<RhythmTelegramV2 key={telegramChanged} />");
    expect(sectionSource).toContain('id="daily-briefs"');
  });

  it("the Settings search finds it only while the flag is on", () => {
    const on = searchSettings("morning brief", { on: (f: string) => f === "daily_rhythm", role: "OWNER" });
    expect(on.map((h) => h.to)).toContain("/settings/alerts#daily-briefs");
    const off = searchSettings("morning brief", { on: () => false, role: "OWNER" });
    expect(off.map((h) => h.to)).not.toContain("/settings/alerts#daily-briefs");
  });
});

describe("MO4 Telegram choice", () => {
  it("off by default, the pulse optional", () => {
    expect(DEFAULT_PREFS).toEqual({ telegram: false, pre_market: true, mid_session: false, closing: true, weekly: true });
    expect(TELEGRAM_CHOICES.map((c) => c.key)).toEqual(["pre_market", "mid_session", "closing", "weekly"]);
  });

  it("one plain next step until Telegram reaches the user's own chat", () => {
    const base = { prefs: DEFAULT_PREFS, version: 0 };
    expect(telegramNote({ ...base, telegram: { connected: false, reaches_you: false } })).toContain("Connect Telegram above first");
    expect(telegramNote({ ...base, telegram: { connected: true, reaches_you: false } })).toContain("another member's chat");
    expect(telegramNote({ ...base, telegram: { connected: true, reaches_you: true } })).toBeNull();
  });
});
