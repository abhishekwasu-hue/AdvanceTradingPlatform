import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import { criterionText } from "../components/paperLadder";
import type { ExperienceStatus } from "../types";
import { gateLine, gateState, nextLevel, reasonOk } from "./experience";
import { useProgressionT } from "./i18n";
import { KeyValueTable, Signed, TextOnDemand } from "../ui/numbersFirst";
import { loadSwitches } from "../settings/myLimits";
import { useUiV2 } from "../ui/useFeatures";

/**
 * N2-2 (flag `experience_levels`): the caller's experience level from their own PAPER record, the gates it opens
 * (futures, naked short options) and the explicit opt-out for today: logged and counted. Hidden while the flag is off
 * (the API answers 404). Exits are never gated.
 */
export default function ExperienceCard({ canTrade }: { canTrade: boolean }) {
  const [status, setStatus] = useState<ExperienceStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // UI-28 U1.7 (with DEF-0 D4): on the v2 pages the card shows only when the user turned on "Limit me by my experience
  // level" in My limits; otherwise no Beginner / Intermediate labels anywhere (the gate itself is server-side).
  const v2 = useUiV2();
  const [gateOn, setGateOn] = useState<boolean | null>(null);
  useEffect(() => { api.paperExperience().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(() => {
    if (!v2) return;
    loadSwitches().then((r) => setGateOn(r.value.limit_by_experience), () => setGateOn(false));
  }, [v2]);
  if (!status || (v2 && !gateOn)) return null;

  async function optOut(gate: string, reason: string): Promise<boolean> {
    setMessage(null);
    try {
      setStatus(await api.paperExperienceOptOut(gate, reason));
      return true;
    } catch (e) {
      setMessage(String(e));
      return false;
    }
  }
  return <ExperiencePanel status={status} canTrade={canTrade} onOptOut={optOut} error={message} />;
}

export function ExperiencePanel({ status, canTrade, onOptOut, error }: {
  status: ExperienceStatus; canTrade: boolean; onOptOut: (gate: string, reason: string) => Promise<boolean>; error?: string | null;
}) {
  const t = useProgressionT();
  const v2 = useUiV2();                       // UX-11: numbers first, the explanations under Details
  const [open, setOpen] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const next = nextLevel(status);
  const r = status.record;

  async function confirm(gate: string, label: string) {
    setBusy(true);
    const ok = await onOptOut(gate, reason.trim());
    setBusy(false);
    if (ok) { setOpen(null); setReason(""); setDone(t("experience.optOut.done", { gate: label })); }
  }

  return (
    <Card title={t("experience.title")} className="space-y-3">
      <div data-testid="experience" className="space-y-3 text-xs">
        {!v2 && <p className="text-fg-muted">{t("experience.intro")}</p>}
        <ol className="flex flex-wrap items-center gap-2" aria-label={t("experience.levelsLabel")}>
          {status.levels.map((l, i) => (
            <li key={l.key} className="flex items-center gap-2" aria-current={l.key === status.level ? "step" : undefined}>
              {i > 0 && <span className="text-fg-muted" aria-hidden>→</span>}
              <span className={`rounded px-2 py-0.5 ${l.key === status.level ? "bg-info/15 text-info" : l.reached ? "bg-surface-2 text-fg" : "bg-surface-2 text-fg-muted"}`}>
                {l.label}{l.key === status.level ? ` · ${t("experience.current")}` : ""}
              </span>
            </li>
          ))}
        </ol>
        {v2 ? (
          // UI-28 U1.9: "Sessions" is shown once, on the progression ladder.
          <KeyValueTable caption={t("experience.title")} rows={[
            { label: "Closed PAPER trades", value: r.closed_trades, testId: "trades" },
            { label: "Largest drawdown", value: <Signed value={-Math.abs(r.max_drawdown_pct)} kind="pct" />, testId: "drawdown" },
          ]} />
        ) : <p className="text-fg">{t("experience.record", { trades: r.closed_trades, sessions: r.sessions, dd: r.max_drawdown_pct.toFixed(2) })}</p>}
        {next ? (
          <div data-testid="experience-next">
            <p className="text-fg-muted">{t("experience.next", { level: next.label, opens: next.opens.join(", ").toLowerCase() })}</p>
            <ul className="mt-1 space-y-0.5">
              {next.criteria.filter((c) => !v2 || c.key !== "sessions").map((c) => (   /* UI-28 U1.9: sessions live on the ladder only */
                <li key={c.key} className="flex justify-between gap-2">
                  <span className="text-fg">{c.label}</span>
                  <span className={c.met ? "text-up" : "text-fg-muted"}>{criterionText(c)}{c.met ? " ✓" : ""}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : <p className="text-fg-muted">{t("experience.allReached")}</p>}

        <ul className="space-y-2" aria-label={t("experience.gatesLabel")}>
          {status.gates.map((g) => {
            const state = gateState(g);
            return (
              <li key={g.key} className="rounded-lg border border-border p-2" data-testid={`gate-${g.key}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-fg">{g.label}</span>
                  <span className={state === "allowed" ? "text-up" : state === "optedOut" ? "text-warn" : "text-fg-muted"}>{gateLine(g, t)}</span>
                </div>
                {state === "shut" && canTrade && open !== g.key && (
                  <button type="button" onClick={() => { setOpen(g.key); setReason(""); setDone(null); }}
                          className="mt-1 min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 sm:min-h-0">
                    {t("experience.optOut.open")}
                  </button>
                )}
                {state === "shut" && canTrade && open === g.key && (
                  <div className="mt-2 space-y-1">
                    <label className="block text-fg-muted" htmlFor={`optout-${g.key}`}>{t("experience.optOut.reasonLabel", { min: status.min_reason_chars })}</label>
                    <textarea id={`optout-${g.key}`} value={reason} maxLength={300} rows={2} onChange={(e) => setReason(e.target.value)}
                              className="w-full rounded border border-border bg-surface-2 px-2 py-1 text-fg" />
                    <p className="text-xs text-fg-muted">{t("experience.optOut.caveat")}</p>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" disabled={busy || !reasonOk(reason, status.min_reason_chars)} onClick={() => void confirm(g.key, g.label)}
                              className="min-h-[44px] rounded border border-warn px-2 py-1 text-warn hover:bg-warn/10 disabled:opacity-50 sm:min-h-0">
                        {t("experience.optOut.confirm", { gate: g.label.toLowerCase() })}
                      </button>
                      <button type="button" onClick={() => setOpen(null)}
                              className="min-h-[44px] rounded border border-border px-2 py-1 text-fg hover:bg-surface-2 sm:min-h-0">
                        {t("experience.optOut.cancel")}
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {(done || error) && <p role="status" className={error ? "text-down" : "text-fg-muted"}>{error ?? done}</p>}

        <TextOnDemand id="mytrades.experience" on={v2}>
          {v2 && <p>{t("experience.intro")}</p>}
          {v2 && <p>{t("experience.record", { trades: r.closed_trades, sessions: r.sessions, dd: r.max_drawdown_pct.toFixed(2) })}</p>}
          <p className="text-fg-muted" data-testid="experience-counts">
            {t("experience.counts", { today: status.opt_outs.today, week: status.opt_outs.last_7_days, total: status.opt_outs.total })}
          </p>
          {status.opt_outs.recent.length > 0 && (
            <ul className="space-y-0.5 text-xs text-fg-muted" aria-label={t("experience.recentLabel")}>
              {status.opt_outs.recent.map((o, i) => (
                <li key={`${o.day}-${o.gate}-${i}`}>{t("experience.recentRow", { day: o.day, gate: o.gate_label, level: o.level.toLowerCase(), reason: o.reason })}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-fg-muted">{status.exits}</p>
        </TextOnDemand>
      </div>
    </Card>
  );
}
