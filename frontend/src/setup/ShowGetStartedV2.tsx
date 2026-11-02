import { useAuth } from "../auth/AuthContext";
import { LINK_SECONDARY } from "../dashboard/CardV2";
import { getStartedSteps, showGetStarted } from "./getStarted";
import { useSetup } from "./useSetup";

/**
 * P3: Help -> "Show Get started" - brings the Dashboard's Get started card back (src/setup/getStarted.ts showGetStarted)
 * and opens the Dashboard. In Settings → Help (both shells) and in the Help desk dialog. Nothing for a VIEWER, who does
 * not get the card. Enabled only once the server answered (never on the cached copy), so "every step done" is the
 * server's and a brought-back card is not hidden again by mistake.
 */
export default function ShowGetStartedV2({ onDone, className = "" }: { onDone?: () => void; className?: string }) {
  const { user } = useAuth();
  const setup = useSetup(user?.id ?? null);
  if (!user || user.role === "VIEWER") return null;
  const show = () => {
    const complete = !!getStartedSteps(setup.view)?.complete;
    setup.update((stored) => showGetStarted(stored, complete, new Date().toISOString()));
    onDone?.();   // M75 NAV-7: Get started shows on Today; he stays on the tab he is on (no jump)
  };
  return (
    <button type="button" onClick={show} disabled={!setup.fresh} data-action="show-get-started" className={`${LINK_SECONDARY} disabled:opacity-50 ${className}`}>
      Show Get started
    </button>
  );
}
