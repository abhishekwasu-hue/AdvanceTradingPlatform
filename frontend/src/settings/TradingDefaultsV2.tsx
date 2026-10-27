import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Link, Navigate } from "react-router";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2, PageTitleV2 } from "../trading/PartsV2";
import { DEBIT_STRUCTURES, WIDTH_STRUCTURES, WINGED_STRUCTURES } from "../types";
import { SIGN_IN_PATH } from "../ui/BrokerGate";
import { useFeatures } from "../ui/useFeatures";
import ChainSettingsV2 from "./chainSettings";
import SmartExecutionSettingsV2 from "./smartExecutionSettings";
import {
  EMPTY_DEFAULTS, EXPIRIES, KINDS, POSITIONS, RISK_LIMITS_PATH, STRIKES, STRUCTURES, TICKET_ORDER_TYPES, TICKET_PRODUCTS,
  defaultsOf, formOf, hasAnyDefault, loadDefaults, saveDefaults, type DefaultsForm,
} from "./tradingDefaults";
import { LIMITS_OFF, limitsFormOf, limitsOf, loadLimits, saveLimits, type LimitsForm } from "./liquidityLimits";

/**
 * Settings redesign part C: Settings → My trading defaults at `/settings/trading-defaults`, shown only while `ui_v2` is on
 * (the v2 shell routes it, src/shell/pagesV2.ts). The user's own starting values for a new paper bot, the full form
 * ("More ways to trade") and the chain order ticket. A pre-fill, never a hidden rule: each form shows "from your
 * defaults" next to a value it took, and the value can be changed there. Nothing trades from this page and the server
 * never reads these values to trade. Only existing fields (review R7); no mode, broker routing or LIVE field. Risk limits
 * are a link to their own page, not a copy.
 * FNO-8B-28: "My own liquidity limits" (namespace `fno`, src/settings/liquidityLimits.ts) - OFF by default; when on, the
 * F&O confirm step's liquidity line also says whether the strike is inside them. Information only; saved with the same
 * "Save my defaults" button and cleared by "Clear all".
 */
export default function TradingDefaultsV2(_props: PageProps = {}) {
  const { user, loading } = useAuth();
  const features = useFeatures();
  const [form, setForm] = useState<DefaultsForm | null>(null);
  const [version, setVersion] = useState(0);
  const [limits, setLimits] = useState<LimitsForm>(limitsFormOf(LIMITS_OFF));
  const [limitsVersion, setLimitsVersion] = useState(0);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, l] = await Promise.all([loadDefaults(), loadLimits()]);
      setForm(formOf(r.value)); setVersion(r.version); setFailed(false);
      setLimits(limitsFormOf(l.value)); setLimitsVersion(l.version);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => { if (user) void load(); }, [user, load]);

  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;

  const approvalAllowed = features.on("approval_only_entries");

  async function save(next: DefaultsForm, nextLimits: LimitsForm, cleared = false) {
    const { value, problems } = defaultsOf(next);
    const own = limitsOf(nextLimits);
    const first = problems[0] ?? own.problems[0];
    if (first) { setNote({ kind: "warn", text: first }); return; }
    setBusy(true); setNote(null);
    try {
      const r = await saveDefaults(value, version);
      setForm(formOf(r.value)); setVersion(r.version);
      const l = await saveLimits(own.value, limitsVersion);
      setLimits(limitsFormOf(l.value)); setLimitsVersion(l.version);
      const limitsWord = l.value.on ? " Your own liquidity limits are on." : "";
      setNote({ kind: "done", text: cleared ? "Your defaults are cleared. New forms start with the usual settings."
        : hasAnyDefault(r.value) ? `Saved. New forms start with these values, marked "from your defaults".${limitsWord}`
        : l.value.on ? "Saved. Your own liquidity limits are on; new forms start with the usual settings."
        : "Saved. Nothing is set, so new forms start with the usual settings." });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await load();
        setNote({ kind: "warn", text: "Your defaults were changed on another device. The latest are shown; make your change again." });
      } else {
        setNote({ kind: "warn", text: "Your defaults could not be saved just now. Try again in a moment." });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-[760px] space-y-space-4" data-page="trading-defaults-v2">
      <PageTitleV2 title="My trading defaults"
        subtitle="Your own starting values for a new paper bot and a chain order. They only fill in the form: you see each one marked there and can change it." />
      {failed && !form ? <CardV2 title="My trading defaults"><LoadFailedV2 what="your defaults" /></CardV2>
        : !form ? <CardV2 title="My trading defaults"><LoadingRowsV2 rows={4} /></CardV2>
        : <DefaultsFormV2 form={form} onChange={(f) => { setForm(f); setNote(null); }} approvalAllowed={approvalAllowed} busy={busy} note={note}
            limits={limits} onLimits={(f) => { setLimits(f); setNote(null); }}
            onSave={() => void save(form, limits)} onClear={() => void save(formOf(EMPTY_DEFAULTS), limitsFormOf(LIMITS_OFF), true)} />}
      {/* TM Part 7 (flag smart_execution): his own numbers for multi-leg orders and the delivery warning. */}
      {features.on("smart_execution") && <SmartExecutionSettingsV2 />}
      {/* B4: his own Option chain settings - the PCR zone edges of "What the chain shows". */}
      <ChainSettingsV2 />
    </div>
  );
}

function Field({ label, hint, children, id }: { label: string; hint?: string; children: ReactNode; id: string }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className={LABEL}>{label}</label>
      {children}
      {hint && <p className="mt-space-1 text-t12 text-fg-muted">{hint}</p>}
    </div>
  );
}

function Choice<T extends string>({ id, value, options, onChange, notSet = "Not set" }: {
  id: string; value: T | ""; options: { value: T; label: string }[]; onChange: (v: T | "") => void; notSet?: string;
}) {
  return (
    <select id={id} className={FIELD} value={value} onChange={(e) => onChange(e.target.value as T | "")}>
      <option value="">{notSet}</option>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function NumberBox({ id, value, onChange, placeholder = "Not set", decimal = false }: {
  id: string; value: string; onChange: (v: string) => void; placeholder?: string; decimal?: boolean;
}) {
  return <input id={id} className={FIELD} value={value} inputMode={decimal ? "decimal" : "numeric"} autoComplete="off" placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)} />;
}

/** The page's form (no data loading: tested on its own). */
export function DefaultsFormV2({ form, onChange, approvalAllowed, busy, note, onSave, onClear, limits, onLimits }: {
  form: DefaultsForm; onChange: (f: DefaultsForm) => void; approvalAllowed: boolean; busy: boolean;
  note: { kind: "done" | "warn"; text: string } | null; onSave: () => void; onClear: () => void;
  /** FNO-8B-28: his own liquidity limits (absent: the card is not shown). */
  limits?: LimitsForm; onLimits?: (f: LimitsForm) => void;
}) {
  const base = useId();
  const id = (k: string) => `${base}-${k}`;
  const set = <K extends keyof DefaultsForm>(k: K, v: DefaultsForm[K]) => onChange({ ...form, [k]: v });
  const kind = form.instrument_kind;
  const options = kind === "OPTION" || kind === "";
  const structure = form.option_strategy && form.option_strategy !== "SINGLE" ? form.option_strategy : null;
  const debit = !!structure && DEBIT_STRUCTURES.includes(structure);
  const widthLabel = structure && (WINGED_STRUCTURES.includes(structure) || structure === "LONG_BUTTERFLY") ? "Wing width (strike steps)" : "Short strike distance (strike steps)";
  const grid = "grid grid-cols-1 gap-space-3 sm:grid-cols-2";

  return (
    <div className="space-y-space-4">
      <CardV2 title="What a new bot trades" testId="what">
        <div className={grid}>
          <Field id={id("kind")} label="Trade as" hint="On an index the bot trades its options unless you pick futures.">
            <Choice id={id("kind")} value={kind} options={KINDS} onChange={(v) => set("instrument_kind", v)} />
          </Field>
          {options && (
            <Field id={id("structure")} label="Structure">
              <Choice id={id("structure")} value={form.option_strategy} options={STRUCTURES} onChange={(v) => set("option_strategy", v)} />
            </Field>
          )}
          {options && !structure && (
            <Field id={id("position")} label="Option position">
              <Choice id={id("position")} value={form.option_position} options={POSITIONS} onChange={(v) => set("option_position", v)} />
            </Field>
          )}
          {options && (
            <Field id={id("strike")} label="Strike">
              <Choice id={id("strike")} value={form.strike_rule} options={STRIKES} onChange={(v) => set("strike_rule", v)} />
            </Field>
          )}
          {options && (form.strike_rule === "ITM" || form.strike_rule === "OTM") && (
            <Field id={id("offset")} label="Strike steps from the money" hint="1 to 10. Empty means 1.">
              <NumberBox id={id("offset")} value={form.strike_offset} onChange={(v) => set("strike_offset", v)} />
            </Field>
          )}
          {kind !== "UNDERLYING" && (
            <Field id={id("expiry")} label="Expiry">
              <Choice id={id("expiry")} value={form.expiry_rule} options={EXPIRIES} onChange={(v) => set("expiry_rule", v)} />
            </Field>
          )}
          {kind !== "UNDERLYING" && (
            <Field id={id("lots")} label="Lots for a new bot" hint="1 to 500. Your risk limits still apply.">
              <NumberBox id={id("lots")} value={form.max_lots} onChange={(v) => set("max_lots", v)} />
            </Field>
          )}
          {options && !structure && (
            <Field id={id("premium")} label={form.option_position === "WRITE" ? "Premium ceiling %" : "Premium stop %"} hint="5 to 95.">
              <NumberBox id={id("premium")} value={form.premium_stop_pct} onChange={(v) => set("premium_stop_pct", v)} decimal />
            </Field>
          )}
          {options && structure && WIDTH_STRUCTURES.includes(structure) && (
            <Field id={id("width")} label={widthLabel} hint="1 to 20.">
              <NumberBox id={id("width")} value={form.spread_width} onChange={(v) => set("spread_width", v)} />
            </Field>
          )}
          {options && structure && (
            <Field id={id("target")} label={`Target % of the ${debit ? "debit" : "credit"}`} hint="5 to 95.">
              <NumberBox id={id("target")} value={form.target_credit_pct} onChange={(v) => set("target_credit_pct", v)} decimal />
            </Field>
          )}
          {options && structure && (
            <Field id={id("stop")} label={`Stop % of the ${debit ? "debit" : "credit"}`} hint={debit ? "10 to 100." : "10 to 500."}>
              <NumberBox id={id("stop")} value={form.stop_credit_pct} onChange={(v) => set("stop_credit_pct", v)} decimal />
            </Field>
          )}
        </div>
      </CardV2>

      <CardV2 title="Exits" testId="exits">
        <p className="mb-space-3 text-t14 text-fg-muted">Optional. Your strategy's own exits still apply.</p>
        <div className={grid}>
          <Field id={id("trail")} label="Trailing stop %" hint="Above 0, up to 50.">
            <NumberBox id={id("trail")} value={form.trailing_stop_pct} onChange={(v) => set("trailing_stop_pct", v)} decimal />
          </Field>
          <Field id={id("be")} label="Move to break-even at R" hint="Above 0, up to 10.">
            <NumberBox id={id("be")} value={form.break_even_at_r} onChange={(v) => set("break_even_at_r", v)} decimal />
          </Field>
          <Field id={id("minutes")} label="Time exit after (minutes)" hint="Empty unless you set one.">
            <NumberBox id={id("minutes")} value={form.time_exit_minutes} onChange={(v) => set("time_exit_minutes", v)} />
          </Field>
          <Field id={id("at")} label="Flat at (HH:MM)" hint="Empty unless you set one.">
            <input id={id("at")} className={FIELD} value={form.time_exit_at} placeholder="Not set" autoComplete="off" inputMode="numeric"
              onChange={(e) => set("time_exit_at", e.target.value)} />
          </Field>
        </div>
      </CardV2>

      {approvalAllowed && (
        <CardV2 title="Entries" testId="entries">
          <label className="flex min-h-11 cursor-pointer items-start gap-space-2 text-t14 text-fg">
            <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand" checked={form.approval} onChange={(e) => set("approval", e.target.checked)} />
            <span>A new bot keeps asking for my Approve before each entry, even if approval-only is turned off later.</span>
          </label>
        </CardV2>
      )}

      <CardV2 title="Expiry day" testId="expiry-day">
        <label className="flex min-h-11 cursor-pointer items-start gap-space-2 text-t14 text-fg">
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand" data-expiry-day-next checked={form.expiry_day_next}
            onChange={(e) => set("expiry_day_next", e.target.checked)} />
          <span>On an expiry day, open the option chain and the spread builder on the next expiry. Today&apos;s expiry stays one tap away.</span>
        </label>
      </CardV2>

      <CardV2 title="Chain order ticket" testId="ticket">
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-3">
          <Field id={id("tlots")} label="Lots" hint="1 to 500.">
            <NumberBox id={id("tlots")} value={form.ticket_lots} onChange={(v) => set("ticket_lots", v)} />
          </Field>
          <Field id={id("product")} label="Product">
            <Choice id={id("product")} value={form.ticket_product} options={TICKET_PRODUCTS} onChange={(v) => set("ticket_product", v)} />
          </Field>
          <Field id={id("otype")} label="Order type">
            <Choice id={id("otype")} value={form.ticket_order_type} options={TICKET_ORDER_TYPES} onChange={(v) => set("ticket_order_type", v)} />
          </Field>
        </div>
      </CardV2>

      {limits && onLimits && (
        <CardV2 title="My own liquidity limits (F&O)" testId="liquidity">
          <p className="mb-space-3 text-t14 text-fg-muted">
            Before you place an F&amp;O order, its strike is compared with the other strikes on the chain. You can also add your own limits;
            the check then says whether the strike is inside them. Information only: it never holds back an order or an exit.
          </p>
          <label className="flex min-h-11 cursor-pointer items-start gap-space-2 text-t14 text-fg">
            <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand" checked={limits.on} data-limits="on"
              onChange={(e) => onLimits({ ...limits, on: e.target.checked })} />
            <span>Use my own limits too</span>
          </label>
          {limits.on && (
            <div className={`${grid} mt-space-3`}>
              <Field id={id("spread")} label="Max bid-ask gap %" hint="Of the price. Above 0, up to 100.">
                <NumberBox id={id("spread")} value={limits.max_spread_pct} onChange={(v) => onLimits({ ...limits, max_spread_pct: v })} decimal />
              </Field>
              <Field id={id("minoi")} label="Min open interest" hint="A whole number. Empty means not checked.">
                <NumberBox id={id("minoi")} value={limits.min_oi} onChange={(v) => onLimits({ ...limits, min_oi: v })} />
              </Field>
            </div>
          )}
        </CardV2>
      )}

      <CardV2 title="Risk limits" testId="risk">
        <p className="text-t14 text-fg-muted">Your daily loss limit and the most open positions live on their own page and apply to every trade.</p>
        <Link to={RISK_LIMITS_PATH}
          className="mt-space-2 inline-flex min-h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-brand hover:underline md:min-h-0">
          Open Risk limits <ChevronRight size={16} aria-hidden />
        </Link>
      </CardV2>

      {note && <NoteV2 kind={note.kind} testId="defaults-note">{note.text}</NoteV2>}
      <div className="flex flex-col gap-space-2 sm:flex-row">
        <button type="button" className={`${BUTTON_PRIMARY} w-full sm:w-auto`} disabled={busy} aria-busy={busy || undefined} onClick={onSave} data-action="save">
          Save my defaults
        </button>
        <button type="button" className={`${BUTTON_SECONDARY} w-full sm:w-auto`} disabled={busy} onClick={onClear} data-action="clear">
          Clear all
        </button>
      </div>
    </div>
  );
}
