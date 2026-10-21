import { Suspense, lazy, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import type { BrokerConnection } from "../types";
import { promptFor, type BrokerPrompt } from "./brokerState";
import { dismissForThisSignIn, dismissedThisSignIn, takeNewAccount } from "./signIn";
import { useBrokerConnections } from "./useBrokerConnections";

/** The Broker page's address in both shells. */
export const BROKER_PATH = "/broker";

const BrokerPromptDialog = lazy(() => import("./BrokerPrompt"));

/** True on the Broker page (`/broker`, `/broker/`). */
export function isBrokerPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === BROKER_PATH;
}

/** The prompt this user should see now, or null: signed in, not on the Broker page, "Later" not chosen during this
 * sign-in, and no broker connected (see promptFor). */
export function promptToShow(userId: number | null, pathname: string, rows: readonly BrokerConnection[] | null): BrokerPrompt | null {
  if (userId === null || isBrokerPath(pathname) || dismissedThisSignIn(userId)) return null;
  return promptFor(rows);
}

/**
 * P2b: decides when the sign-in prompt shows, in both shells.
 * - A brand-new account opens the Broker page first (no prompt on top of it).
 * - Otherwise, while no broker is connected (the connections are known), the prompt shows once per sign-in: "Later"
 *   (or closing it) hides it until the next sign-in; "Connect" opens the Broker page. Never on the Broker page itself.
 * The prompt stays as it was shown until it is closed, so a renewal finished inside it can say "Connected ✓".
 */
export default function BrokerPromptHost() {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const { rows, refresh } = useBrokerConnections();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [shown, setShown] = useState<BrokerPrompt | null>(null);
  const onBrokerPage = isBrokerPath(pathname);

  // A new account: straight to the Broker page; that counts as this sign-in's prompt.
  useEffect(() => {
    if (userId === null || !takeNewAccount(userId)) return;
    dismissForThisSignIn(userId);
    if (!onBrokerPage) navigate(BROKER_PATH);
  }, [userId]);                                            // once per user (the flag is taken once)

  const wanted = promptToShow(userId, pathname, rows);
  useEffect(() => {
    if (userId === null) { setShown(null); return; }
    // Checked again here: the new-account step above may have just used this sign-in's prompt.
    if (!shown && wanted && !dismissedThisSignIn(userId)) setShown(wanted);
  }, [userId, wanted?.kind, shown]);

  if (!shown || userId === null) return null;
  const close = () => { dismissForThisSignIn(userId); setShown(null); };
  return (
    <Suspense fallback={null}>
      <BrokerPromptDialog state={shown} onLater={close} onChanged={refresh}
        onConnect={() => { close(); navigate(BROKER_PATH); }} />
    </Suspense>
  );
}
