/**
 * Owner 25 Nov ("Terminal var SRV2 bot status dakhav"): the owner's trading agent (the 15M SRv2 bot) on the Terminal's Bots & signals
 * panel - one line: whether it runs, whether his Telegram (✅ cards) and his AI key (Vision) reach it, and its last step. Read only;
 * owner only on the server (`GET /admin/controls/srv2-status`: another admin gets `hidden`, a user is refused and sees nothing).
 */
import { request } from "../api/client";
import { formatTimeIST } from "../ui/format";
import type { ChipTone } from "../ui/Chip";

export type AgentState = "hidden" | "not_installed" | "off" | "not_running" | "no_telegram" | "running";

export interface AgentStatus {
  state: AgentState; installed?: boolean; on?: boolean; running?: boolean; last_step_at?: string | null;
  telegram?: boolean; vision?: boolean;
}

export const fetchAgentStatus = () => request<AgentStatus>("/admin/controls/srv2-status");

/** The chip: the one word for the state, coloured by meaning (running green, a problem amber, off grey). */
export const AGENT_CHIP: Record<Exclude<AgentState, "hidden" | "not_installed">, { text: string; tone: ChipTone }> = {
  running: { text: "Running", tone: "up" },
  no_telegram: { text: "Telegram not linked", tone: "warn" },
  not_running: { text: "Not running", tone: "warn" },
  off: { text: "Off", tone: "neutral" },
};

/** Shown only when the server says the line is his and the bot is installed. */
export const agentShown = (s: AgentStatus | null | undefined): s is AgentStatus & { state: keyof typeof AGENT_CHIP } =>
  !!s && s.state !== "hidden" && s.state !== "not_installed";

/** The second line: what reaches the agent and when it last stepped ("Telegram linked · Vision on · last step 17:28 IST"). */
export function agentDetail(s: AgentStatus): string {
  if (s.state === "off") return "Turned off. Turn it on in Admin.";
  if (s.state === "not_running") return s.last_step_at ? `No step since ${formatTimeIST(s.last_step_at)}.` : "It has not started yet.";
  const parts = [s.telegram ? "Telegram linked" : "Telegram not linked", s.vision ? "Vision on" : "Vision off (no AI key)"];
  if (s.last_step_at) parts.push(`last step ${formatTimeIST(s.last_step_at)}`);
  return parts.join(" · ");
}
