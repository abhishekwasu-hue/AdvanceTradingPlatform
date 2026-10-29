import { Plus } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { MarketEvent, RiskEvent, RiskLimit, RiskLimitType, RiskScope, UserResponse } from "../types";
import { Combobox } from "../ui/Combobox";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Details } from "../ui/numbersFirst";
import { formatDateIST, formatDateTimeIST } from "../ui/format";
import { strategyDisplayName } from "../ui/strategyName";
import {
  EVENT_KINDS, LIMIT_SCOPES, LIMIT_TYPES, checkStatus, eventAction, eventKindLabel, eventRemoveConsequence, eventScope, eventWindow,
  failedText, limitScopeLabel, limitTypeLabel, limitValueText,
} from "./model";
import { BUTTON_SECONDARY, FIELD, LABEL, NoteV2, StateChipV2 } from "./PartsV2";

/**
 * UI2-9a (UI v2 proposal §8, §9, §13 UI-11): the lower half of Risk limits - market events (blackouts and size cuts),
 * the organisation's limits (Phase I1's risk hierarchy) and the recent risk checks. The same endpoints and values as
 * today's cards; "Add" and "Set limit" are secondary buttons (the page's one primary action is "Save limits"), and
 * "Remove" is a neutral button whose confirm names what changes.
 */

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const todayIST = () => new Date(Date.now() + IST_OFFSET_MS).toISOString().slice(0, 10);

function Field({ label, children, className = "" }: { label: string; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={`min-w-0 ${className}`}>
      <label htmlFor={id} className={LABEL}>{label}</label>
      {children(id)}
    </div>
  );
}

// --- Market events ----------------------------------------------------------------------------------------------------

export function MarketEventsV2({ user, defaultCut, nonce, onChanged }: {
  user: UserResponse; defaultCut: number | null; nonce: number; onChanged: () => void;
}) {
  const [events, setEvents] = useState<MarketEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [date, setDate] = useState(todayIST);
  const [underlying, setUnderlying] = useState("");
  const [kind, setKind] = useState("OTHER");
  const [action, setAction] = useState<"BLOCK" | "SIZE_CUT">("SIZE_CUT");
  const [cut, setCut] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [removing, setRemoving] = useState<MarketEvent | null>(null);

  useEffect(() => {
    api.listMarketEvents().then((e) => { setEvents(e); setFailed(false); }).catch(() => setFailed(true));
  }, [nonce]);

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!date) { setNotice({ kind: "warn", text: "Choose the day of the event, then add it." }); return; }
    setBusy(true); setNotice(null);
    try {
      // Today's request, unchanged: a global event only for the platform operator writing "*" as the symbol.
      await api.createMarketEvent({
        event_date: date, underlying: underlying || null, kind, action,
        size_cut_pct: action === "SIZE_CUT" && cut ? Number(cut) : null,
        start_time: start || null, end_time: end || null, description: desc,
        global_event: user.role === "SUPER_ADMIN" && underlying.toUpperCase() === "*" ? true : false,
      });
      setDesc("");
      setNotice({ kind: "done", text: `Added: ${eventKindLabel(kind)} on ${formatDateIST(date)}, ${eventScope(underlying).replace(/^All/, "all")}.` });
      onChanged();
    } catch (err) {
      setNotice({ kind: "warn", text: failedText("The event was not added.", err) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(ev: MarketEvent) {
    setBusy(true);
    try {
      await api.deleteMarketEvent(ev.id);
      setRemoving(null);
      setNotice({ kind: "done", text: `Removed: ${eventKindLabel(ev.kind)} on ${formatDateIST(ev.event_date)}.` });
      onChanged();
    } catch (err) {
      setRemoving(null);
      setNotice({ kind: "warn", text: failedText("The event was not removed.", err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Market events" count={events?.length} testId="events">
      <Details id="risk.events" className="mb-space-3">
        <p className="text-t13 text-fg-muted">
          On a budget, policy, expiry or results day - and inside its hours, if you give them - an event either stops new entries
          or cuts the size of each trade. Leave the symbol empty for every symbol, or write INDEX for NIFTY, BANK NIFTY and SENSEX together.
        </p>
      </Details>
      <form onSubmit={add} noValidate className="mt-space-4 grid grid-cols-1 gap-space-3 sm:grid-cols-2 lg:grid-cols-4" data-event-form>
        <Field label="Day">{(id) => <input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} className={FIELD} />}</Field>
        <Field label="Symbol">{(id) => <input id={id} value={underlying} placeholder="All symbols" autoCapitalize="characters" onChange={(e) => setUnderlying(e.target.value)} className={FIELD} />}</Field>
        <Field label="Kind">{(id) => (
          <Combobox id={id} label="Kind" value={kind} onChange={setKind} options={EVENT_KINDS} />
        )}</Field>
        <Field label="What happens">{(id) => (
          <Combobox id={id} label="What happens" value={action} onChange={(v) => setAction(v as "BLOCK" | "SIZE_CUT")}
            options={[{ value: "SIZE_CUT", label: "Cut the size" }, { value: "BLOCK", label: "Stop new entries" }]} />
        )}</Field>
        {action === "SIZE_CUT" && (
          <Field label="Size cut (%)">{(id) => (
            <input id={id} type="number" min={0} max={100} inputMode="decimal" value={cut} placeholder={defaultCut === null ? "" : `${defaultCut} if empty`}
              onChange={(e) => setCut(e.target.value)} className={FIELD} />
          )}</Field>
        )}
        <Field label="From (optional)">{(id) => <input id={id} type="time" value={start} onChange={(e) => setStart(e.target.value)} className={FIELD} />}</Field>
        <Field label="To (optional)">{(id) => <input id={id} type="time" value={end} onChange={(e) => setEnd(e.target.value)} className={FIELD} />}</Field>
        <Field label="Note (optional)" className={action === "SIZE_CUT" ? "lg:col-span-1" : "lg:col-span-2"}>{(id) => (
          <input id={id} value={desc} onChange={(e) => setDesc(e.target.value)} className={FIELD} />
        )}</Field>
        <div className="flex items-end sm:col-span-2 lg:col-span-4">
          <button type="submit" disabled={busy} className={`${BUTTON_SECONDARY} w-full sm:w-auto`}><Plus size={16} aria-hidden />Add event</button>
        </div>
      </form>
      {notice && <div className="mt-space-3"><NoteV2 kind={notice.kind}>{notice.text}</NoteV2></div>}
      <div className="mt-space-4">
        {events === null && !failed ? <LoadingRowsV2 /> : failed ? <LoadFailedV2 what="your market events" /> : events && events.length === 0 ? (
          <EmptyStateV2 text="The next 30 days are clear of market events." />
        ) : (
          <ul className="divide-y divide-subtle border-t border-subtle">
            {(events ?? []).map((ev) => (
              <li key={ev.id} className="flex flex-col gap-space-2 py-space-3 sm:flex-row sm:items-center" data-event={ev.id}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-space-2">
                    <span className="text-t14 font-semibold text-fg">{formatDateIST(ev.event_date, { weekday: true })}</span>
                    <span className="text-t14 text-fg">{eventKindLabel(ev.kind)}</span>
                    <StateChipV2 tone={ev.action === "BLOCK" ? "warn" : "neutral"} check={false}>{eventAction(ev, defaultCut)}</StateChipV2>
                    {ev.global && <ChipV2>Platform</ChipV2>}
                  </div>
                  <p className="mt-px text-t13 text-fg-muted">
                    {eventScope(ev.underlying)} · {eventWindow(ev.start_time, ev.end_time)}{ev.description ? ` · ${ev.description}` : ""}
                  </p>
                </div>
                {(!ev.global || user.role === "SUPER_ADMIN") && (
                  <button type="button" onClick={() => setRemoving(ev)} className={BUTTON_SECONDARY}>Remove</button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog open={removing !== null} onOpenChange={(o) => { if (!o) setRemoving(null); }}
        title="Remove this market event?"
        consequence={removing ? eventRemoveConsequence(removing) : ""}
        confirmLabel="Remove event" busy={busy} onConfirm={() => { if (removing) void remove(removing); }} />
    </CardV2>
  );
}

// --- Organisation limits (Phase I1: the risk hierarchy) ---------------------------------------------------------------

export function OrgLimitsV2({ canEdit }: { canEdit: boolean }) {
  const [limits, setLimits] = useState<RiskLimit[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [scope, setScope] = useState<RiskScope>("TENANT");
  const [scopeId, setScopeId] = useState("");
  const [type, setType] = useState<RiskLimitType>("MAX_DAILY_LOSS");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [removing, setRemoving] = useState<RiskLimit | null>(null);

  function refresh() {
    api.listRiskLimits().then((l) => { setLimits(l); setFailed(false); }).catch(() => setFailed(true));
  }
  useEffect(refresh, []);

  const needsId = LIMIT_SCOPES.find((s) => s.value === scope)?.needsId ?? null;
  const typeSpec = LIMIT_TYPES.find((t) => t.value === type);

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!value) return;
    setBusy(true); setNotice(null);
    try {
      await api.upsertRiskLimit({ scope, scope_id: scopeId, limit_type: type, limit_value: Number(value) });
      setValue("");
      setNotice({ kind: "done", text: `Set: ${limitTypeLabel(type)} ${limitValueText(type, Number(value))} for ${limitScopeLabel(scope, scopeId).toLowerCase()}.` });
      refresh();
    } catch (err) {
      setNotice({ kind: "warn", text: failedText("The limit was not set.", err) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(l: RiskLimit) {
    setBusy(true);
    try {
      await api.deleteRiskLimit(l.id);
      setRemoving(null);
      setNotice({ kind: "done", text: `Removed: ${limitTypeLabel(l.limit_type)} for ${limitScopeLabel(l.scope, l.scope_id).toLowerCase()}.` });
      refresh();
    } catch (err) {
      setRemoving(null);
      setNotice({ kind: "warn", text: failedText("The limit was not removed.", err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Organisation limits" count={limits?.length} testId="org-limits">
      <Details id="risk.org-limits" className="mb-space-3">
        <p className="text-t13 text-fg-muted">
          Every order is checked against each limit that applies to it - the organisation, a member, a broker account, a
          strategy, a symbol - and the strictest of each kind counts. Reaching a loss limit also turns on the matching kill
          switch, so the next signal is refused. A warning comes at 80% of a limit.
        </p>
      </Details>
      {canEdit && (
        <form onSubmit={add} noValidate className="mt-space-4 grid grid-cols-1 gap-space-3 sm:grid-cols-2 lg:grid-cols-4" data-limit-form>
          <Field label="Applies to">{(id) => (
            <Combobox id={id} label="Applies to" value={scope} onChange={(v) => setScope(v as RiskScope)} options={LIMIT_SCOPES} />
          )}</Field>
          {needsId && <Field label={needsId}>{(id) => <input id={id} value={scopeId} onChange={(e) => setScopeId(e.target.value)} className={FIELD} />}</Field>}
          <Field label="Limit">{(id) => (
            <Combobox id={id} label="Limit" value={type} onChange={(v) => setType(v as RiskLimitType)} options={LIMIT_TYPES} />
          )}</Field>
          <Field label={typeSpec?.unit === "inr" ? "Value (₹)" : typeSpec?.unit === "pct" ? "Value (%)" : "Value"}>{(id) => (
            <input id={id} type="number" step="any" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className={FIELD} />
          )}</Field>
          <p className="text-t12 text-fg-muted sm:col-span-2 lg:col-span-4">{typeSpec?.help}</p>
          <div className="sm:col-span-2 lg:col-span-4">
            <button type="submit" disabled={busy || !value} className={`${BUTTON_SECONDARY} w-full sm:w-auto`}><Plus size={16} aria-hidden />Set limit</button>
          </div>
        </form>
      )}
      {notice && <div className="mt-space-3"><NoteV2 kind={notice.kind}>{notice.text}</NoteV2></div>}
      <div className="mt-space-4">
        {limits === null && !failed ? <LoadingRowsV2 /> : failed ? <LoadFailedV2 what="the organisation's limits" /> : limits && limits.length === 0 ? (
          <EmptyStateV2 text="Your own limits above are the check." />
        ) : (
          <ul className="divide-y divide-subtle border-t border-subtle">
            {(limits ?? []).map((l) => (
              <li key={l.id} className="flex flex-col gap-space-2 py-space-3 sm:flex-row sm:items-center" data-limit={l.id}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-space-2">
                    <span className="text-t14 font-semibold text-fg">{limitTypeLabel(l.limit_type)}</span>
                    <span className="font-tabular text-t14 text-fg">{limitValueText(l.limit_type, l.limit_value)}</span>
                    {!l.enabled && <ChipV2>Off</ChipV2>}
                    {l.scope === "GLOBAL" && <ChipV2>Platform</ChipV2>}
                  </div>
                  <p className="mt-px text-t13 text-fg-muted">{limitScopeLabel(l.scope, l.scope_id)}{l.note ? ` · ${l.note}` : ""}</p>
                </div>
                {canEdit && l.scope !== "GLOBAL" && (
                  <button type="button" onClick={() => setRemoving(l)} className={BUTTON_SECONDARY}>Remove</button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog open={removing !== null} onOpenChange={(o) => { if (!o) setRemoving(null); }}
        title="Remove this limit?"
        consequence={removing
          ? `Orders will no longer be checked against ${limitTypeLabel(removing.limit_type).toLowerCase()} ${limitValueText(removing.limit_type, removing.limit_value)} for ${limitScopeLabel(removing.scope, removing.scope_id).toLowerCase()}. Other limits still apply.`
          : ""}
        confirmLabel="Remove limit" busy={busy} onConfirm={() => { if (removing) void remove(removing); }} />
    </CardV2>
  );
}

// --- Recent risk checks -----------------------------------------------------------------------------------------------

const CHECKS_SHOWN = 10;

export function RiskChecksV2() {
  const [events, setEvents] = useState<RiskEvent[] | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => { api.listRiskEvents({ limit: 50 }).then(setEvents).catch(() => setEvents([])); }, []);
  const shown = all ? events ?? [] : (events ?? []).slice(0, CHECKS_SHOWN);
  return (
    <CardV2 title="Recent risk checks" count={events?.length} testId="checks">
      {events === null ? <LoadingRowsV2 /> : events.length === 0 ? (
        <EmptyStateV2 text="Each order's checks show here as it is placed." />
      ) : (
        <>
          <ul className="divide-y divide-subtle">
            {shown.map((e) => {
              const s = checkStatus(e.status);
              return (
                <li key={e.id} className="py-space-3 first:pt-0 last:pb-0" data-check={e.id}>
                  <div className="flex flex-wrap items-center justify-between gap-space-2">
                    <span className="text-t14 font-semibold text-fg">{limitTypeLabel(e.rule_type)}</span>
                    <StateChipV2 tone={s.tone}>{s.label}</StateChipV2>
                  </div>
                  <p className="mt-px text-t13 text-fg">{e.reason}</p>
                  <p className="mt-px text-t12 text-fg-muted">
                    {formatDateTimeIST(e.created_at)}{e.strategy_id ? ` · ${strategyDisplayName(e.strategy_id)}` : ""}{e.symbol ? ` · ${e.symbol}` : ""}{e.order_id ? ` · order #${e.order_id}` : ""}
                  </p>
                </li>
              );
            })}
          </ul>
          {events.length > CHECKS_SHOWN && (
            <button type="button" onClick={() => setAll((a) => !a)} className={`${BUTTON_SECONDARY} mt-space-3 w-full`}>
              {all ? "Show fewer" : `Show all ${events.length}`}
            </button>
          )}
        </>
      )}
    </CardV2>
  );
}
