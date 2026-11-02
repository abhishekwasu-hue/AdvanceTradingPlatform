import { useEffect, useState } from "react";
import { request } from "../api/client";
import { errorSentence } from "../api/errors";
import { Card } from "../components/ui";
import { Button, Input } from "../components/primitives";

/**
 * M72 PR-2 (F20, M72Q-7): Admin › Controls › Limits - the Copilot planner's limits per question: model steps, tool
 * calls, seconds and tokens per step. Each field shows the value in force; an empty field goes back to the default; the
 * server keeps every value inside its bounds (400 otherwise).
 */
export type AgentLimitKey = "max_steps" | "max_tool_calls" | "max_seconds" | "max_tokens_per_step";
export interface AgentLimits {
  limits: Record<AgentLimitKey, number>;
  defaults: Record<AgentLimitKey, number>;
  bounds: Record<AgentLimitKey, { low: number; high: number }>;
}
export const AGENT_LIMIT_FIELDS: readonly { key: AgentLimitKey; label: string }[] = [
  { key: "max_steps", label: "Model steps per question" }, { key: "max_tool_calls", label: "Tool calls per question" },
  { key: "max_seconds", label: "Seconds per question" }, { key: "max_tokens_per_step", label: "Tokens per step" },
];
export const agentLimitsApi = {
  get: () => request<AgentLimits>("/admin/controls/copilot-agent-limits"),
  set: (values: Partial<Record<AgentLimitKey, number | null>>) =>
    request<AgentLimits>("/admin/controls/copilot-agent-limits", { method: "PUT", body: JSON.stringify({ values }) }),
};

/** A field's text as a value: "" = back to the default (null), a number inside the bounds, else undefined (invalid). */
export function parseLimit(text: string, bounds: { low: number; high: number }): number | null | undefined {
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= bounds.low && n <= bounds.high ? n : undefined;
}

const textsOf = (l: AgentLimits) => Object.fromEntries(AGENT_LIMIT_FIELDS.map((f) => [f.key, String(l.limits[f.key])])) as Record<AgentLimitKey, string>;

export function CopilotAgentLimitsView({ limits, busy, onSave }: {
  limits: AgentLimits; busy?: boolean; onSave: (values: Record<AgentLimitKey, number | null>) => void;
}) {
  const [texts, setTexts] = useState(() => textsOf(limits));
  useEffect(() => setTexts(textsOf(limits)), [limits]);
  const parsed = Object.fromEntries(AGENT_LIMIT_FIELDS.map((f) => [f.key, parseLimit(texts[f.key], limits.bounds[f.key])])) as Record<AgentLimitKey, number | null | undefined>;
  const invalid = Object.values(parsed).some((v) => v === undefined);
  return (
    <Card title="Copilot AI planner">
      <div data-agent-limits className="space-y-space-3">
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2 lg:grid-cols-4">
          {AGENT_LIMIT_FIELDS.map((f) => {
            const b = limits.bounds[f.key];
            return (
              <Input key={f.key} label={f.label} type="number" inputMode="decimal" min={b.low} max={b.high} placeholder={String(limits.defaults[f.key])}
                value={texts[f.key]} onChange={(e) => setTexts({ ...texts, [f.key]: e.target.value })}
                error={parsed[f.key] === undefined ? `${b.low} to ${b.high}` : undefined} data-agent-limit={f.key} />
            );
          })}
        </div>
        <Button size="v2" variant="primary" disabled={busy || invalid} onClick={() => onSave(parsed as Record<AgentLimitKey, number | null>)} data-save-agent-limits>
          Save planner limits
        </Button>
      </div>
    </Card>
  );
}

/** The card with its server state (admin only; the route answers 403 to anyone else). */
export default function CopilotAgentLimitsCard() {
  const [limits, setLimits] = useState<AgentLimits | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    agentLimitsApi.get().then((l) => { if (live) setLimits(l); }).catch((e) => { if (live) setNotice(errorSentence(e)); });
    return () => { live = false; };
  }, []);
  if (!limits) return notice ? <Card title="Copilot AI planner"><p className="text-t13 text-down">{notice}</p></Card> : null;
  async function save(values: Record<AgentLimitKey, number | null>) {
    setBusy(true);
    setNotice(null);
    try {
      setLimits(await agentLimitsApi.set(values));
      setNotice("Planner limits saved.");
    } catch (e) {
      setNotice(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-space-1">
      <CopilotAgentLimitsView limits={limits} busy={busy} onSave={(v) => void save(v)} />
      {notice && <p className="text-t13 text-fg-muted" role="status">{notice}</p>}
    </div>
  );
}
