/**
 * SCR-FIX PR C (owner order ATP_ORDER_SCR_FIX): C1 the evidence on the Charts page's own chart (bars, timeframe, the
 * screen's thresholds as level lines, boxes / bands, the matched bar marked, "Open in Charts"); C2 the matched bar's
 * time and O / H / L / C beside it; C4 "Send to my Telegram" in place (the connect row when not linked, the reason's
 * own action, hidden without a bot, the send after linking exactly once); C5 the grid on a phone and its load error.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MatchedBarCard, OpenInCharts } from "./EvidenceChart";
import { EvidenceView, TelegramSend } from "./EvidencePanel";
import { ChartGridView } from "./ResultViews";
import sample from "./evidenceSample.json";
import {
  SendAfterLink, chartTimeframe, evidenceChartLayers, matchedBar, packCandles, sendRefusal, telegramAction, visibleDrawables,
  type EvidencePack,
} from "./evidence";

const pack = sample as unknown as EvidencePack;
const none = new Set<string>();
const noop = () => undefined;

describe("C1: the evidence on the Charts page's chart", () => {
  it("renders the shared chart with the pack's own bars, the timeframe and an Open in Charts link", () => {
    const html = renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={none} showFailed={false} onSend={noop} />);
    expect(html).toContain('data-testid="evidence-chart-live"');
    expect(html).toContain('data-timeframe="day"');
    expect(html).toContain('data-chart-theme=');                                         // ChartV2's root (the Charts page's chart)
    expect(html).toMatch(/data-testid="evidence-timeframe">Daily</);
    expect(html).toContain('href="/charts/BRK?tf=day"');
    expect(html).toContain("Open in Charts");
    expect(packCandles(pack)).toHaveLength(pack.bars.t.length);
    expect(packCandles(pack)[pack.matched_index]).toMatchObject({ timestamp: pack.bars.t[pack.matched_index], close: 103.8, volume: 3000 });
  });

  it("maps the screen's timeframes to the Charts page's and keeps the exchange in the link", () => {
    expect(["1m", "3m", "15m", "1h", "1d", "1w", "1M"].map(chartTimeframe)).toEqual(["1min", "3min", "15min", "60min", "day", "week", "month"]);
    expect(chartTimeframe("2h")).toBeNull();
    expect(renderToStaticMarkup(<OpenInCharts symbol="ABC" exchange="BSE" timeframe="15min" />)).toContain('href="/charts/ABC?tf=15min&amp;exchange=BSE"');
  });

  it("draws only the pack's drawables: thresholds as level lines, a box as its edges, a band, series as study lines, the matched bar marked", () => {
    const shown = visibleDrawables(pack, none, false);
    const l = evidenceChartLayers(pack, shown);
    expect(l.levels.map((x) => x.price)).toContain(102);                                 // the breakout box's level
    expect(l.layer.segments).toHaveLength(4);                                            // the box: top, bottom, left, right
    expect(l.layer.bands).toHaveLength(1);                                               // the zone band
    expect(l.studies.some((s) => s.line.pane === "price")).toBe(true);                   // the price overlay
    expect(l.studies.some((s) => s.line.pane === "own")).toBe(true);                     // the oscillator in its own pane
    const matched = l.markers.find((m) => m.text === "Matched");
    expect(matched?.timestamp).toBe(pack.bars.t[pack.matched_index]);
    expect(evidenceChartLayers(pack, []).studies).toEqual([]);                           // nothing the screen did not read
    expect(evidenceChartLayers(pack, []).markers).toHaveLength(1);                       // only the matched bar
    const faded = evidenceChartLayers(pack, shown, "d2");
    expect(faded.studies.find((s) => s.id.startsWith("ev-d3"))?.line.color).toMatch(/55$/);   // hovering a block fades the others
  });
});

describe("C2: the matched bar beside the chart", () => {
  it("shows the matched bar's time and its O / H / L / C / V from the pack", () => {
    expect(matchedBar(pack)).toEqual({ time: "2025-04-15T03:45:00+00:00", open: 101.5, high: 104, low: 101.2, close: 103.8, volume: 3000 });
    const html = renderToStaticMarkup(<MatchedBarCard pack={pack} />);
    expect(html).toContain("The matched bar");
    expect(html).toContain("bar of 15 Apr 2025");
    expect(html).toContain('data-field="open">101.50<');
    expect(html).toContain('data-field="high">104.00<');
    expect(html).toContain('data-field="low">101.20<');
    expect(html).toContain('data-field="close">103.80<');
    expect(html).toContain('data-field="volume">3,000<');
    expect(matchedBar({ ...pack, matched_index: 99 })).toBeNull();
  });
});

describe("C4: Send to my Telegram in place", () => {
  const notLinked = { ...pack, telegram_linked: false, telegram_reason: "not_linked" as const, telegram_message: "Connect your Telegram to get this chart there." };

  it("maps the server's state to the action: connect, send, or hidden without a bot / for someone else's evidence / a replay", () => {
    expect(telegramAction(notLinked)).toBe("connect");
    expect(telegramAction({ ...pack, telegram_linked: true, telegram_reason: null })).toBe("send");
    expect(telegramAction({ ...pack, telegram_linked: false, telegram_reason: "bot_not_configured" })).toBe("hidden");
    expect(telegramAction({ ...pack, can_send: false, telegram_reason: "not_owner" })).toBe("hidden");
    expect(telegramAction({ ...pack, replay: { at: "x", source: null, note: "", stored_as_of: pack.as_of } })).toBe("hidden");
  });

  it("shows the connect row in place when not linked, and nothing when the server has no bot", () => {
    const html = renderToStaticMarkup(<TelegramSend pack={notLinked} onSend={noop} onLinked={noop} />);
    expect(html).toContain('data-testid="telegram-connect"');
    expect(html).toContain("Connect your Telegram to get this chart there. It is sent by itself once you are connected.");
    expect(html).not.toContain("Send this chart to my Telegram");
    const noBot = renderToStaticMarkup(<EvidenceView pack={{ ...pack, telegram_linked: false, telegram_reason: "bot_not_configured" }} hiddenBlocks={none} showFailed={false} onSend={noop} />);
    expect(noBot).not.toContain("Telegram");
  });

  it("after a refused send offers the reason's action: try again (blocked / failed) or connect again (not linked)", () => {
    const linked = { ...pack, telegram_linked: true, telegram_reason: null };
    const blocked = renderToStaticMarkup(<TelegramSend pack={linked} onSend={noop}
      send={{ status: "failed", reason: "blocked_by_user", message: "Telegram says the chat with the bot is blocked or closed. Open the chat with the bot, press Start, then try again." }} />);
    expect(blocked).toContain('data-reason="blocked_by_user"');
    expect(blocked).toContain("press Start, then try again.");
    expect(blocked).toContain("Try again");
    const relink = renderToStaticMarkup(<TelegramSend pack={linked} onSend={noop} send={{ status: "failed", reason: "not_linked", message: "Connect your Telegram to get this chart there." }} />);
    expect(relink).toContain('data-testid="telegram-connect"');
  });

  it("reads the server's reason code and sentence from a refused send", () => {
    expect(sendRefusal(JSON.stringify({ detail: { reason: "send_failed", message: "Telegram did not take the image just now. Try again in a minute." } })))
      .toEqual({ reason: "send_failed", message: "Telegram did not take the image just now. Try again in a minute." });
    expect(sendRefusal(JSON.stringify({ detail: "old sentence" }))).toEqual({ reason: null, message: null });
    expect(sendRefusal("<html>")).toEqual({ reason: null, message: null });
  });

  it("sends by itself once linked from the panel - exactly once per evidence", () => {
    const gate = new SendAfterLink();
    const linked = { ...pack, telegram_linked: true };
    expect(gate.take(11, linked)).toBe(false);                                           // not armed: the trader did not link here
    gate.arm();
    expect(gate.take(11, notLinked)).toBe(false);                                        // still not linked: wait for the next read
    expect(gate.take(11, linked)).toBe(true);                                            // linked: send now
    expect(gate.take(11, linked)).toBe(false);                                           // a re-render or reload: never twice
    gate.arm();
    expect(gate.take(11, linked)).toBe(false);                                           // linked again: this evidence was sent
    expect(gate.take(null, linked)).toBe(false);
  });
});

describe("C5: the chart grid on a phone", () => {
  const results = [{ symbol: "DDD", matched: true, reason: null, evidence_id: 7 }, { symbol: "EEE", matched: true, reason: null, evidence_id: 8 }];

  it("is one column on a phone, the shared chart in each card, and a card whose evidence failed says so", () => {
    const grid = renderToStaticMarkup(<ChartGridView results={results} context={[]} packs={{ 8: pack }} failed={{ 7: "this evidence is no longer available." }} />);
    expect(grid).toContain("grid-cols-1");
    expect(grid).toContain("sm:grid-cols-2");
    expect(grid).toContain('data-testid="grid-evidence-error"');
    expect(grid).toContain("The chart for DDD could not be loaded: this evidence is no longer available.");
    expect(grid).toContain('data-testid="evidence-chart-live"');
    expect(grid).not.toContain("text-t10");
  });
});
