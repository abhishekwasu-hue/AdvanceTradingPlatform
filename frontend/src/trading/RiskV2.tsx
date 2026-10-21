import { useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import type { GuardianStatus, PaperRiskStatus, RiskCeilings, RiskConfig } from "../types";
import { formatINR, formatNumber } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { PageIntro } from "../ui/PageIntro";
import { TermHint } from "../ui/TermHint";
import { drawdownOrderProblem, failedText, fieldProblem, guardianLine, todayRisk } from "./model";
import { BUTTON_PRIMARY, FIELD, LABEL, NoteV2, PageTitleV2, ShareBarV2, StateChipV2, TileV2, TilesV2 } from "./PartsV2";
import { MarketEventsV2, OrgLimitsV2, RiskChecksV2 } from "./RiskPartsV2";

interface FieldSpec { key: keyof RiskConfig; label: string; unit?: string; hint?: GlossaryKey; ceiling?: string; minimum?: string; help: string; step?: string }

/** The account's own limits (today's "Risk Management" fields, in plain words; same keys, same save). */
const LIMIT_FIELDS: FieldSpec[] = [
  { key: "capital", label: "Capital", unit: "₹", help: "The money your sizes and limits are worked out from." },
  { key: "risk_per_trade_pct", label: "Risk per trade", unit: "% of capital", step: "0.1", ceiling: "risk_per_trade_pct", help: "The most one trade can lose at its stop." },
  { key: "max_daily_loss_pct", label: "Daily loss limit", unit: "% of capital", hint: "daily-loss-limit", step: "0.1", ceiling: "max_daily_loss_pct", help: "New entries stop for the day once your losses reach it." },
  { key: "max_trades_per_day", label: "Trades per day", help: "New entries allowed in one day." },
  { key: "max_open_positions", label: "Open positions", help: "Positions open at the same time." },
  { key: "max_consecutive_losses", label: "Losses in a row", help: "New entries stop after this many losing trades in a row." },
  { key: "min_risk_reward", label: "Lowest reward to risk", hint: "reward-risk", step: "0.1", help: "A trade whose target pays less than this for each rupee at risk is refused." },
  { key: "lot_size", label: "Lot size", hint: "lot", help: "Units in one lot, used to size an order." },
];

/** Phase V1: the risk guardian's rules, checked on every entry, paper or live, whatever built the strategy. */
const GUARDIAN_FIELDS: FieldSpec[] = [
  { key: "max_portfolio_risk_pct", label: "Risk across open positions", unit: "% of capital", hint: "risk-at-stops", step: "0.5", ceiling: "max_portfolio_risk_pct", help: "What every open stop and the new trade could lose together. Index positions count as one group." },
  { key: "stop_cooldown_minutes", label: "Cool-down after a stop-out", unit: "minutes", hint: "cool-down", minimum: "min_stop_cooldown_minutes", help: "No new entry in the same index or stock for this long after a stop." },
  { key: "dd_level_1_pct", label: "Halve the size at a drawdown of", unit: "%", hint: "drawdown", step: "0.5", help: "This far below your equity peak, the risk per trade is halved." },
  { key: "dd_level_2_pct", label: "Pause new entries at a drawdown of", unit: "%", step: "0.5", ceiling: "dd_level_2_pct", help: "This far below the peak, new entries pause until your equity recovers." },
  { key: "event_size_cut_pct", label: "Size cut on event days", unit: "%", step: "5", help: "Used by a market event that cuts the size and names no cut of its own." },
];

const ALL_FIELDS = [...LIMIT_FIELDS, ...GUARDIAN_FIELDS];
type Drafts = Partial<Record<keyof RiskConfig, string>>;

const draftsOf = (c: RiskConfig): Drafts => Object.fromEntries(ALL_FIELDS.map((f) => [f.key, String(c[f.key])])) as Drafts;

/** Plan before trade answers 404 while its flag is off: no paper limit to show, not a failure. */
const paperRiskOrNone = () => api.paperRisk().catch((e: unknown) => {
  if (typeof e === "object" && e !== null && (e as { status?: number }).status === 404) return null;
  throw e;
});

/**
 * UI2-9a (UI v2 proposal §2 row 17, §3 M13, §8, §9, §13 UI-11): Risk limits - "How much can I lose today?". Shown only
 * while `ui_v2` is on (the v2 shell picks it for /risk-management); with the flag off today's RiskManagementPage renders
 * exactly as before.
 *
 * Top to bottom: today (the daily loss limit in rupees, risk per trade, how much of the paper limit is in use, the
 * guardian's state for paper and live), the account's limits and guardian rules with the page's one primary action,
 * "Save limits", the market events, the organisation's limits and the recent risk checks. Every save goes to the same
 * endpoint with the same values as today; the server still checks every value against the platform ceilings. Removing
 * an event or a limit is a neutral button with a confirm that names what changes (UI-11). Nothing here places an order,
 * and none of these limits ever blocks an exit (ADR-0004).
 */
export default function RiskV2(_props: PageProps = {}) {
  const { user } = useAuth();
  const [config, setConfig] = useState<RiskConfig | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [touched, setTouched] = useState<Partial<Record<keyof RiskConfig, boolean>>>({});
  const [ceilings, setCeilings] = useState<RiskCeilings | null>(null);
  const [guardian, setGuardian] = useState<GuardianStatus | null>(null);
  const [paper, setPaper] = useState<PaperRiskStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [eventsNonce, setEventsNonce] = useState(0);

  function refreshGuardian() {
    api.guardianStatus().then(setGuardian).catch(() => {});
    setEventsNonce((n) => n + 1);
  }

  useEffect(() => {
    if (!user) return;
    api.getRiskSettings().then((c) => { setConfig(c); setDrafts(draftsOf(c)); }).catch(() => setLoadFailed(true));
    api.riskCeilings().then(setCeilings).catch(() => {});
    paperRiskOrNone().then(setPaper).catch(() => {});
    refreshGuardian();
  }, [user]);

  const ceilingOf = (f: FieldSpec) => (f.ceiling && ceilings ? ceilings[f.ceiling] : undefined);
  const minimumOf = (f: FieldSpec) => (f.minimum && ceilings ? ceilings[f.minimum] : undefined);
  const problemOf = (f: FieldSpec) => fieldProblem(drafts[f.key] ?? "", ceilingOf(f), minimumOf(f))
    ?? (f.key === "dd_level_1_pct" ? drawdownOrderProblem(drafts.dd_level_1_pct ?? "", drafts.dd_level_2_pct ?? "") : null);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!config) return;
    setTouched(Object.fromEntries(ALL_FIELDS.map((f) => [f.key, true])));
    if (ALL_FIELDS.some((f) => problemOf(f) !== null)) {
      setNotice({ kind: "warn", text: "Nothing was saved. Fix the values marked below, then save again." });
      return;
    }
    const next: RiskConfig = { ...config };
    for (const f of ALL_FIELDS) (next as unknown as Record<string, number>)[f.key] = Number(drafts[f.key]);
    setSaving(true);
    setNotice(null);
    try {
      const saved = await api.updateRiskSettings(next);
      setConfig(saved);
      setDrafts(draftsOf(saved));
      setNotice({ kind: "done", text: "Saved. Every paper and live entry now uses these limits and guardian rules." });
      refreshGuardian();
    } catch (err) {
      setNotice({ kind: "warn", text: failedText("Your limits were not saved.", err) });
    } finally {
      setSaving(false);
    }
  }

  if (!user) {
    return (
      <div className="mx-auto w-full space-y-space-4" data-page="risk-v2">
        <PageTitleV2 title="Risk limits" subtitle="How much you can lose today, and the rules every entry is checked against." />
        <NoteV2>
          Sign in to set your own limits. <Link to="/account" className="font-semibold text-brand hover:underline">Sign in</Link>
        </NoteV2>
      </div>
    );
  }

  const today = todayRisk(config, paper);
  const canEditOrg = user.role === "OWNER" || user.role === "SUPER_ADMIN";

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="risk-v2">
      <PageIntro id="risk" lines={[
        "How much you can lose today, and the limits every paper and live entry is checked against before it is placed.",
        "Change a limit and save it; exits are never blocked by these limits.",
      ]} />
      <PageTitleV2 title="Risk limits" subtitle="How much you can lose today, and the rules every paper and live entry is checked against." />

      <CardV2 title="Today" testId="today">
        {!config && !loadFailed ? <LoadingRowsV2 rows={2} /> : loadFailed ? <LoadFailedV2 what="your limits" /> : (
          <div className="space-y-space-4">
            <TilesV2 columns={today.paperLimit !== null ? 3 : 2}>
              <TileV2 testId="daily-limit" label="Daily loss limit" hint="daily-loss-limit" value={formatINR(today.limit)}
                sub={today.limitPct === null ? undefined : `${formatNumber(today.limitPct, 1)}% of ${formatINR(config?.capital, { compact: true })} capital`} />
              <TileV2 testId="per-trade" label="Risk per trade" value={formatINR(today.perTrade)}
                sub={config ? `${formatNumber(config.risk_per_trade_pct, 2)}% of capital` : undefined} />
              {today.paperLimit !== null && (
                <TileV2 testId="paper-used" label="Paper limit in use" hint="risk-in-use"
                  value={today.paperUsedPct === null ? "–" : `${formatNumber(today.paperUsedPct, 0)}%`} tone={today.paperUsedPct !== null && today.paperUsedPct >= 80 ? "warn" : "neutral"}
                  sub={`${formatINR(today.paperUsed, { compact: true })} of ${formatINR(today.paperLimit, { compact: true })} today`}>
                  {today.paperUsedPct !== null && <ShareBarV2 pct={today.paperUsedPct} warn={today.paperUsedPct >= 80} />}
                </TileV2>
              )}
            </TilesV2>
            {today.lockout && <NoteV2 kind="warn" testId="lockout">{today.lockout}</NoteV2>}
            {guardian && (
              <ul className="divide-y divide-subtle rounded-control border border-subtle" data-guardian>
                {(["PAPER", "LIVE"] as const).map((mode) => {
                  const line = guardianLine(mode, guardian.modes[mode]);
                  return (
                    <li key={mode} className="px-space-3 py-space-3 md:px-space-4" data-mode={mode}>
                      <div className="flex flex-wrap items-center justify-between gap-space-2">
                        <span className="text-t14 font-semibold text-fg">{line.mode}</span>
                        <StateChipV2 tone={line.tone}>{line.state}</StateChipV2>
                      </div>
                      {line.detail.map((d) => <p key={d} className="mt-space-1 font-tabular text-t13 text-fg-muted">{d}</p>)}
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="text-t13 text-fg-muted">
              {guardian
                ? `The size is halved at ${formatNumber(guardian.settings.dd_level_1_pct, 1)}% below your equity peak and new entries pause at ${formatNumber(guardian.settings.dd_level_2_pct, 1)}%. A winning streak never raises the size above normal. Exits are never blocked.`
                : "Exits are never blocked by these limits."}
            </p>
          </div>
        )}
      </CardV2>

      {notice && <NoteV2 kind={notice.kind} testId="save">{notice.text}</NoteV2>}

      {config && (
        <form onSubmit={save} noValidate data-block="limits"
          className="rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5">
          <h2 className="text-t15 font-semibold text-fg">Your limits</h2>
          <p className="mt-space-1 text-t13 text-fg-muted">Checked before every paper and live entry you place. A limit can never be set above the platform&apos;s ceiling.</p>
          <FieldGroup title="Size and daily limits" fields={LIMIT_FIELDS} drafts={drafts} touched={touched} ceilingOf={ceilingOf} minimumOf={minimumOf} problemOf={problemOf}
            onChange={(k, v) => setDrafts((d) => ({ ...d, [k]: v }))} onBlur={(k) => setTouched((t) => ({ ...t, [k]: true }))} />
          <FieldGroup title="Guardian rules" fields={GUARDIAN_FIELDS} drafts={drafts} touched={touched} ceilingOf={ceilingOf} minimumOf={minimumOf} problemOf={problemOf}
            onChange={(k, v) => setDrafts((d) => ({ ...d, [k]: v }))} onBlur={(k) => setTouched((t) => ({ ...t, [k]: true }))}
            note="The risk guardian checks these on every entry, paper or live, whatever built the strategy." />
          <div className="mt-space-5">
            <button type="submit" disabled={saving} className={`${BUTTON_PRIMARY} w-full sm:w-auto`}>{saving ? "Saving…" : "Save limits"}</button>
          </div>
        </form>
      )}

      <MarketEventsV2 user={user} defaultCut={config?.event_size_cut_pct ?? null} nonce={eventsNonce} onChanged={refreshGuardian} />
      <OrgLimitsV2 canEdit={canEditOrg} />
      <RiskChecksV2 />
    </div>
  );
}

function FieldGroup({ title, note, fields, drafts, touched, ceilingOf, minimumOf, problemOf, onChange, onBlur }: {
  title: string; note?: string; fields: FieldSpec[]; drafts: Drafts; touched: Partial<Record<keyof RiskConfig, boolean>>;
  ceilingOf: (f: FieldSpec) => number | undefined; minimumOf: (f: FieldSpec) => number | undefined; problemOf: (f: FieldSpec) => string | null;
  onChange: (k: keyof RiskConfig, v: string) => void; onBlur: (k: keyof RiskConfig) => void;
}) {
  return (
    <fieldset className="mt-space-5">
      <legend className="text-t14 font-semibold text-fg">{title}</legend>
      {note && <p className="mt-space-1 text-t13 text-fg-muted">{note}</p>}
      <div className="mt-space-3 grid grid-cols-1 gap-space-4 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((f) => (
          <LimitField key={f.key} spec={f} value={drafts[f.key] ?? ""} ceiling={ceilingOf(f)} minimum={minimumOf(f)} problem={touched[f.key] ? problemOf(f) : null}
            onChange={(v) => onChange(f.key, v)} onBlur={() => onBlur(f.key)} />
        ))}
      </div>
    </fieldset>
  );
}

function LimitField({ spec, value, ceiling, minimum, problem, onChange, onBlur }: {
  spec: FieldSpec; value: string; ceiling: number | undefined; minimum: number | undefined; problem: string | null; onChange: (v: string) => void; onBlur: () => void;
}) {
  const id = useId();
  const helpId = `${id}-help`;
  const errId = `${id}-error`;
  return (
    <div className="min-w-0" data-field={spec.key}>
      <div className={LABEL}>
        <label htmlFor={id}>{spec.label}{spec.unit ? ` (${spec.unit})` : ""}</label>
        {spec.hint && <TermHint term={spec.hint} />}
      </div>
      <input id={id} type="number" inputMode="decimal" step={spec.step ?? "1"} min={0} value={value}
        onChange={(e) => onChange(e.target.value)} onBlur={onBlur}
        aria-invalid={problem ? true : undefined} aria-describedby={problem ? `${errId} ${helpId}` : helpId}
        className={`${FIELD} font-tabular ${problem ? "border-warn" : ""}`} />
      {problem && <p id={errId} role="alert" className="mt-space-1 text-t12 font-medium text-warn">{problem}</p>}
      <p id={helpId} className="mt-space-1 text-t12 text-fg-muted">
        {spec.help}{typeof minimum === "number" && minimum > 0 ? ` At least ${formatNumber(minimum, Number.isInteger(minimum) ? 0 : 2)}.` : ""}
        {typeof ceiling === "number" ? ` Up to ${formatNumber(ceiling, Number.isInteger(ceiling) ? 0 : 2)}.` : ""}
      </p>
    </div>
  );
}
