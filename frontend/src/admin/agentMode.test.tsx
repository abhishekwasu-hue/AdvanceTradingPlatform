import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentModeView, lastLine, type AgentModeStatus } from "./AgentModeCard";

/** SRV2 P3 (ADR-0029): the owner's agent mode card - the two modes, the Vision switch only in Automatic, the last audited change. */
const base: AgentModeStatus = { installed: true, mode: "human_confirm", vision_on: true, can_switch: true, last: null };

describe("SRV2 P3: agent mode card", () => {
  it("Ask me first is the shown default; no Vision switch outside Automatic", () => {
    const html = renderToStaticMarkup(<AgentModeView status={base} />);
    expect(html).toContain('data-agent-mode="human_confirm"');
    expect(html).toMatch(/aria-pressed="true"[^>]*>.*Ask me first/s);
    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toContain("data-agent-mode-last");
  });

  it("in Automatic the Vision switch is shown, and the last change reads from -> to", () => {
    const s: AgentModeStatus = { ...base, mode: "auto", vision_on: false,
      last: { by: 1, at: "2026-11-21T04:00:00+00:00", from: "human_confirm", to: "auto", vision_on: false } };
    const html = renderToStaticMarkup(<AgentModeView status={s} />);
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("Vision checks each setup first");
    expect(lastLine(s)).toMatch(/Ask me first → Automatic \(Vision off\)$/);
    expect(lastLine(base)).toBeNull();
  });

  it("not installed: both buttons are disabled and it says so", () => {
    const html = renderToStaticMarkup(<AgentModeView status={{ ...base, installed: false }} />);
    expect((html.match(/disabled=""/g) ?? []).length).toBe(2);
    expect(html).toContain("not installed on this server");
  });
});
