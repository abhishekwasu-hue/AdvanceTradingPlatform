import { useState } from "react";
import type { BrokerCatalogEntry } from "../types";
import type { BrokerPrompt as PromptState } from "./brokerState";
import ConnectPanel from "./ConnectPanel";
import { BTN_PRIMARY, BTN_SECONDARY, BrokerLogo, ResponsiveDialog } from "./parts";
import { useBrokerCatalog } from "./useBrokerCatalog";

/**
 * P2b: the prompt right after sign-in while no broker is connected (a modal on a wide screen, a sheet on a phone).
 * - none connected: "Connect your broker to see live charts and start paper trading", the broker tiles, Connect / Later;
 * - a daily login needed: "<Broker> needs today's login", Renew / Later - Renew opens the Connect panel right here.
 * Its own chunk: loaded only when it is shown.
 */

/** The prompt's words and actions - no dialog, so tests render it directly. */
export function BrokerPromptView({ state, brokers, onConnect, onRenew, onLater }: {
  state: PromptState;
  brokers: readonly Pick<BrokerCatalogEntry, "broker" | "name">[];
  onConnect?: () => void;
  onRenew?: () => void;
  onLater?: () => void;
}) {
  return (
    <div className="space-y-space-4" data-broker-prompt={state.kind}>
      {state.kind === "connect" && brokers.length > 0 && (
        <ul aria-label="Brokers you can connect" className="flex flex-wrap gap-space-2">
          {brokers.slice(0, 8).map((b) => (
            <li key={b.broker} title={b.name}><BrokerLogo name={b.name} /><span className="sr-only">{b.name}</span></li>
          ))}
        </ul>
      )}
      <div className="flex flex-col-reverse gap-space-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onLater} className={BTN_SECONDARY}>Later</button>
        {state.kind === "connect"
          ? <button type="button" onClick={onConnect} className={BTN_PRIMARY}>Connect</button>
          : <button type="button" onClick={onRenew} className={BTN_PRIMARY}>Renew</button>}
      </div>
    </div>
  );
}

export function promptTitle(state: PromptState): string {
  return state.kind === "connect"
    ? "Connect your broker to see live charts and start paper trading"
    : `${state.connection.name} needs today's login`;
}

export default function BrokerPrompt({ state, onConnect, onLater, onChanged }: {
  state: PromptState;
  /** "Connect": the shell opens the Broker page. */
  onConnect: () => void;
  /** "Later", the close button, Escape or the backdrop. */
  onLater: () => void;
  /** A renewal went through: re-read the connections. */
  onChanged: () => void;
}) {
  const { entries } = useBrokerCatalog();
  const [renewing, setRenewing] = useState(false);
  const brokers = (entries ?? []).filter((e) => e.available);
  return (
    <>
      <ResponsiveDialog open={!renewing} onOpenChange={(o) => { if (!o) onLater(); }} title={promptTitle(state)} marker="prompt">
        <BrokerPromptView state={state} brokers={brokers} onConnect={onConnect} onLater={onLater} onRenew={() => setRenewing(true)} />
      </ResponsiveDialog>
      {state.kind === "renew" && (
        <ConnectPanel broker={state.connection.broker} mode="renew" accountLabel={state.connection.account_label}
          open={renewing} onOpenChange={(o) => { if (!o) { setRenewing(false); onLater(); } }} onChanged={onChanged} />
      )}
    </>
  );
}
