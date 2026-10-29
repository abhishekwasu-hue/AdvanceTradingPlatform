import { AlertTriangle, CheckCircle2, CircleHelp, Wrench, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { LogoMark } from "../components/Logo";
import { Details } from "../ui/Details";
import {
  COMPONENT_NAMES, STATE_LABEL, STATE_TONE, checkedAt, eodLine, incidentText, marketLine, pageHeadline,
  type ComponentState, type StatusLoad,
} from "./publicStatus";
import {
  SESSION_LABEL, SESSION_TONE, headlineUptime, measuredLine, nextOpenLine, sessionDayLine, type SessionsLoad, type StatusSessions,
} from "./sessions";

/** Z10-e (G7): the public status page body. Presentational only (the page fetches), so it renders on the server in
 * tests. Every state is said in words next to its icon; colour is never the only signal. */

function StateIcon({ state, size = 16 }: { state: ComponentState; size?: number }) {
  const props = { size, "aria-hidden": true as const, className: STATE_TONE[state] };
  if (state === "operational") return <CheckCircle2 {...props} />;
  if (state === "degraded") return <AlertTriangle {...props} />;
  if (state === "outage") return <XCircle {...props} />;
  return <CircleHelp {...props} />;
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-surface text-fg">
      <main
        aria-labelledby="status-title"
        className="mx-auto max-w-2xl px-4 pb-[calc(2rem+env(safe-area-inset-bottom))] pt-[calc(1.5rem+env(safe-area-inset-top))] space-y-4"
      >
        <header className="flex items-center gap-3">
          <LogoMark size={28} />
          <h1 id="status-title" className="text-lg font-bold">AiAlgoDesk status</h1>
        </header>
        {children}
        <footer className="pt-2 text-xs text-fg-muted">
          This page refreshes every minute. PAPER trading only.{" "}
          <a href="/" className="text-brand underline-offset-2 hover:underline">Open the app</a>
        </footer>
      </main>
    </div>
  );
}

/** AUD-T8: uptime per market session (one shared line of measurements, then one row per market) and the service
 * targets. The state and this session's uptime stay open; past sessions and how it is measured live in Details. */
export function MarketsBlock({ body }: { body: StatusSessions }) {
  return (
    <>
      <section aria-labelledby="markets-title" data-markets className="rounded-xl border border-border bg-surface-1">
        <div className="p-space-4 pb-0">
          <h2 id="markets-title" className="text-t14 font-semibold">Markets</h2>
          <p className="mt-space-1 text-t13 text-fg-muted" data-measured>{measuredLine(body.measured)}</p>
        </div>
        <ul className="divide-y divide-border">
          {body.sessions.map((m) => {
            const next = nextOpenLine(m);
            return (
              <li key={m.key} className="p-space-4" data-market={m.key} data-state={m.state}>
                <div className="flex items-center justify-between gap-space-3">
                  <h3 className="text-t14 font-semibold">{m.name}</h3>
                  <span className={`text-t12 font-semibold ${SESSION_TONE[m.state]}`}>{SESSION_LABEL[m.state]}</span>
                </div>
                <p className="mt-space-1 text-t13 text-fg" data-uptime>{headlineUptime(m)}</p>
                {next && <p className="text-t12 text-fg-muted">{next}</p>}
                {m.recent.length > 1 && (
                  <Details id={`status.market.${m.key}`} label="Recent sessions">
                    <ul className="space-y-space-1">
                      {m.recent.map((s) => <li key={s.opens_at}>{sessionDayLine(s)}</li>)}
                    </ul>
                  </Details>
                )}
              </li>
            );
          })}
        </ul>
        <div className="px-space-4 pb-space-4">
          <Details id="status.uptime" label="How uptime is measured">
            <p>Uptime is the share of a session&apos;s minutes in which the trading engine had checked in within the last{" "}
              {Math.round(body.measured.heartbeat_limit_seconds / 60)} minutes and market data was on time. A minute with no
              check-in counts as down. Crypto trades around the clock and is counted per day (IST).</p>
            <p>Minutes before measuring began are not counted.</p>
          </Details>
        </div>
      </section>

      {body.slos.length > 0 && (
        <section aria-labelledby="targets-title" data-targets className="rounded-xl border border-border bg-surface-1 p-space-4">
          <h2 id="targets-title" className="text-t14 font-semibold">Service targets</h2>
          <ul className="mt-space-2 space-y-space-2 text-t13">
            {body.slos.map((t) => (
              <li key={t.id} data-slo={t.id}>{t.target}</li>
            ))}
          </ul>
          <Details id="status.targets">
            <p>These are the targets the platform is built to meet, not guarantees or past results. They are reviewed after
              the first full month of live trading.</p>
          </Details>
        </section>
      )}
    </>
  );
}

export function StatusView({ load, sessions }: { load: StatusLoad; sessions?: SessionsLoad }) {
  if (load.kind === "loading") {
    return (
      <Shell>
        <p role="status" aria-live="polite" className="rounded-xl border border-border bg-surface-1 p-4 text-sm text-fg-muted">
          Checking the platform…
        </p>
      </Shell>
    );
  }
  if (load.kind === "disabled") {
    return (
      <Shell>
        <p role="status" className="rounded-xl border border-border bg-surface-1 p-4 text-sm text-fg-muted">
          The status page is not enabled.
        </p>
      </Shell>
    );
  }
  if (load.kind === "unreachable") {
    return (
      <Shell>
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-down/40 bg-down/10 p-4 text-sm">
          <XCircle size={18} aria-hidden className="mt-0.5 shrink-0 text-down" />
          <p>Cannot reach the platform. It may be down, or your connection may be offline. This page tries again every minute.</p>
        </div>
      </Shell>
    );
  }

  const { status } = load;
  const { components, banner } = status;
  const feed = components.data_feed;
  const top = pageHeadline(status);
  return (
    <Shell>
      <section
        role="status"
        aria-live="polite"
        aria-label="Overall status"
        data-state={top.maintenance ? "maintenance" : status.status}
        className="flex items-center gap-3 rounded-xl border border-border bg-surface-1 p-4"
      >
        {top.maintenance ? <Wrench size={22} aria-hidden className="text-warn" /> : <StateIcon state={status.status} size={22} />}
        <div>
          <p className="text-base font-semibold">{top.text}</p>
          <p className="text-xs text-fg-muted">{checkedAt(status.checked_at)}</p>
        </div>
      </section>

      {banner.maintenance && (
        <div className="flex items-start gap-2 rounded-xl border border-warn/40 bg-warn/10 p-4 text-sm" data-banner="maintenance">
          <Wrench size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          <p>
            <span className="font-semibold">Planned maintenance: no new entries.</span> Exits and monitoring keep working.
            {banner.maintenance_message ? ` ${banner.maintenance_message}` : ""}
          </p>
        </div>
      )}

      {banner.incidents.length > 0 && (
        <section aria-labelledby="incidents-title" className="rounded-xl border border-border bg-surface-1 p-4">
          <h2 id="incidents-title" className="text-sm font-semibold">Current incidents</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {banner.incidents.map((incident, n) => (
              <li key={n} className="flex items-start gap-2" data-impact={incident.impact}>
                <StateIcon state={incident.impact === "major" ? "outage" : "degraded"} />
                <span>
                  {incidentText(incident)}
                  {incident.note && <span className="block text-fg-muted" data-incident-note>{incident.note}</span>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="components-title" className="rounded-xl border border-border bg-surface-1">
        <h2 id="components-title" className="sr-only">Components</h2>
        <ul className="divide-y divide-border">
          {COMPONENT_NAMES.map(({ key, name }) => {
            const c = components[key];
            return (
              <li key={key} className="p-4" data-component={key} data-state={c.state}>
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold">{name}</h3>
                  <span className={`flex items-center gap-1.5 text-xs font-semibold ${STATE_TONE[c.state]}`}>
                    <StateIcon state={c.state} size={14} />
                    {STATE_LABEL[c.state]}
                  </span>
                </div>
                <p className="mt-1 text-sm text-fg-muted">{c.summary}</p>
                {key === "data_feed" && (
                  <ul className="mt-2 space-y-0.5 text-xs text-fg-muted">
                    <li>{eodLine(feed.eod.date)}</li>
                    {/* AUD-T8: the Markets block says each market's state; not repeated here. */}
                    {sessions?.kind !== "ok" && <li>{marketLine(feed.market_open)}</li>}
                    <li>{feed.tier_a.summary}</li>
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {sessions?.kind === "ok" && <MarketsBlock body={sessions.body} />}
    </Shell>
  );
}
