import { ChevronRight, LogIn, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import BrokerAccountsCard from "../components/BrokerAccountsCard";
import StaticIpCard from "../components/StaticIpCard";
import UpstoxRedirectUrl from "../components/UpstoxRedirectUrl";
import type { BrokerCatalogEntry, BrokerTokenInfo } from "../types";
import { loginErrorText } from "../ui/brokerSession";
import { ASK_OWNER } from "./brokerState";
import { BTN_PRIMARY, BTN_SECONDARY } from "./parts";

/**
 * P2b: "Advanced", collapsed at the bottom of the Broker page - the technical settings moved off the main screen:
 * - "Connect another account": a second account at the same broker, under its own account name;
 * - the Upstox OAuth app: "Log in with Upstox" for an Upstox connection that has the API key and secret (the existing
 *   OAuth start), and the exact redirect URL with a copy button (the setting on Upstox's developer site);
 * - the static IP registered with each broker (SEBI);
 * - the broker accounts (default account, enable / disable, balances, the read-only check).
 * Nothing here loads until "Advanced" is opened.
 */

/** Account names the server accepts: letters, digits, dashes and underscores. */
export function validAccountName(name: string): boolean {
  return /^[A-Za-z0-9_-]{1,32}$/.test(name.trim());
}

/** "Connect another account": the broker, an account name, then the usual Connect panel for that account. */
export function AnotherAccount({ entries, canChange, onConnect }: {
  entries: readonly BrokerCatalogEntry[]; canChange: boolean; onConnect: (broker: string, label: string) => void;
}) {
  const ids = useId();
  const available = entries.filter((e) => e.available);
  const [broker, setBroker] = useState("");
  const [name, setName] = useState("");
  const picked = available.some((e) => e.broker === broker) ? broker : (available[0]?.broker ?? "");
  const ok = canChange && picked !== "" && validAccountName(name) && name.trim() !== "primary";
  const submit = (e: FormEvent) => { e.preventDefault(); if (ok) onConnect(picked, name.trim()); };
  return (
    <form onSubmit={submit} className="space-y-space-2" data-another-account>
      <p className="text-t13 text-fg-muted">A second account at the same broker gets its own name, for example &quot;family&quot;.</p>
      <div className="flex flex-wrap items-end gap-space-2">
        <div className="space-y-space-1">
          <label htmlFor={`${ids}-b`} className="block text-t13 font-medium text-fg">Broker</label>
          <select id={`${ids}-b`} value={picked} onChange={(e) => setBroker(e.target.value)} disabled={!canChange}
            className="h-11 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg">
            {available.map((e) => <option key={e.broker} value={e.broker}>{e.name}</option>)}
          </select>
        </div>
        <div className="min-w-0 flex-1 space-y-space-1">
          <label htmlFor={`${ids}-n`} className="block text-t13 font-medium text-fg">Account name</label>
          <input id={`${ids}-n`} value={name} onChange={(e) => setName(e.target.value)} disabled={!canChange} maxLength={32}
            autoComplete="off" spellCheck={false} placeholder="family"
            className="h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-[16px] text-fg md:text-t14" />
        </div>
        <button type="submit" disabled={!ok} className={BTN_SECONDARY}>Connect</button>
      </div>
      {name.trim() !== "" && !validAccountName(name) && (
        <p className="text-t13 text-warn">Use letters, digits, dashes or underscores.</p>
      )}
      {!canChange && <p className="text-t13 text-fg">{ASK_OWNER}</p>}
    </form>
  );
}

/** The Upstox OAuth app: "Log in with Upstox" for each Upstox connection that can log in this way, and the redirect
 * URL (D2's UpstoxRedirectUrl: GET /api/broker/upstox/redirect-url, the deployment's public address, with Copy). */
export function UpstoxOAuth({ canChange }: { canChange: boolean }) {
  const [rows, setRows] = useState<BrokerTokenInfo[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.brokerTokenStatus().then((r) => { if (live) setRows(Array.isArray(r) ? r : []); }).catch(() => { if (live) setRows([]); });
    return () => { live = false; };
  }, []);
  const upstox = (rows ?? []).filter((r) => r.broker_name === "upstox" && r.oauth_supported);

  async function login(t: BrokerTokenInfo) {
    const label = t.account_label ?? "primary";
    setBusy(label);
    setError(null);
    try {
      const { authorization_url } = await api.upstoxOAuthStart(label);
      window.location.href = authorization_url;                 // back on the Broker page with the outcome
    } catch (e) {
      setError(loginErrorText(e, "upstox"));
      setBusy(null);
    }
  }

  return (
    <div className="space-y-space-3">
      {upstox.length > 0 ? (
        <div className="flex flex-wrap gap-space-2">
          {upstox.map((t) => {
            const label = t.account_label ?? "primary";
            return (
              <button key={label} type="button" onClick={() => void login(t)} disabled={!canChange || busy !== null} data-upstox-oauth={label}
                className={BTN_PRIMARY}>
                <LogIn size={16} aria-hidden /> {busy === label ? "Opening Upstox…" : `Log in with Upstox${label !== "primary" ? ` (${label})` : ""}`}
              </button>
            );
          })}
        </div>
      ) : rows !== null && (
        <p className="text-t13 text-fg-muted">The OAuth login shows here for an Upstox connection with an API key and secret.</p>
      )}
      {error && (
        <p role="alert" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn">
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" /><span>{error}</span>
        </p>
      )}
      <UpstoxRedirectUrl selected />
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-space-2">
      <h3 className="text-t13 font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

export default function BrokerAdvanced({ entries = [], canChange = true, onConnectAccount }: {
  entries?: readonly BrokerCatalogEntry[];
  canChange?: boolean;
  /** "Connect another account": open the Connect panel for this broker and account name. */
  onConnectAccount?: (broker: string, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details data-broker-advanced className="rounded-panel border border-subtle bg-surface-1"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-2 px-space-4 text-t14 font-semibold text-fg-muted hover:text-fg [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} aria-hidden className={`transition-transform ${open ? "rotate-90" : ""}`} />
        Advanced
      </summary>
      {open && (
        <div className="space-y-space-5 border-t border-subtle p-space-4">
          <Section title="Connect another account">
            <AnotherAccount entries={entries} canChange={canChange} onConnect={(b, l) => onConnectAccount?.(b, l)} />
          </Section>
          <Section title="Upstox OAuth app">
            <UpstoxOAuth canChange={canChange} />
          </Section>
          <StaticIpCard />
          <BrokerAccountsCard />
        </div>
      )}
    </details>
  );
}
