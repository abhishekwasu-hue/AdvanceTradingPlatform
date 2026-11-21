import { Bot, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { errorSentence } from "../api/errors";
import { Card } from "../components/ui";
import { Details } from "../ui/Details";
import { useConfirm } from "../ui/ConfirmDialog";
import { formatDateTimeIST } from "../ui/format";

/**
 * SRV2 P3 (owner AGENT-MODES 20 Nov, ADR-0029): the owner's trading agent mode for his 15M SRv2 bot.
 * - "Ask me first" (human_confirm, the default): a Telegram card, and only his ✅ places the entry.
 * - "Automatic" (auto): the bot places the entry itself; Vision can stay on as a check.
 * Shown only to the owner account (the server says `can_switch`); every switch is audited on the server. Exits never wait on
 * this; LIVE stays his own switch.
 */
export type AgentMode = "human_confirm" | "auto";
export interface AgentModeStatus {
  installed: boolean; mode: AgentMode; vision_on: boolean; can_switch: boolean;
  last: { by: number | null; at: string | null; from: string | null; to: string | null; vision_on: boolean | null } | null;
}

export const agentModeApi = {
  status: () => request<AgentModeStatus>("/admin/controls/srv2-agent-mode"),
  set: (mode: AgentMode, vision_on: boolean, confirm: boolean) => request<AgentModeStatus>("/admin/controls/srv2-agent-mode",
    { method: "PUT", body: JSON.stringify({ mode, vision_on, confirm }) }),
};

const LABEL: Record<AgentMode, string> = { human_confirm: "Ask me first", auto: "Automatic" };

export function lastLine(status: AgentModeStatus): string | null {
  const l = status.last;
  if (!l?.to) return null;
  const to = LABEL[l.to as AgentMode] ?? l.to;
  const from = l.from ? LABEL[l.from as AgentMode] ?? l.from : null;
  const when = l.at ? formatDateTimeIST(l.at) : "";
  return `Last change ${when}: ${from ? `${from} → ` : ""}${to}${l.to === "auto" ? (l.vision_on ? " (Vision on)" : " (Vision off)") : ""}`;
}

export function AgentModeView({ status, busy = false, onPick }: {
  status: AgentModeStatus; busy?: boolean; onPick?: (mode: AgentMode, visionOn: boolean) => void;
}) {
  const auto = status.mode === "auto";
  const off = busy || !status.installed;
  const btn = (on: boolean) => `inline-flex min-h-11 items-center gap-space-2 rounded-control px-space-3 text-t14 font-semibold md:min-h-9 disabled:opacity-50 ${
    on ? "bg-brand text-on-brand" : "border border-border text-fg hover:bg-surface-2"}`;
  const line = lastLine(status);
  return (
    <div className="space-y-space-2 text-t13" data-agent-mode={status.mode}>
      <div className="flex flex-wrap gap-space-2" role="group" aria-label="Agent mode">
        <button type="button" className={btn(!auto)} aria-pressed={!auto} disabled={off} onClick={() => { if (auto) onPick?.("human_confirm", true); }}>
          <ShieldCheck size={14} aria-hidden /> {LABEL.human_confirm}
        </button>
        <button type="button" className={btn(auto)} aria-pressed={auto} disabled={off} onClick={() => { if (!auto) onPick?.("auto", true); }}>
          <Bot size={14} aria-hidden /> {LABEL.auto}
        </button>
      </div>
      {auto && (
        <label className="flex items-center gap-space-2 text-fg">
          <input type="checkbox" checked={status.vision_on} disabled={off} onChange={(e) => onPick?.("auto", e.target.checked)} />
          Vision checks each setup first
        </label>
      )}
      {!status.installed && <p className="text-fg-muted">The trading agent is not installed on this server.</p>}
      {line && <p className="text-fg-muted" data-agent-mode-last>{line}</p>}
      <Details id="admin.agent-mode" className="mt-0">
        Ask me first: each setup comes to Telegram as a card, and only your ✅ places the entry; no ✅ in time means no entry.
        Automatic: the bot places the entry itself on your own settings. Exits always run. LIVE stays your own switch.
      </Details>
    </div>
  );
}

export default function AgentModeCard() {
  const [status, setStatus] = useState<AgentModeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { ask, dialog } = useConfirm();

  useEffect(() => { agentModeApi.status().then(setStatus, () => setStatus(null)); }, []);

  async function pick(mode: AgentMode, visionOn: boolean) {
    if (mode === "auto" && status?.mode !== "auto") {
      const ok = await ask("Switch to Automatic?", "The bot will place entries itself, without your ✅. Exits always run.", "Switch to Automatic");
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      setStatus(await agentModeApi.set(mode, visionOn, mode === "auto"));
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status?.can_switch) return null;
  return (
    <div data-console-card="agent-mode"><Card title="My trading agent">
      {error && <p className="mb-space-2 text-t13 text-down">{error}</p>}
      <AgentModeView status={status} busy={busy} onPick={(m, v) => void pick(m, v)} />
      {dialog}
    </Card></div>
  );
}
