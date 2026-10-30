import { useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import type { GuardianStatus, LossStreakPause, LossStreakStatus, PaperRiskStatus, RiskCeilings, RiskConfig } from "../types";
import { formatINR, formatNumber } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
import { PageIntro } from "../ui/PageIntro";
import { Details, PageFootnote } from "../ui/numbersFirst";
import { useLiveHidden } from "../settings/useLiveHidden";
import { TermHint } from "../ui/TermHint";
import {
  LOSS_STREAK_CHOICES, drawdownOrderProblem, drawdownSentence, entriesWord, failedText, fieldProblem, guardianLine, lastResumeText, limitDraft,
  limitValue, lossStreakNote, todayRisk,
} from "./model";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2, PageTitleV2, ShareBarV2, StateChipV2, TileV2, TilesV2 } from "./PartsV2";
import { LIMIT_FIELDS as SETUP_LIMIT_FIELDS, RISK_LABELS, SETUP_PATH, limitText, type LimitField } from "../setup/model";
import { MarketEventsV2, OrgLimitsV2, RiskChecksV2 } from "./RiskPartsV2";

interface FieldSpec {
  key: keyof RiskConfig; label: string; unit?: string; hint?: GlossaryKey; ceiling?: string; minimum?: string; help: string; step?: string;
  /** REV-H5 (DEF-0): the limit may be "Not set" (null): a blank field saves as not set. */
  optional?: boolean;
  /** REV-H5: the risk engine reads 0 as a real limit here, so 0 blocks every new entry (said in the help). */
  zeroBlocks?: boolean;
  /** REV-H5: what a blank field means, when it is not simply "no limit". */
  blank?: string;
}

/** The account's own limits (today's "Risk Management" fields, in plain words; same keys, same save). */
const LIMIT_FIELDS: FieldSpec[] = [
  { key: "capital", label: "Capital", unit: "₹", optional: true, blank: " Leave it blank to use your paper account balance.", help: "The money your sizes and limits are worked out from." },
  { key: "risk_per_trade_pct", label: "Risk per trade", unit: "% of capital", step: "0.1", ceiling: "risk_per_trade_pct", optional: true, help: "The most one trade can lose at its stop." },
  { key: "max_daily_loss_pct", label: "Daily loss limit", unit: "% of capital", hint: "daily-loss-limit", step: "0.1", ceiling: "max_daily_loss_pct", optional: true, zeroBlocks: true, help: "New entries stop for the day once your losses reach it." },
  { key: "max_trades_per_day", label: "Trades per day", optional: true, zeroBlocks: true, help: "New entries allowed in one day." },
  { key: "max_open_positions", label: "Open positions", optional: true, zeroBlocks: true, help: "Positions open at the same time." },
  { key: "max_consecutive_losses", label: "Losses in a row", optional: true, zeroBlocks: true, help: "New entries pause after this many losing trades in a row. The choice below says when they start again." },
  { key: "min_risk_reward", label: "Lowest reward to risk", hint: "reward-risk", step: "0.1", help: "0 = not set: no minimum. Set your own and a trade whose target pays less than this for each rupee at risk is refused." },
  { key: "lot_size", label: "Lot size", hint: "lot", help: "Units in one lot, used to size an order." },
];

/** Phase V1: the risk guardian's rules, checked on every entry, paper or live, whatever built the strategy. */
const GUARDIAN_FIELDS: FieldSpec[] = [
  { key: "max_portfolio_risk_pct", label: "Risk across open positions", unit: "% of capital", hint: "risk-at-stops", step: "0.5", ceiling: "max_portfolio_risk_pct", optional: true, help: "What every open stop and the new trade could lose together. Index positions count as one group." },
  { key: "stop_cooldown_minutes", label: "Cool-down after a stop-out", unit: "minutes", hint: "cool-down", minimum: "min_stop_cooldown_minutes", optional: true, blank: " Leave it blank for no wait.", help: "After a stop, new entries in the same index or stock wait this long." },
  { key: "dd_level_1_pct", label: "Halve the size at a drawdown of", unit: "%", hint: "drawdown", step: "0.5", optional: true, blank: " Leave it blank to keep it off.", help: "This far below your equity peak, the risk per trade is halved." },
  { key: "dd_level_2_pct", label: "Pause new entries at a drawdown of", unit: "%", step: "0.5", ceiling: "dd_level_2_pct", optional: true, blank: " Leave it blank to keep it off.", help: "This far below the peak, new entries pause until your equity recovers." },
  { key: "event_size_cut_pct", label: "Size cut on event days", unit: "%", step: "5", optional: true, blank: " Leave it blank for no cut.", help: "Used by a market event that cuts the size and names no cut of its own." },
];

const ALL_FIELDS = [...LIMIT_FIELDS, ...GUARDIAN_FIELDS];
type Drafts = Partial<Record<keyof RiskConfig, string>>;

// REV-H5: a limit that is not set (null) shows as a blank field, never the text "null".
export const draftsOf = (c: RiskConfig): Drafts => Object.fromEntries(ALL_FIELDS.map((f) => [f.key, limitDraft(c[f.key] as number | null)])) as Drafts;

/** REV-H5: the limits as saved - each blank optional field "Not set" (null), every other the number typed. */
export function configFromDrafts(config: RiskConfig, drafts: Drafts, pause: LossStreakPause): RiskConfig {
  const next: RiskConfig = { ...config, loss_streak_pause: pause };
  for (const f of ALL_FIELDS) (next as unknown as Record<string, number | null>)[f.key] = limitValue(drafts[f.key] ?? "", !!f.optional);
  return next;
}

/** REV-H5: the fields' problems with these drafts (only typed text is checked; a blank optional limit is "Not set"). */
export function draftProblems(drafts: Drafts, ceilings: RiskCeilings | null): Partial<Record<keyof RiskConfig, string>> {
  const out: Partial<Record<keyof RiskConfig, string>> = {};
  for (const f of ALL_FIELDS) {
    const p = fieldProblemOf(f, drafts, ceilings);
    if (p) out[f.key] = p;
  }
  return out;
}

function fieldProblemOf(f: FieldSpec, drafts: Drafts, ceilings: RiskCeilings | null): string | null {
  const ceiling = f.ceiling && ceilings ? ceilings[f.ceiling] : undefined;
  const minimum = f.minimum && ceilings ? ceilings[f.minimum] : undefined;
  return fieldProblem(drafts[f.key] ?? "", ceiling, minimum, !!f.optional)
    ?? (f.key === "dd_level_1_pct" ? drawdownOrderProblem(drafts.dd_level_1_pct ?? "", drafts.dd_level_2_pct ?? "") : null);
}

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
  const liveHidden = useLiveHidden();                       // item 2: "paper and live" only once LIVE is in play
  const entries = entriesWord(liveHidden);
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
  // CP6-15: when the losses-in-a-row pause ends, and the pause as the risk engine counts it now
  const [pauseDraft, setPauseDraft] = useState<LossStreakPause>("next_session");
  const [streak, setStreak] = useState<LossStreakStatus | null>(null);
  const [resuming, setResuming] = useState(false);

  function refreshGuardian() {
    api.guardianStatus().then(setGuardian).catch(() => {});
    api.lossStreak().then(setStreak).catch(() => {});
    setEventsNonce((n) => n + 1);
  }

  async function resume() {
    setResuming(true);
    setNotice(null);
    try {
      setStreak(await api.resumeLossStreak());
      setNotice({ kind: "done", text: "Resumed. New entries are allowed again; only losses from now on count towards your limit." });
    } catch (err) {
      setNotice({ kind: "warn", text: failedText("New entries were not resumed.", err) });
      api.lossStreak().then(setStreak).catch(() => {});
    } finally {
      setResuming(false);
    }
  }

  useEffect(() => {
    if (!user) return;
    api.getRiskSettings().then((c) => { setConfig(c); setDrafts(draftsOf(c)); setPauseDraft(c.loss_streak_pause ?? "next_session"); }).catch(() => setLoadFailed(true));
    api.riskCeilings().then(setCeilings).catch(() => {});
    paperRiskOrNone().then(setPaper).catch(() => {});
    refreshGuardian();
  }, [user]);

  const ceilingOf = (f: FieldSpec) => (f.ceiling && ceilings ? ceilings[f.ceiling] : undefined);
  const minimumOf = (f: FieldSpec) => (f.minimum && ceilings ? ceilings[f.minimum] : undefined);
  const problemOf = (f: FieldSpec) => fieldProblemOf(f, drafts, ceilings);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!config) return;
    setTouched(Object.fromEntries(ALL_FIELDS.map((f) => [f.key, true])));
    if (ALL_FIELDS.some((f) => problemOf(f) !== null)) {
      setNotice({ kind: "warn", text: "Fix the values marked below, then save again." });
      return;
    }
    const next = configFromDrafts(config, drafts, pauseDraft);
    setSaving(true);
    setNotice(null);
    try {
      const saved = await api.updateRiskSettings(next);
      setConfig(saved);
      setDrafts(draftsOf(saved));
      setPauseDraft(saved.loss_streak_pause ?? "next_session");
      setNotice({ kind: "done", text: `Saved. ${entries.charAt(0).toUpperCase()}${entries.slice(1)} now uses these limits and guardian rules.` });
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
  const pausedNote = lossStreakNote(streak);
  const resumedText = lastResumeText(streak);
  const canEditOrg = user.role === "OWNER" || user.role === "SUPER_ADMIN";

  return (
    <div className="mx-auto w-full space-y-space-4 md:space-y-space-6" data-page="risk-v2">
      <PageIntro id="risk" lines={["Change a limit and save it."]} />
      <PageTitleV2 title="Risk limits" subtitle={`How much you can lose today, and the rules ${entries} is checked against.`} />

      <CardV2 title="Today" testId="today" tour="risk-today">
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
            {pausedNote && streak && (
              <div data-block="loss-streak" className="space-y-space-2">
                <NoteV2 kind="warn" testId="loss-streak">{pausedNote}{resumedText ? ` ${resumedText}` : ""}</NoteV2>
                {streak.can_resume && (
                  <div className="flex flex-col gap-space-2 sm:flex-row sm:items-center">
                    <button type="button" onClick={() => void resume()} disabled={resuming} data-testid="loss-streak-resume"
                      className={`${BUTTON_SECONDARY} w-full sm:w-auto`}>{resuming ? "Resuming…" : "Resume after losses in a row"}</button>
                    <p className="text-t12 text-fg-muted">Only losses from now on will count towards your limit. Your limits stay as they are.</p>
                  </div>
                )}
              </div>
            )}
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
            {/* UX-11 U1: the guardian's sentence on demand, word for word. */}
            {/* REV-H5: only the drawdown levels that are set are stated; neither set, no sentence. */}
            {guardian && drawdownSentence(guardian.settings.dd_level_1_pct, guardian.settings.dd_level_2_pct)
              ? (
                <Details id="risk.today">
                  <p className="text-t13 text-fg-muted" data-guardian-sentence>
                    {drawdownSentence(guardian.settings.dd_level_1_pct, guardian.settings.dd_level_2_pct)}
                  </p>
                </Details>
              )
              : null /* UX-11b C5: "exits are never blocked" is said once, in the page footnote */}
          </div>
        )}
      </CardV2>

      {paper && <OwnPaperLimitsV2 paper={paper} />}

      {notice && <NoteV2 kind={notice.kind} testId="save">{notice.text}</NoteV2>}

      {config && (
        <form onSubmit={save} noValidate data-block="limits"
          className="rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5">
          <h2 data-tour="risk-limits" className="text-t15 font-semibold text-fg">Your limits</h2>
          {/* UX-11b C5: the explanation on demand. */}
          <Details id="risk.limits" label="How limits work">
            <p>Checked before {entries} you place. A limit can never be set above the platform&apos;s ceiling.</p>
          </Details>
          <FieldGroup title="Size and daily limits" fields={LIMIT_FIELDS} drafts={drafts} touched={touched} ceilingOf={ceilingOf} minimumOf={minimumOf} problemOf={problemOf}
            onChange={(k, v) => setDrafts((d) => ({ ...d, [k]: v }))} onBlur={(k) => setTouched((t) => ({ ...t, [k]: true }))} />
          <PauseChoice value={pauseDraft} onChange={setPauseDraft} resumedText={pauseDraft === "until_resume" ? resumedText : null} />
          <FieldGroup title="Guardian rules" fields={GUARDIAN_FIELDS} drafts={drafts} touched={touched} ceilingOf={ceilingOf} minimumOf={minimumOf} problemOf={problemOf}
            onChange={(k, v) => setDrafts((d) => ({ ...d, [k]: v }))} onBlur={(k) => setTouched((t) => ({ ...t, [k]: true }))}
            note={liveHidden ? "The risk guardian checks these on every entry, whatever built the strategy." : "The risk guardian checks these on every entry, paper or live, whatever built the strategy."} />
          <div className="mt-space-5">
            <button type="submit" disabled={saving} data-tour="risk-save" className={`${BUTTON_PRIMARY} w-full sm:w-auto`}>{saving ? "Saving…" : "Save limits"}</button>
          </div>
        </form>
      )}

      <MarketEventsV2 user={user} defaultCut={config?.event_size_cut_pct ?? null} nonce={eventsNonce} onChanged={refreshGuardian} />
      <OrgLimitsV2 canEdit={canEditOrg} />
      <RiskChecksV2 />

      {/* UX-11b C5: said once for the page (it repeated in the intro, the guardian card and the pause choice). */}
      <PageFootnote>Exits are never blocked by these limits.</PageFootnote>
    </div>
  );
}

/** TT-12 (owner decision, 24 Oct): the trader's own paper limits, each "Not set" until he sets it himself - no platform
 * number is applied for him. Changed in Setup's risk step (empty boxes, his own numbers). */
function OwnPaperLimitsV2({ paper }: { paper: PaperRiskStatus }) {
  const values: Record<LimitField, number | undefined> = {
    riskPerTradePct: paper.settings?.risk_per_trade_pct, dailyLossPct: paper.settings?.daily_loss_pct,
    maxTradesPerDay: paper.settings?.max_trades_per_day, minRr: paper.settings?.min_rr,
  };
  const none = SETUP_LIMIT_FIELDS.every((f) => limitText(f, paper.set, values[f]) === "Off");
  return (
    <CardV2 title="Your paper limits" testId="own-paper-limits">
      <div className="space-y-space-3">
        <p className="text-t13 text-fg-muted">
          {none ? "You have not set any. Nothing stops a new paper trade until you do." : "Only the limits you set apply. Not set means no limit."}
        </p>
        <dl className="grid grid-cols-1 divide-y divide-subtle rounded-control border border-subtle sm:grid-cols-2 sm:divide-y-0">
          {SETUP_LIMIT_FIELDS.map((f) => {
            const text = limitText(f, paper.set, values[f]);
            return (
              <div key={f} className="flex items-center justify-between gap-space-3 px-space-3 py-space-2 md:px-space-4" data-limit={f}>
                <dt className="text-t14 text-fg">{RISK_LABELS[f]}</dt>
                <dd className={`text-t14 font-tabular ${text === "Off" ? "text-fg-muted" : "font-semibold text-fg"}`}>{text}</dd>
              </div>
            );
          })}
        </dl>
        <div className="flex flex-col gap-space-2 sm:flex-row sm:items-center">
          <Link to={SETUP_PATH} className={`${BUTTON_SECONDARY} w-full sm:w-auto`} data-testid="own-limits-change">
            {none ? "Set your limits" : "Change your limits"}
          </Link>
        </div>
        {/* UX-11 U1: the second line on demand, word for word. */}
        <Details id="risk.paper-limits">
          <p className="text-t13 text-fg-muted">They only stop new paper trades. Closing a trade always works.</p>
        </Details>
      </div>
    </CardV2>
  );
}

/** CP6-15: "After the losses-in-a-row limit" - when the pause ends. Saved with the other limits by "Save limits". */
function PauseChoice({ value, onChange, resumedText }: { value: LossStreakPause; onChange: (v: LossStreakPause) => void; resumedText: string | null }) {
  const name = useId();
  return (
    <fieldset className="mt-space-5" data-field="loss_streak_pause">
      <legend className="text-t14 font-semibold text-fg">After the losses-in-a-row limit</legend>
      <p className="mt-space-1 text-t13 text-fg-muted">When new entries start again once your losses in a row reach the limit above.</p>
      <div className="mt-space-3 grid grid-cols-1 gap-space-3 sm:grid-cols-2">
        {LOSS_STREAK_CHOICES.map((c) => {
          const id = `${name}-${c.value}`;
          const on = value === c.value;
          return (
            <label key={c.value} htmlFor={id} data-choice={c.value}
              className={`flex min-w-0 cursor-pointer items-start gap-space-3 rounded-control border p-space-3 transition-colors duration-fast ${on ? "border-brand bg-surface-2" : "border-border bg-surface-1 hover:bg-surface-2"}`}>
              <input id={id} type="radio" name={name} value={c.value} checked={on} onChange={() => onChange(c.value)}
                className="mt-[3px] h-4 w-4 shrink-0 accent-brand" />
              <span className="min-w-0">
                <span className="block text-t14 font-semibold text-fg">{c.label}</span>
                <span className="mt-space-1 block text-t12 text-fg-muted">{c.help}</span>
              </span>
            </label>
          );
        })}
      </div>
      {resumedText && <p className="mt-space-2 text-t12 text-fg-muted">{resumedText}</p>}
    </fieldset>
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
      <input id={id} type="number" inputMode="decimal" step={spec.step ?? "1"} min={0} value={value} placeholder={spec.optional ? "Not set" : undefined}
        onChange={(e) => onChange(e.target.value)} onBlur={onBlur}
        aria-invalid={problem ? true : undefined} aria-describedby={problem ? `${errId} ${helpId}` : helpId}
        className={`${FIELD} font-tabular ${problem ? "border-warn" : ""}`} />
      {problem && <p id={errId} role="alert" className="mt-space-1 text-t12 font-medium text-warn">{problem}</p>}
      <p id={helpId} className="mt-space-1 text-t12 text-fg-muted">
        {spec.help}{spec.optional ? spec.blank ?? " Leave it blank for no limit." : ""}{spec.zeroBlocks ? " 0 blocks every new entry." : ""}
        {typeof minimum === "number" && minimum > 0 ? ` At least ${formatNumber(minimum, Number.isInteger(minimum) ? 0 : 2)}.` : ""}
        {typeof ceiling === "number" ? ` Up to ${formatNumber(ceiling, Number.isInteger(ceiling) ? 0 : 2)}.` : ""}
      </p>
    </div>
  );
}
