import { Activity, Database, Eye, Lock, Search, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Details } from "../ui/Details";
import { CARD } from "../dashboard/CardV2";
import { startView } from "../api/viewAs";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { growthApi } from "../growth/api";
import type { Activation, GrowthFunnel, ValueFunnel } from "../growth/types";
import { useFeatures } from "../ui/useFeatures";
import {
  ago, consoleApi, maskEmail, shortDate,
  type ConsoleAiCost, type ConsoleSystem, type ConsoleUsers, type DeadLetter, type OutboxState, type RetentionState, type SupportUser,
} from "./consoleApi";
import { SkeletonRows } from "../components/primitives";

/**
 * MO3 addition (a): the new read-only parts of the admin console's sections. Each reads its own `/api/admin/console/*`
 * (or an existing admin read) once when the section opens; nothing here writes. The existing cards of each section come
 * from pages/AdminPage.tsx (`section` prop), unchanged.
 */

export const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
export const BUTTON = `inline-flex h-11 shrink-0 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-medium text-fg hover:bg-surface-2 md:h-9 ${FOCUS}`;
const TH = "py-space-1 pr-space-3 text-left font-medium text-fg-muted";
const TD = "py-space-1 pr-space-3 align-top";

const TONES = { ok: "border-up/40 bg-up/10 text-up", warn: "border-warn/40 bg-warn-tint text-warn", bad: "border-down/40 bg-down/10 text-down", off: "border-border bg-surface-2 text-fg-muted" } as const;
type Tone = keyof typeof TONES;

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span data-tone={tone} className={`inline-flex items-center rounded-pill border px-space-2 text-t12 font-medium ${TONES[tone]}`}>{children}</span>;
}

export function Panel({ title, note, children, id }: { title: string; note?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section data-console-card={id} aria-label={title} className={`${CARD} p-space-4 md:p-space-5`}>
      <h2 className="text-t15 font-semibold text-fg">{title}</h2>
      {/* PROSE-1: the card's explanation sits in its Details, closed by default. */}
      {note && <Details id={`console.${id ?? title}`}>{note}</Details>}
      <div className="mt-space-3">{children}</div>
    </section>
  );
}

function Scroll({ children }: { children: ReactNode }) {
  return <div className="-mx-space-1 overflow-x-auto px-space-1">{children}</div>;
}

function Loading({ error }: { error: string | null }) {
  return error ? <p className="text-t12 text-down">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />;
}

function useRead<T>(read: () => Promise<T>, deps: unknown[] = []): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setError(null);
    read().then((d) => { if (live) setData(d); }, (e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error };
}

const toneOf = (status: string | undefined): Tone =>
  status === "ok" || status === "paper_only" ? "ok" : status === "disabled" || status === "unknown" || status === "flag_controlled" ? "off" : status === "down" ? "bad" : "warn";

const CHECK_NAMES: Record<string, string> = { database: "Database", migrations: "Migrations", worker: "Trading worker", live_paths: "LIVE paths", redis: "Cache (Redis)" };

// --- System -----------------------------------------------------------------------------------------------------------

export function SystemPanels() {
  const { data, error } = useRead<ConsoleSystem>(consoleApi.system);
  if (!data) return <Panel title="Health" id="health"><Loading error={error} /></Panel>;
  const feed = data.data_feed;
  const quality = Object.entries(feed.data_quality_24h);
  return (
    <>
      <Panel title="Health" id="health" note={`Checked ${ago(data.checked_at)} · environment ${data.health.environment}`}>
        <div className="flex flex-wrap items-center gap-space-2">
          <span className="text-t14 font-semibold text-fg">Overall</span><Pill tone={toneOf(data.health.status)}>{data.health.status}</Pill>
        </div>
        <ul className="mt-space-3 grid gap-space-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data.health.checks).map(([name, check]) => (
            <li key={name} data-check={name} className="flex items-center justify-between gap-space-2 rounded-control border border-subtle px-space-3 py-space-2">
              <span className="text-t14 text-fg">{CHECK_NAMES[name] ?? name}</span><Pill tone={toneOf(check.status)}>{check.status.replace(/_/g, " ")}</Pill>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title={`Workers (${data.workers.length})`} id="workers" note="Each background worker's last heartbeat. A stale trading worker during market hours is the alert.">
        {data.workers.length === 0 ? <p className="text-t12 text-fg-muted">No worker has checked in yet.</p> : (
          <Scroll>
            <table className="w-full text-t12">
              <thead><tr><th className={TH}>Worker</th><th className={TH}>State</th><th className={TH}>Last seen</th><th className={TH}>Cycles</th><th className={TH}>Last error</th></tr></thead>
              <tbody>
                {data.workers.map((w) => (
                  <tr key={w.name} data-worker={w.name} className="border-t border-subtle">
                    <td className={`${TD} font-medium text-fg`}>{w.name.replace(/_/g, " ")}</td>
                    <td className={TD}><Pill tone={w.state === "ok" ? "ok" : w.state === "stale" ? "warn" : "off"}>{w.state.replace(/_/g, " ")}</Pill></td>
                    <td className={`${TD} whitespace-nowrap text-fg-muted`}>{ago(w.last_seen_at)}</td>
                    <td className={`${TD} font-tabular text-fg-muted`}>{w.cycles}{w.last_cycle_ms != null && ` · ${w.last_cycle_ms} ms`}</td>
                    <td className={`${TD} text-fg-muted`}>{w.last_error ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
        )}
      </Panel>
      <Panel title="Data feed" id="data-feed">
        <dl className="grid gap-space-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat icon={Activity} label="Market" value={feed.market_open ? "Open" : "Closed"} sub={feed.market_open ? feed.market_reason : feed.next_open ? `Opens ${new Date(feed.next_open).toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : feed.market_reason} />
          <Stat icon={Database} label="Market memory" value={ago(feed.market_memory.last_captured_at)} sub={`${feed.market_memory.snapshots_last_hour} snapshots in the last hour`} />
          <Stat icon={Database} label="Instrument master" value={`${feed.instrument_master.instruments.toLocaleString("en-IN")} instruments`} sub={`Synced ${ago(feed.instrument_master.last_synced_at)}`} />
          <Stat icon={Activity} label="Data quality (24 h)" value={quality.length === 0 ? "No events" : `${quality.reduce((a, [, n]) => a + n, 0)} events`} sub={quality.map(([k, n]) => `${k} ${n}`).join(" · ") || "gaps, spikes, late bars"} />
        </dl>
        <h3 className="mt-space-4 text-t14 font-semibold text-fg">Broker circuits</h3>
        {feed.broker_circuits.length === 0 ? <p className="mt-space-1 text-t12 text-fg-muted">No broker call since the last restart.</p> : (
          <ul className="mt-space-1 flex flex-wrap gap-space-2">
            {feed.broker_circuits.map((b) => (
              <li key={b.broker} className="flex items-center gap-space-2 text-t12 text-fg"><span className="capitalize">{b.broker}</span>
                <Pill tone={b.state === "CLOSED" ? "ok" : b.state === "OPEN" ? "bad" : "warn"}>{b.state.toLowerCase()}</Pill>
                <span className="text-fg-muted">{b.failures_in_window}/{b.calls_in_window} failed</span></li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

function Stat({ icon: Icon, label, value, sub }: { icon: typeof Activity; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-control border border-subtle p-space-3">
      <dt className="flex items-center gap-space-2 text-t12 text-fg-muted"><Icon size={14} aria-hidden />{label}</dt>
      <dd className="mt-space-1 text-t14 font-semibold text-fg">{value}</dd>
      {sub && <dd className="text-t12 text-fg-muted">{sub}</dd>}
    </div>
  );
}

// --- Controls ---------------------------------------------------------------------------------------------------------

/** Read-only: the Phase 0 posture. Nothing here turns LIVE on; the switches below are the existing ones. */
export function Phase0Notice() {
  const { on, loaded } = useFeatures();
  if (!loaded) return null;
  const paperOnly = on("phase0_paper_only");
  const live = on("live_trading");
  return (
    <section data-console-card="phase0" aria-label="Phase 0" className={`${CARD} flex items-start gap-space-3 p-space-4 md:p-space-5`}>
      <Lock size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
      <div className="min-w-0">
        <h2 className="text-t15 font-semibold text-fg">{paperOnly ? "Phase 0: PAPER only - LIVE is locked" : "Phase 0 lock is off"}</h2>
        <p className="mt-space-1 text-t12 text-fg-muted">
          Phase 0 paper only: <strong className="text-fg">{paperOnly ? "on" : "off"}</strong> · LIVE trading: <strong className="text-fg">{live ? "on" : "off"}</strong>.
        </p>
        <Details id="console.phase0" className="mt-space-1">LIVE changes only on the owner's explicit word. Exits are never blocked by any switch here.</Details>
      </div>
    </section>
  );
}

// --- Users & business -------------------------------------------------------------------------------------------------

export function UsersPanel() {
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState("");
  useEffect(() => { const t = setTimeout(() => setQuery(typed.trim()), 300); return () => clearTimeout(t); }, [typed]);
  const { data, error } = useRead<ConsoleUsers>(() => consoleApi.users(query), [query]);
  return (
    <Panel title="People" id="users" note="Everyone on the platform, newest first. E-mail addresses are partly hidden; search by the full address or the organisation.">
      {data && (
        <dl className="mb-space-3 grid grid-cols-3 gap-space-3">
          <Stat icon={Users} label="People" value={String(data.totals.users)} />
          <Stat icon={Activity} label="Active (7 days)" value={String(data.totals.active_7d)} />
          <Stat icon={Users} label="Sign-ups (7 days)" value={String(data.totals.signups_7d)} />
        </dl>
      )}
      <label className="relative mb-space-3 block max-w-sm">
        <span className="sr-only">Search people</span>
        <Search size={14} aria-hidden className="pointer-events-none absolute left-space-2 top-1/2 -translate-y-1/2 text-fg-muted" />
        <input type="search" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Search by e-mail or organisation"
          className={`h-11 w-full rounded-control border border-border bg-surface-2 pl-space-6 pr-space-2 text-t14 text-fg md:h-9 ${FOCUS}`} />
      </label>
      {!data ? <Loading error={error} /> : data.users.length === 0 ? <p className="text-t12 text-fg-muted">Nobody matches.</p> : (
        <Scroll>
          <table className="w-full text-t12">
            <thead><tr><th className={TH}>Person</th><th className={TH}>Organisation</th><th className={TH}>Plan</th><th className={TH}>Signed up</th><th className={TH}>Last seen</th><th className={TH}><span className="sr-only">Status</span></th></tr></thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id} data-user={u.id} className="border-t border-subtle">
                  <td className={TD}><div className="font-medium text-fg">{u.email}</div><div className="text-fg-muted">{u.role.replace(/_/g, " ").toLowerCase()}{u.mfa ? " · two-factor" : ""}{!u.active ? " · removed" : ""}</div></td>
                  <td className={`${TD} text-fg`}>{u.tenant}{u.tenant_status !== "active" && <span className="ml-space-1"><Pill tone="bad">{u.tenant_status}</Pill></span>}</td>
                  <td className={`${TD} text-fg`}>{u.plan}</td>
                  <td className={`${TD} whitespace-nowrap text-fg-muted`}>{shortDate(u.signed_up_at)}</td>
                  <td className={`${TD} whitespace-nowrap text-fg-muted`}>{ago(u.last_seen_at)}</td>
                  <td className={`${TD} text-right`}><Link to={`/admin?section=support&user=${u.id}`} className="whitespace-nowrap text-brand hover:underline">Status</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
    </Panel>
  );
}

/** MO4 slice 3 (`growth_v2`): who reached the first steps after signing up - read-only, from rows that already exist. */
export function ActivationPanel() {
  const [days, setDays] = useState(90);
  const { data, error } = useRead<Activation>(() => growthApi.activation(days), [days]);
  const signedUp = data?.funnel.find((f) => f.key === "signed_up")?.count ?? 0;
  const when = (iso: string | null) => (iso ? shortDate(iso) : "-");
  return (
    <Panel title="Activation" id="activation"
      note={"The first steps after signing up: a broker connected, a first paper bot and a first journal entry. Activated = all three. Platform operators are not counted."}>
      <div className="mb-space-3 flex flex-wrap items-center gap-space-2" role="group" aria-label="Signed up in the last">
        {[30, 90, 365].map((d) => (
          <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}
            className={`${BUTTON} ${days === d ? "border-brand text-brand" : ""}`}>{d} days</button>
        ))}
        {data && <span className="text-t14 text-fg-muted">Median time to activation: <strong className="text-fg">
          {data.median_hours_to_activation === null ? "-" : data.median_hours_to_activation < 48 ? `${data.median_hours_to_activation} hours` : `${Math.round(data.median_hours_to_activation / 24)} days`}</strong></span>}
      </div>
      {!data ? <Loading error={error} /> : (
        <>
          <dl className="mb-space-3 grid grid-cols-2 gap-space-3 md:grid-cols-5">
            {data.funnel.map((f) => (
              <Stat key={f.key} icon={f.key === "signed_up" ? Users : Activity} label={f.label} value={String(f.count)}
                sub={f.key === "signed_up" || signedUp === 0 ? undefined : `${Math.round((100 * f.count) / signedUp)}% of sign-ups`} />
            ))}
          </dl>
          {data.value_funnel && <ValueFunnelView funnel={data.value_funnel} />}
          {data.growth_funnel && <GrowthFunnelView funnel={data.growth_funnel} />}
          {data.people.length === 0 ? <p className="text-t12 text-fg-muted">Nobody signed up in this period.</p> : (
            <Scroll>
              <table className="w-full text-t12">
                <thead><tr><th className={TH}>Person</th><th className={TH}>Plan</th><th className={TH}>Signed up</th><th className={TH}>Broker</th><th className={TH}>Paper bot</th><th className={TH}>Journal</th><th className={TH}>Week 1</th><th className={TH}>Activated</th></tr></thead>
                <tbody>
                  {data.people.map((p) => (
                    <tr key={p.id} data-activation-user={p.id} className="border-t border-subtle">
                      <td className={TD}><div className="font-medium text-fg">{p.email}</div><div className="text-fg-muted">{p.tenant}</div></td>
                      <td className={`${TD} text-fg-muted`}>{p.plan}</td>
                      <td className={`${TD} whitespace-nowrap text-fg-muted`}>{when(p.signed_up_at)}</td>
                      <td className={`${TD} whitespace-nowrap`}>{when(p.broker_at)}</td>
                      <td className={`${TD} whitespace-nowrap`}>{when(p.paper_bot_at)}</td>
                      <td className={`${TD} whitespace-nowrap`}>{when(p.journal_at)}</td>
                      <td className={`${TD} whitespace-nowrap text-fg-muted`}>{p.active_week1 == null ? "in week 1" : p.active_week1 ? "came back" : "did not"}</td>
                      <td className={TD}>{p.activated_at ? <Pill tone="ok">{shortDate(p.activated_at)}</Pill> : <Pill tone="off">not yet</Pill>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
          )}
        </>
      )}
    </Panel>
  );
}

/** Hours as a reader says them: under two days in hours, else in days. */
export const spanText = (hours: number | null) =>
  hours === null ? "-" : hours < 1 ? `${Math.round(hours * 60)} minutes` : hours < 48 ? `${hours} hours` : `${Math.round(hours / 24)} days`;

/** AUD-G2.4: time to first value - each activation step's people, its share of the sign-ups and the median time from
 * signing up to it, then the share whose first paper trade came within their first session (the target). */
export function ValueFunnelView({ funnel }: { funnel: ValueFunnel }) {
  const first = funnel.steps[0]?.count ?? 0;
  const s = funnel.first_session;
  return (
    <section aria-label="Time to first value" data-value-funnel className="mb-space-4">
      <h3 className="text-t14 font-semibold text-fg">Time to first value</h3>
      <Details id="console.value-funnel" className="mt-space-1">
        The first time each person took each step, counted once per person. The time is the median from signing up to that step.
      </Details>
      <ol className="mt-space-3 flex flex-col gap-space-2">
        {funnel.steps.map((step, i) => {
          const ofFirst = first ? Math.round((100 * step.count) / first) : 0;
          return (
            <li key={step.key} data-value-step={step.key} className="text-t12">
              <div className="flex flex-wrap items-baseline justify-between gap-x-space-3">
                <span className="text-fg">{step.label}</span>
                <span className="whitespace-nowrap font-tabular text-fg">
                  <strong>{step.count}</strong>
                  {i > 0 && <span className="text-fg-muted"> · {ofFirst}% of sign-ups · median {spanText(step.median_hours)}</span>}
                </span>
              </div>
              <span className="mt-space-1 block h-2 rounded-pill bg-surface-2" aria-hidden><span className="block h-2 rounded-pill bg-brand" style={{ width: `${ofFirst}%` }} /></span>
            </li>
          );
        })}
      </ol>
      <p data-first-session className="mt-space-3 text-t14 text-fg">
        First paper trade within the first session ({s.hours} {s.hours === 1 ? "hour" : "hours"} after signing up):{" "}
        <strong>{s.share_pct === null ? "-" : `${s.share_pct}%`}</strong>
        <span className="text-t12 text-fg-muted"> ({s.within} of {s.decided}{s.open > 0 ? `; ${s.open} still in their first session` : ""})</span>
      </p>
    </section>
  );
}

/** Readiness C13: the growth funnel - each step's people, its share of the step before and of the sign-ups. */
export function GrowthFunnelView({ funnel }: { funnel: GrowthFunnel }) {
  const first = funnel.steps[0]?.count ?? 0;
  return (
    <section aria-label="Growth funnel" data-growth-funnel className="mb-space-4">
      <h3 className="text-t14 font-semibold text-fg">Funnel: the first week</h3>
      <Details id="console.growth-funnel" className="mt-space-1">
        People whose first week is over. Each step counts only those who also took every step before it. Active in week 1 = signed in or used the app again on a later day within seven days.
      </Details>
      {funnel.week1_open > 0 && <p className="mt-space-1 text-t12 text-fg-muted">{`${funnel.week1_open} more ${funnel.week1_open === 1 ? "person is" : "people are"} still in their first week.`}</p>}
      <ol className="mt-space-3 flex flex-col gap-space-2">
        {funnel.steps.map((step, i) => {
          const prev = i === 0 ? step.count : funnel.steps[i - 1].count;
          const ofFirst = first ? Math.round((100 * step.count) / first) : 0;
          return (
            <li key={step.key} data-funnel-step={step.key} className="text-t12">
              <div className="flex flex-wrap items-baseline justify-between gap-x-space-3">
                <span className="text-fg">{step.label}</span>
                <span className="whitespace-nowrap font-tabular text-fg">
                  <strong>{step.count}</strong>{i > 0 && <span className="text-fg-muted"> · {prev ? Math.round((100 * step.count) / prev) : 0}% of the step before</span>}
                </span>
              </div>
              <span className="mt-space-1 block h-2 rounded-pill bg-surface-2" aria-hidden><span className="block h-2 rounded-pill bg-brand" style={{ width: `${ofFirst}%` }} /></span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const usd = (n: number) => `$${n.toFixed(n >= 10 ? 2 : 4)}`;
const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const tokens = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

export function AiCostPanel() {
  const [days, setDays] = useState(30);
  const { data, error } = useRead<ConsoleAiCost>(() => consoleApi.aiCost(days), [days]);
  return (
    <Panel title="AI cost" id="ai-cost" note="From the AI call log. Own-key cost is paid by the user to the provider, not from the plan's budget. List prices per million tokens.">
      <div className="mb-space-3 flex flex-wrap items-center gap-space-2" role="group" aria-label="Period">
        {[7, 30, 90].map((d) => (
          <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}
            className={`${BUTTON} ${days === d ? "border-brand text-brand" : ""}`}>{d} days</button>
        ))}
        {data && <span className="text-t14 text-fg">Total <strong>{inr(data.total.cost_inr)}</strong> <span className="text-fg-muted">({usd(data.total.cost_usd)}, {data.total.calls} calls)</span></span>}
      </div>
      {!data ? <Loading error={error} /> : (
        <>
          <h3 className="text-t14 font-semibold text-fg">Per organisation</h3>
          {data.tenants.length === 0 ? <p className="mt-space-1 text-t12 text-fg-muted">No AI call in this period.</p> : (
            <Scroll>
              <table className="mt-space-1 w-full text-t12">
                <thead><tr><th className={TH}>Organisation</th><th className={TH}>Plan</th><th className={TH}>Calls</th><th className={TH}>Tokens in / out</th><th className={TH}>Cost</th><th className={TH}>Own key</th><th className={TH}>Failed</th></tr></thead>
                <tbody>
                  {data.tenants.map((t) => (
                    <tr key={t.tenant_id} data-ai-tenant={t.tenant_id} className="border-t border-subtle">
                      <td className={`${TD} text-fg`}>{t.tenant}</td><td className={`${TD} text-fg-muted`}>{t.plan}</td>
                      <td className={`${TD} font-tabular`}>{t.calls}</td><td className={`${TD} font-tabular`}>{tokens(t.input_tokens)} / {tokens(t.output_tokens)}</td>
                      <td className={`${TD} whitespace-nowrap font-tabular text-fg`}>{inr(t.cost_inr)} <span className="text-fg-muted">{usd(t.cost_usd)}</span></td>
                      <td className={`${TD} font-tabular text-fg-muted`}>{usd(t.own_key_cost_usd)}</td>
                      <td className={`${TD} font-tabular ${t.failed ? "text-warn" : "text-fg-muted"}`}>{t.failed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
          )}
          <h3 className="mt-space-4 text-t14 font-semibold text-fg">Per task, tier and model</h3>
          {data.tasks.length === 0 ? <p className="mt-space-1 text-t12 text-fg-muted">No AI call in this period.</p> : (
            <Scroll>
              <table className="mt-space-1 w-full text-t12">
                <thead><tr><th className={TH}>Task</th><th className={TH}>Tier</th><th className={TH}>Provider · model</th><th className={TH}>Calls</th><th className={TH}>Tokens in / out</th><th className={TH}>Price in / out</th><th className={TH}>Cost</th></tr></thead>
                <tbody>
                  {data.tasks.map((t) => (
                    <tr key={`${t.task}|${t.provider}|${t.model}`} data-ai-task={t.task} className="border-t border-subtle">
                      <td className={`${TD} text-fg`}>{t.task.replace(/_/g, " ")}</td><td className={`${TD} text-fg-muted`}>{t.tier}</td>
                      <td className={`${TD} text-fg-muted`}>{t.provider} · <span className="font-mono">{t.model || "-"}</span></td>
                      <td className={`${TD} font-tabular`}>{t.calls}{t.failed ? <span className="text-warn"> ({t.failed} failed)</span> : null}</td>
                      <td className={`${TD} font-tabular`}>{tokens(t.input_tokens)} / {tokens(t.output_tokens)}</td>
                      <td className={`${TD} whitespace-nowrap font-tabular text-fg-muted`}>{t.price ? `$${t.price.input_per_m_usd} / $${t.price.output_per_m_usd}${t.price.estimated ? " (est.)" : ""}` : "-"}</td>
                      <td className={`${TD} whitespace-nowrap font-tabular text-fg`}>{inr(t.cost_inr)} <span className="text-fg-muted">{usd(t.cost_usd)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
          )}
        </>
      )}
    </Panel>
  );
}

// --- Testing tools ----------------------------------------------------------------------------------------------------

function sumValues(o: unknown): number {
  if (typeof o === "number") return o;
  if (o && typeof o === "object") return Object.values(o as Record<string, unknown>).reduce<number>((a, v) => a + sumValues(v), 0);
  return 0;
}

function Tool({ id, name, what, read, render }: { id: string; name: string; what: ReactNode; read: () => Promise<unknown>; render: (data: never) => ReactNode }) {
  const { data, error } = useRead<unknown>(read);
  return (
    <li data-tool={id} className="rounded-control border border-subtle p-space-3">
      <div className="text-t14 font-semibold text-fg">{name}</div>
      {/* PROSE-1: `what` is the tool's Details (closed by default), written at the call site. */}
      {what}
      <div className="mt-space-2 text-t12 text-fg">{data === null ? <Loading error={error} /> : render(data as never)}</div>
    </li>
  );
}

interface BackfillSegment { by_status?: Record<string, number>; first_done?: string | null; last_done?: string | null }

const SEGMENT_WORDS: Record<string, string> = { cm: "stocks", index: "indices", fo: "F&O futures" };

/** Owner order 23 Oct: the days the shared Daily history holds, per segment ("stocks 2016-01-01 to 2026-10-22"). */
export function heldRange(segments: Record<string, BackfillSegment> | undefined): string {
  const held = Object.entries(segments ?? {}).filter(([, v]) => v.first_done && v.last_done)
    .map(([k, v]) => `${SEGMENT_WORDS[k] ?? k} ${v.first_done} to ${v.last_done}`);
  return held.length ? `Holds: ${held.join(" · ")}` : "Holds: no daily files yet";
}

const pairs = (o: Record<string, number> | undefined) => Object.entries(o ?? {}).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ") || "nothing";

export function TestingToolsPanel() {
  return (
    <Panel title="Testing and operator tools" id="testing-tools"
      note="The state of the operator tools that already exist, read-only. Replays, uploads and runs stay where they are today (the API and the runbook). Sample data and test signals move here from the user pages in a later step.">
      <ul className="grid gap-space-3 md:grid-cols-2">
        <Tool id="outbox" name="Alert outbox" what={<Details id="console.tool.outbox" className="mt-space-1">Alerts waiting to be delivered, by state.</Details>} read={consoleApi.outbox}
          render={(o: OutboxState) => <>{pairs(o.by_status)}{o.oldest_pending_seconds != null && <> · oldest waiting {Math.round(o.oldest_pending_seconds)} s</>}</>} />
        <Tool id="dead-letters" name="Failed alert deliveries" what={<Details id="console.tool.dead-letters" className="mt-space-1">Deliveries that gave up (the newest 20).</Details>} read={consoleApi.deadLetters}
          render={(rows: DeadLetter[]) => rows.length === 0 ? "None." : (
            <ul className="space-y-space-1">{rows.slice(0, 5).map((d) => <li key={d.id}>{d.channel.toLowerCase()} · {d.event_type.toLowerCase()} · {d.error_class ?? "unknown"} · {ago(d.created_at)}</li>)}
              {rows.length > 5 && <li className="text-fg-muted">+{rows.length - 5} more</li>}</ul>)} />
        <Tool id="retention" name="Data retention" what={<Details id="console.tool.retention" className="mt-space-1">Rows past the retention policy right now (a dry run) and the last run.</Details>} read={consoleApi.retention}
          render={(r: RetentionState) => <>{sumValues(r.eligible_now)} rows due · last run {r.last_run ? ago(r.last_run.at) : "never"}</>} />
        <Tool id="screener-audit" name="Saved screens check" what={<Details id="console.tool.screener-audit" className="mt-space-1">Saved screens and alert rules that no longer pass the screen language check.</Details>} read={consoleApi.screenerAudit}
          render={(a: { screens_checked?: number; alert_rules_checked?: number; failing?: unknown[] }) =>
            <>{a.screens_checked ?? 0} screens and {a.alert_rules_checked ?? 0} alert rules checked · <strong>{a.failing?.length ?? 0} failing</strong></>} />
        <Tool id="history-import" name="History import" what={<Details id="console.tool.history-import" className="mt-space-1">The stored history import, by dataset.</Details>} read={consoleApi.historyImport}
          render={(h: { datasets?: Record<string, Record<string, number>>; symbol_count?: number }) =>
            <>{Object.entries(h.datasets ?? {}).map(([k, v]) => `${k}: ${pairs(v)}`).join(" | ") || "nothing imported"}</>} />
        <Tool id="history-backfill" name="Daily file backfill" what={<Details id="console.tool.history-backfill" className="mt-space-1">The exchange daily files backfill, by segment, and the range of days the shared history holds (what backtests without a broker can use).</Details>} read={consoleApi.historyBackfill}
          render={(h: { flag_on?: boolean; segments?: Record<string, BackfillSegment>; attention?: unknown[] }) =>
            <>{h.flag_on ? "On" : "Off"} · {Object.entries(h.segments ?? {}).map(([k, v]) => `${k}: ${pairs(v.by_status)}`).join(" | ") || "no files"} · {h.attention?.length ?? 0} need attention
              <div data-lake-range>{heldRange(h.segments)}</div></>} />
        <Tool id="history-intraday" name="Intraday history" what={<Details id="console.tool.history-intraday" className="mt-space-1">The owner's intraday history load.</Details>} read={consoleApi.historyIntraday}
          render={(h: { flag_on?: boolean; instruments?: Record<string, unknown>; attention?: unknown[] }) =>
            <>{h.flag_on ? "On" : "Off"} · {Object.keys(h.instruments ?? {}).length} instruments · {h.attention?.length ?? 0} need attention</>} />
      </ul>
    </Panel>
  );
}

// --- Support ----------------------------------------------------------------------------------------------------------

export function SupportPanel({ userId, onUser }: { userId: number | null; onUser: (id: number | null) => void }) {
  const [typed, setTyped] = useState(userId ? String(userId) : "");
  useEffect(() => { setTyped(userId ? String(userId) : ""); }, [userId]);
  return (
    <>
      <Panel title="One person's status" id="support-user" note="Their account, broker connections (status only), recent sign-ins, bots with an error and failed AI calls. Never a password, key or token.">
        <form className="flex flex-wrap items-end gap-space-2" onSubmit={(e) => { e.preventDefault(); const n = parseInt(typed, 10); onUser(Number.isNaN(n) ? null : n); }}>
          <label className="flex flex-col gap-space-1 text-t12 text-fg-muted">Person number (from Users & business)
            <input inputMode="numeric" value={typed} onChange={(e) => setTyped(e.target.value.replace(/\D/g, ""))}
              className={`h-11 w-40 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg md:h-9 ${FOCUS}`} />
          </label>
          <button type="submit" className={BUTTON}>Show status</button>
        </form>
        {userId != null && <SupportUserView key={userId} id={userId} />}
      </Panel>
      <ViewsPanel />
    </>
  );
}

function SupportUserView({ id }: { id: number }) {
  const { data, error } = useRead<SupportUser>(() => consoleApi.supportUser(id), [id]);
  if (!data) return <div className="mt-space-3"><Loading error={error && /404|Unknown user/.test(error) ? "nobody with that number" : error} /></div>;
  const u = data.user;
  return (
    <div data-support-user={u.id} className="mt-space-4 grid gap-space-4 lg:grid-cols-2">
      <div>
        <h3 className="text-t14 font-semibold text-fg">{u.email}</h3>
        <p className="text-t12 text-fg-muted">{u.role.replace(/_/g, " ").toLowerCase()} · {data.tenant ? `${data.tenant.name} (${data.tenant.plan}, ${data.tenant.status})` : "no organisation"}</p>
        <ul className="mt-space-2 flex flex-wrap gap-space-2">
          <li><Pill tone={u.active ? "ok" : "off"}>{u.active ? "active" : "removed"}</Pill></li>
          <li><Pill tone={u.mfa ? "ok" : "off"}>{u.mfa ? "two-factor on" : "no two-factor"}</Pill></li>
          <li><Pill tone={u.email_verified ? "ok" : "warn"}>{u.email_verified ? "e-mail confirmed" : "e-mail not confirmed"}</Pill></li>
          {u.trading_paused && <li><Pill tone="warn">new trades paused</Pill></li>}
        </ul>
        <p className="mt-space-2 text-t12 text-fg-muted">Signed up {shortDate(u.signed_up_at)} · last seen {ago(u.last_seen_at)}</p>
        <ViewAsStart userId={u.id} role={u.role} active={u.active} />
        <h4 className="mt-space-3 text-t14 font-semibold text-fg">Broker</h4>
        {data.brokers.length === 0 ? <p className="text-t12 text-fg-muted">No broker connected.</p> : (
          <ul className="text-t12">{data.brokers.map((b) => (
            <li key={`${b.broker}-${b.account}`} className="flex flex-wrap items-center gap-space-2 py-space-1"><span className="capitalize text-fg">{b.broker}</span>
              <span className="text-fg-muted">{b.account}</span><Pill tone={b.status === "VALID" ? "ok" : "warn"}>{b.status.toLowerCase()}</Pill>
              <span className="text-fg-muted">checked {ago(b.last_verified_at)}</span></li>))}</ul>
        )}
        <h4 className="mt-space-3 text-t14 font-semibold text-fg">Alerts</h4>
        <p className="text-t12 text-fg-muted">{data.alert_deliveries_failed} failed deliveries for the organisation.</p>
      </div>
      <div>
        <h4 className="text-t14 font-semibold text-fg">Recent sign-ins</h4>
        {data.sign_ins.length === 0 ? <p className="text-t12 text-fg-muted">None recorded.</p> : (
          <ul className="text-t12">{data.sign_ins.map((s, i) => (
            <li key={i} className="flex flex-wrap gap-space-2 py-space-1"><Pill tone={s.success ? "ok" : "bad"}>{s.success ? "ok" : (s.reason ?? "failed").replace(/_/g, " ")}</Pill>
              <span className="text-fg-muted">{ago(s.at)} · {s.device ?? "unknown device"} · {s.network ?? "-"}</span></li>))}</ul>
        )}
        <h4 className="mt-space-3 text-t14 font-semibold text-fg">Bots with an error</h4>
        {data.bots_with_errors.length === 0 ? <p className="text-t12 text-fg-muted">None.</p> : (
          <ul className="text-t12">{data.bots_with_errors.map((b) => (
            <li key={b.id} className="py-space-1"><span className="text-fg">{b.strategy} · {b.symbol} · {b.mode.toLowerCase()}</span> <span className="text-fg-muted">({b.failures_in_a_row} in a row)</span>
              <div className="text-fg-muted">{b.error}</div></li>))}</ul>
        )}
        <h4 className="mt-space-3 text-t14 font-semibold text-fg">Failed AI calls (7 days)</h4>
        {data.ai_failures_7d.length === 0 ? <p className="text-t12 text-fg-muted">None.</p> : (
          <ul className="text-t12">{data.ai_failures_7d.map((a, i) => <li key={i} className="py-space-1 text-fg-muted">{ago(a.at)} · {a.task} · {a.provider} · {a.status}</li>)}</ul>
        )}
      </div>
    </div>
  );
}

/** MO3-A1: "View as this person" - a read-only view of the app as they see it, for a limited time, with a reason they will
 * see. The server refuses every change during it; the banner on top ends it. */
function ViewAsStart({ userId, role, active }: { userId: number; role: string; active: boolean }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // MO3-A1 review: starting a view needs a fresh two-factor check - the server's step-up 403 opens the code prompt.
  const [stepUp, setStepUp] = useState<string | null>(null);
  const begin = async () => {
    setBusy(true);
    setError(null);
    try {
      await startView(userId, reason.trim());
    } catch (err) {
      if (isStepUpError(err)) setStepUp(String(err));
      else setError(String(err));
      setBusy(false);
    }
  };
  if (role === "SUPER_ADMIN") return <p className="mt-space-3 text-t12 text-fg-muted" data-view-as-start="admin">An administrator's account cannot be viewed.</p>;
  if (!active) return <p className="mt-space-3 text-t12 text-fg-muted" data-view-as-start="removed">This account was removed; there is nothing to view.</p>;
  const ok = reason.trim().length >= 5;
  if (!open) {
    return (
      <button type="button" className={`${BUTTON} mt-space-3`} data-view-as-start="open" onClick={() => setOpen(true)}>
        <Eye size={16} aria-hidden />View as this person
      </button>
    );
  }
  return (
    <form className="mt-space-3 space-y-space-2 rounded-control border border-border bg-surface-2 p-space-3" data-view-as-start="form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ok || busy) return;
        await begin();
      }}>
      <Details id="console.view-as" className="mt-0">
        Opens the app as this person sees it, for a short time (the banner on top shows how long). Read only: nothing can be changed, placed, connected or
        downloaded. It is recorded on both accounts, and the person sees it and your reason in their Settings.
      </Details>
      <label className="flex flex-col gap-space-1 text-t12 text-fg-muted">Why are you opening this account? (required)
        <textarea value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} rows={2} required minLength={5} maxLength={300}
          className={`rounded-control border border-border bg-surface-1 px-space-2 py-space-1 text-t14 text-fg ${FOCUS}`} />
      </label>
      {error && <p className="text-t12 text-down" role="alert">{error}</p>}
      {stepUp && <StepUpDialog reason={stepUp} onCancel={() => setStepUp(null)} onVerified={() => { setStepUp(null); void begin(); }} />}
      <div className="flex flex-wrap gap-space-2">
        <button type="submit" disabled={!ok || busy} className={`${BUTTON} disabled:opacity-60`}>{busy ? "Opening…" : "Start read-only view"}</button>
        <button type="button" className={BUTTON} onClick={() => { setOpen(false); setReason(""); setError(null); }}>Cancel</button>
      </div>
    </form>
  );
}

function ViewsPanel() {
  const { data, error } = useRead(consoleApi.views);
  return (
    <Panel title="Recent views as a user" id="views" note="Every read-only view: whom, why, how long and how many requests (refused changes counted apart).">
      {!data ? <Loading error={error} /> : data.views.length === 0 ? <p className="text-t12 text-fg-muted">No view yet.</p> : (
        <ul className="divide-y divide-border text-t12">
          {data.views.map((v) => (
            <li key={v.id} data-view={v.id} className="flex flex-wrap items-center gap-space-2 py-space-2">
              <Pill tone={v.active ? "warn" : "off"}>{v.active ? "open" : (v.end_reason ?? "ended")}</Pill>
              <span className="text-fg">{v.name ?? "-"}</span>
              <span className="min-w-0 break-words text-fg-muted">“{v.reason}”</span>
              <span className="text-fg-muted">{ago(v.started_at)} · {v.requests} requests · {v.refused} refused</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
