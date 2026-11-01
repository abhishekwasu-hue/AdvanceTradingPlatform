import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { parseRupees, platformAiApi, type PlatformLimits } from "../aiKeys/platformAiApi";
import { Card } from "../components/ui";
import { Badge, Button, Input } from "../components/primitives";

/**
 * M72 PR-1b (M72Q-1): Admin › Controls › Limits. The owner's numbers for the platform AI key: each plan's monthly budget
 * and the daily cap per user, in rupees. Every field starts Not set (ATP chooses no number) and Not set = ATP's rules
 * answer; an empty field clears the owner's value. The key itself never reaches the browser - only whether the server
 * has one.
 */
const rupeeText = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));
const budgetTexts = (l: PlatformLimits) => Object.fromEntries(l.plans.map((p) => [p.id, rupeeText(l.plan_budget_inr[p.id])]));

export function AiPlatformLimitsView({ limits, busy, onSave }: {
  limits: PlatformLimits; busy?: boolean;
  onSave: (body: { plan_budget_inr: Record<string, number | null>; user_daily_cap_inr?: number | null; clear_cap?: boolean }) => void;
}) {
  const [budgets, setBudgets] = useState<Record<string, string>>(() => budgetTexts(limits));
  const [cap, setCap] = useState(() => rupeeText(limits.user_daily_cap_inr));
  useEffect(() => {
    setBudgets(budgetTexts(limits));
    setCap(rupeeText(limits.user_daily_cap_inr));
  }, [limits]);
  const parsedBudgets = Object.fromEntries(limits.plans.map((p) => [p.id, parseRupees(budgets[p.id] ?? "")]));
  const parsedCap = parseRupees(cap);
  const invalid = parsedCap === undefined || Object.values(parsedBudgets).some((v) => v === undefined);
  function save() {
    const plan_budget_inr = parsedBudgets as Record<string, number | null>;
    onSave(parsedCap === null ? { plan_budget_inr, clear_cap: true } : { plan_budget_inr, user_daily_cap_inr: parsedCap as number });
  }
  return (
    <Card title="Platform AI key">
      <div data-ai-platform-limits className="space-y-space-3">
        <div className="flex flex-wrap items-center gap-space-2 text-t13">
          <span className="text-fg-muted">Key on the server</span>
          {limits.key_configured
            ? <Badge tone="up">Ready{limits.provider ? ` · ${limits.provider === "anthropic" ? "Anthropic" : "OpenAI"}` : ""}</Badge>
            : <Badge tone="neutral">Not set</Badge>}
        </div>
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2 lg:grid-cols-4">
          {limits.plans.map((p) => (
            <Input key={p.id} label={`${p.name}: budget a month (₹)`} type="number" inputMode="decimal" min={0} placeholder="Not set"
              value={budgets[p.id] ?? ""} onChange={(e) => setBudgets({ ...budgets, [p.id]: e.target.value })}
              error={parsedBudgets[p.id] === undefined ? "A number, 0 or more" : undefined} data-plan-budget={p.id} />
          ))}
          <Input label="Daily cap per user (₹)" type="number" inputMode="decimal" min={0} placeholder="Not set" value={cap}
            onChange={(e) => setCap(e.target.value)} error={parsedCap === undefined ? "A number, 0 or more" : undefined} data-user-cap />
        </div>
        <p className="text-t12 text-fg-muted">Not set = answers from ATP's rules. A user's own key, or his organisation's, is used first.</p>
        <Button size="v2" variant="primary" disabled={busy || invalid} onClick={save} data-save-ai-platform>Save AI limits</Button>
      </div>
    </Card>
  );
}

/** The card with its server state (admin only; the route answers 403 to anyone else). */
export default function AiPlatformLimitsCard() {
  const [limits, setLimits] = useState<PlatformLimits | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    platformAiApi.limits().then((l) => { if (live) setLimits(l); }).catch((e) => { if (live) setNotice(errorSentence(e)); });
    return () => { live = false; };
  }, []);
  if (!limits) return notice ? <Card title="Platform AI key"><p className="text-t13 text-down">{notice}</p></Card> : null;
  async function save(body: Parameters<typeof platformAiApi.setLimits>[0]) {
    setBusy(true);
    setNotice(null);
    try {
      setLimits(await platformAiApi.setLimits(body));
      setNotice("AI limits saved.");
    } catch (e) {
      setNotice(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-space-1">
      <AiPlatformLimitsView limits={limits} busy={busy} onSave={(b) => void save(b)} />
      {notice && <p className="text-t13 text-fg-muted" role="status">{notice}</p>}
    </div>
  );
}
