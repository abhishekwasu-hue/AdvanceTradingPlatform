import { TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { BrokerCatalogEntry, BrokerConnection } from "../types";
import { oauthErrorText } from "../utils/oauthErrors";
import BrokerAdvanced from "./BrokerAdvanced";
import { ASK_OWNER, KNOWN_BROKERS, canChangeBrokers, connectionTitle, needsReconnect, shownStatus, statusText } from "./brokerState";
import { SIGN_IN_PAGE_PATH } from "./paths";
import ConnectPanel, { failureText, isPracticeOnly, type PanelMode } from "./ConnectPanel";
import { BTN_PRIMARY, BTN_SECONDARY, BrokerLogo, ResponsiveDialog, StatusDot } from "./parts";
import { useBrokerCatalog } from "./useBrokerCatalog";
import { useBrokerConnections } from "./useBrokerConnections";
import { Skeleton } from "../components/primitives";

/**
 * P2b: the Broker page - the whole screen is about connecting a broker (owner request: simple first).
 * - "Connect your broker" and a grid of broker tiles from the catalog; a tile opens the Connect panel.
 * - Your brokers, one line each: tile, name, status, "Renew" (only for a broker with a daily login), "Remove" (a
 *   neutral confirm that names what happens). Nothing else.
 * - "Advanced", collapsed: another account at the same broker, the Upstox login and redirect URL, the static IP, the
 *   broker accounts.
 * A member without the `brokers:write` permission sees the same page with the actions off and one line to ask the
 * owner (the server refuses the calls anyway).
 * No token expiry times, no credential form, no developer text here. Places no orders.
 */

/** The Upstox login comes back to the app as `/?broker=upstox&connected=1` (or `&error=<code>`); the root forwards it
 * here. The outcome, once, in plain words. */
export function loginOutcome(search: string): { ok: boolean; text: string } | null {
  const params = new URLSearchParams(search);
  if (!params.has("broker")) return null;
  const name = KNOWN_BROKERS[(params.get("broker") ?? "").toLowerCase()] ?? "Your broker";   // never echo the address
  if (params.get("connected") === "1") return { ok: true, text: `${name}: Connected ✓` };
  const error = params.get("error");
  if (error) return { ok: false, text: `${name} login did not finish: ${oauthErrorText(error)}` };
  return null;
}

export function ConnectionRow({ c, canChange = true, onRenew, onRemove, onReconnect }: {
  c: BrokerConnection; canChange?: boolean; onRenew?: () => void; onRemove?: () => void; onReconnect?: () => void;
}) {
  const status = shownStatus(c);
  const reconnect = needsReconnect(c);
  return (
    <li data-connection={c.broker} data-status={status} className="flex flex-wrap items-center gap-x-space-3 gap-y-space-2 py-space-3">
      <BrokerLogo name={c.name} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-t14 font-semibold text-fg">{connectionTitle(c)}</div>
        <div className="flex items-center gap-space-2 text-t13 text-fg-muted">
          <StatusDot dot={status} />
          <span className="truncate">{statusText(c)}{status === "connected" ? " ✓" : ""}</span>
        </div>
        {/* What is wrong, in the server's own sentence, when Renew cannot fix it. */}
        {reconnect && c.message && <p data-row-message className="mt-space-1 text-t13 text-fg-muted">{c.message}</p>}
      </div>
      {/* On a phone the buttons take a line of their own, so the name and status are never cut short. */}
      <div className="flex w-full justify-end gap-space-2 sm:w-auto">
        {c.renew && !c.data_only && status !== "not_working" && (
          <button type="button" onClick={onRenew} disabled={!canChange} className={status === "needs_login" ? BTN_PRIMARY : BTN_SECONDARY}>Renew</button>
        )}
        {reconnect && (
          <button type="button" onClick={onReconnect} disabled={!canChange} className={BTN_PRIMARY}>Connect again</button>
        )}
        <button type="button" onClick={onRemove} disabled={!canChange} className={BTN_SECONDARY}>Remove</button>
      </div>
    </li>
  );
}

export function BrokerTile({ entry, onPick, canChange = true }: { entry: BrokerCatalogEntry; onPick: () => void; canChange?: boolean }) {
  return (
    <button type="button" onClick={onPick} disabled={!entry.available || !canChange} data-broker-tile={entry.broker}
      className="flex min-h-[96px] flex-col items-center justify-center gap-space-2 rounded-panel border border-subtle bg-surface-1 p-space-3 text-t14 font-medium text-fg hover:border-border hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-60">
      <BrokerLogo name={entry.name} size="lg" />
      <span className="text-center leading-tight">{entry.name}</span>
      {!entry.available && <span className="text-t12 font-normal text-fg-muted">Coming soon</span>}
      {entry.available && isPracticeOnly(entry) && <span data-practice-only className="text-t12 font-normal text-fg-muted">Practice only</span>}
    </button>
  );
}

/** "Remove <Broker>?" - neutral (no red), and it says what happens. */
export function RemoveDialog({ c, open, onOpenChange, onConfirm, busy, error }: {
  c: BrokerConnection | null; open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void; busy: boolean; error: string | null;
}) {
  const name = c ? connectionTitle(c) : "";
  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange} title={`Remove ${name}?`} marker="remove">
      <div className="space-y-space-4">
        <p className="text-t14 text-fg-muted">
          The details saved for {name} are deleted. Live charts and prices from {c?.name ?? "this broker"} stop until you
          connect it again.
        </p>
        {error && (
          <p role="alert" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn">
            <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" /><span>{error}</span>
          </p>
        )}
        <div className="flex flex-col-reverse gap-space-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={() => onOpenChange(false)} className={BTN_SECONDARY}>Cancel</button>
          <button type="button" onClick={onConfirm} disabled={busy} aria-busy={busy || undefined}
            className="inline-flex h-11 items-center justify-center rounded-control bg-fg px-space-5 text-t14 font-semibold text-surface hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface">
            {busy ? "Removing…" : "Remove"}
          </button>
        </div>
      </div>
    </ResponsiveDialog>
  );
}

export default function BrokerPage() {
  const { user, loading } = useAuth();
  const { rows, failed, refresh } = useBrokerConnections();
  const catalog = useBrokerCatalog();
  const [panel, setPanel] = useState<{ broker: string; mode: PanelMode; label?: string } | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [removing, setRemoving] = useState<BrokerConnection | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  // Back from the Upstox login: say how it went once, then clean the address.
  useEffect(() => {
    try {
      const found = loginOutcome(window.location.search);
      if (!found) return;
      setOutcome(found);
      window.history.replaceState(window.history.state, "", window.location.pathname);
    } catch { /* URL APIs unavailable: nothing to show */ }
  }, []);

  const openPanel = (broker: string, mode: PanelMode, label?: string) => { setPanel({ broker, mode, label }); setPanelOpen(true); };

  async function confirmRemove() {
    if (!removing) return;
    setRemoveBusy(true);
    setRemoveError(null);
    try {
      await api.deleteBrokerCredentials(removing.broker, removing.account_label || "primary");
      setRemoving(null);
      refresh();
    } catch (e) {
      setRemoveError(failureText(e, removing.name, "remove"));
    } finally {
      setRemoveBusy(false);
    }
  }

  if (loading) return null;
  if (!user) {
    return (
      <section className="mx-auto max-w-xl space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-5 text-center">
        <h2 className="text-t18 font-semibold text-fg">Sign in to connect your broker</h2>
        <Link to={SIGN_IN_PAGE_PATH} className={`${BTN_PRIMARY} w-full sm:w-auto`}>Sign in</Link>
      </section>
    );
  }

  const entries = catalog.entries ?? [];
  const connected = rows ?? [];
  const canChange = canChangeBrokers(user);
  return (
    <div className="mx-auto max-w-3xl space-y-space-6" data-broker-page>
      {outcome && (
        <p role="status" data-login-outcome={outcome.ok ? "ok" : "error"}
          className={`rounded-control px-space-3 py-space-2 text-t14 ${outcome.ok ? "border border-subtle bg-surface-1 font-semibold text-fg" : "bg-warn-tint text-warn"}`}>
          {outcome.text}
        </p>
      )}

      {connected.length > 0 && (
        <section aria-labelledby="your-brokers" className="rounded-panel border border-subtle bg-surface-1 px-space-4">
          <h2 id="your-brokers" className="pt-space-4 text-t15 font-semibold text-fg">Your brokers</h2>
          <ul className="divide-y divide-border-subtle">
            {connected.map((c) => (
              <ConnectionRow key={`${c.broker}/${c.account_label}`} c={c} canChange={canChange}
                onRenew={() => openPanel(c.broker, "renew", c.account_label)}
                onReconnect={() => openPanel(c.broker, "connect", c.account_label)}
                onRemove={() => { setRemoveError(null); setRemoving(c); }} />
            ))}
          </ul>
        </section>
      )}
      {failed && rows === null && (
        <div role="alert" className="flex flex-wrap items-center gap-space-3 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn">
          <span className="flex-1">Your brokers could not be loaded.</span>
          <button type="button" onClick={refresh} className={BTN_SECONDARY}>Try again</button>
        </div>
      )}

      <section aria-labelledby="connect-broker" className="space-y-space-3">
        <div data-tour="broker-connect">
          <h2 id="connect-broker" className="text-t20 font-semibold text-fg">Connect your broker</h2>
          <p className="mt-space-1 text-t14 text-fg-muted">See live charts and start paper trading.</p>
          {!canChange && <p data-ask-owner className="mt-space-2 text-t14 text-fg">{ASK_OWNER}</p>}
        </div>
        {catalog.entries === null && !catalog.failed ? (
          <div aria-busy="true" className="grid grid-cols-2 gap-space-3 sm:grid-cols-3 md:grid-cols-4">
            <span className="sr-only">Loading brokers</span>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" panel />)}
          </div>
        ) : catalog.failed && entries.length === 0 ? (
          <div className="flex flex-wrap items-center gap-space-3 text-t14 text-fg-muted">
            <span>The list of brokers could not be loaded.</span>
            <button type="button" onClick={catalog.retry} className={BTN_SECONDARY}>Try again</button>
          </div>
        ) : (
          <div data-tour="broker-tiles" className="grid grid-cols-2 gap-space-3 sm:grid-cols-3 md:grid-cols-4">
            {entries.map((e) => <BrokerTile key={e.broker} entry={e} canChange={canChange} onPick={() => openPanel(e.broker, "connect")} />)}
          </div>
        )}
      </section>

      <BrokerAdvanced entries={entries} canChange={canChange}
        onConnectAccount={(broker, label) => openPanel(broker, "connect", label)} />

      <ConnectPanel broker={panel?.broker ?? null} mode={panel?.mode ?? "connect"} accountLabel={panel?.label}
        open={panelOpen} onOpenChange={setPanelOpen} onChanged={refresh} />
      <RemoveDialog c={removing} open={removing !== null} onOpenChange={(o) => { if (!o) setRemoving(null); }}
        onConfirm={() => void confirmRemove()} busy={removeBusy} error={removeError} />
    </div>
  );
}
