import { AlertTriangle, CheckCircle2, CircleHelp, Wrench, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { LogoMark } from "../components/Logo";
import {
  COMPONENT_NAMES, STATE_LABEL, STATE_TONE, checkedAt, eodLine, headline, incidentText, marketLine,
  type ComponentState, type StatusLoad,
} from "./publicStatus";

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

export function StatusView({ load }: { load: StatusLoad }) {
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
  return (
    <Shell>
      <section
        role="status"
        aria-live="polite"
        aria-label="Overall status"
        data-state={status.status}
        className="flex items-center gap-3 rounded-xl border border-border bg-surface-1 p-4"
      >
        <StateIcon state={status.status} size={22} />
        <div>
          <p className="text-base font-semibold">{headline(status.status)}</p>
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
                <span>{incidentText(incident)}</span>
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
                    <li>{marketLine(feed.market_open)}</li>
                    <li>{feed.tier_a.summary}</li>
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </Shell>
  );
}
