import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import pageSource from "./PaperBotV2.tsx?raw";
import { BotWhyV2, WhyList, whyWhen } from "./BotWhyV2";

/** AUD-T3 (owner audit 28 Oct): "Why no trade?" on a bot - its last closed bars with the reason, newest first. */
describe("AUD-T3 why no trade", () => {
  const rows = [
    { bar_time: "2026-09-25T05:01:00Z", seen_at: "2026-09-25T05:02:30Z", code: "entered", label: "A trade was placed", detail: null },
    { bar_time: "2026-09-25T05:00:00Z", seen_at: "2026-09-25T05:01:30Z", code: "no_signal", label: "No setup on this bar", detail: "price has not reached the demand area" },
    { bar_time: null, seen_at: "2026-09-25T03:30:00Z", code: "outside_hours", label: "The market was closed", detail: null },
  ];

  it("closed by default, loads only when opened", () => {
    let calls = 0;
    const html = renderToStaticMarkup(<BotWhyV2 botId={7} load={async () => { calls += 1; return { rows }; }} />);
    expect(html).toContain("Why no trade? Last bars");
    expect(html).not.toContain(" open=\"\"");
    expect(calls).toBe(0);
  });

  it("each bar with its reason chip and the detail under it; a trade is the positive chip", () => {
    const html = renderToStaticMarkup(<WhyList rows={rows} />);
    expect(html.indexOf('data-why-code="entered"')).toBeLessThan(html.indexOf('data-why-code="no_signal"'));
    expect(html).toContain("price has not reached the demand area");
    expect(html).toContain("Bar 25 Sep 2026, 10:31 IST");
    expect(whyWhen(rows[2])).toBe("Seen 25 Sep 2026, 09:00 IST");
    expect(html).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
  });

  it("empty, loading and failed states in plain words", () => {
    expect(renderToStaticMarkup(<WhyList rows={[]} />)).toContain("No bar read yet");
    expect(renderToStaticMarkup(<WhyList rows={null} />)).toContain("Loading");
    expect(renderToStaticMarkup(<WhyList rows={null} failed />)).toContain("Could not load the reasons");
  });

  it("shown on a running bot's row only behind the why_no_trade flag", () => {
    expect(pageSource).toContain('why={features.on("why_no_trade") ? <BotWhyV2 botId={bot.id} /> : undefined}');
    expect(pageSource).toContain('{state !== "stopped" && why}');
  });
});
