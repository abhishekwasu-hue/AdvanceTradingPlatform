import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BotsViewV2 } from "./PanelsV2";
import { agentDetail, agentShown, type AgentStatus } from "./agentStatus";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const view = (agent: AgentStatus | null) => renderToStaticMarkup(<MemoryRouter><BotsViewV2 now={new Date("2026-11-25T12:00:00Z")} agent={agent}
  bots={{ status: "ok", value: [] }} signals={{ status: "ok", value: [] }} /></MemoryRouter>);

describe("owner 25 Nov: his trading agent's status on the Terminal", () => {
  it("running: one line with Telegram, Vision and the last step in IST; the empty 'No bot' text gives way to it", () => {
    const html = view({ state: "running", telegram: true, vision: true, last_step_at: "2026-11-25T11:58:22+00:00" });
    const t = text(html);
    expect(html).toContain('data-agent-status="running"');
    expect(t).toContain("My trading agent");
    expect(t).toContain("Running");
    expect(t).toContain("Telegram linked · Vision on · last step 17:28 IST");
    expect(t).not.toContain("No bot is running.");
    expect(t).not.toMatch(/srv2|SRv2|flag|token|TELEGRAM_|ANTHROPIC/i);           // no developer text, no names of settings
  });

  it("a problem reads as a plain sentence with an amber chip; off is grey", () => {
    expect(text(view({ state: "no_telegram", telegram: false, vision: true, last_step_at: "2026-11-25T11:58:22+00:00" })))
      .toContain("Telegram not linked · Vision on");
    expect(view({ state: "not_running", last_step_at: "2026-11-25T10:00:00+00:00" })).toContain('data-agent-status="not_running"');
    expect(agentDetail({ state: "not_running", last_step_at: "2026-11-25T10:00:00+00:00" })).toBe("No step since 15:30 IST.");
    expect(agentDetail({ state: "off" })).toBe("Turned off. Turn it on in Admin.");
  });

  it("hidden for anyone but the owner, and when the bot is not installed: the panel is as before", () => {
    for (const agent of [null, { state: "hidden" } as AgentStatus, { state: "not_installed" } as AgentStatus]) {
      const html = view(agent);
      expect(html).not.toContain("data-agent-status");
      expect(text(html)).toContain("No bot is running.");
    }
    expect(agentShown({ state: "running" })).toBe(true);
    expect(agentShown({ state: "hidden" })).toBe(false);
  });
});
