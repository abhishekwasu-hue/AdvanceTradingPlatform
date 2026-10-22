import { ChevronRight } from "lucide-react";
import { Link, Navigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import type { PageProps } from "../routes";
import { SIGN_IN_PATH } from "../ui/BrokerGate";
import { GO_LIVE_PATH_V2 } from "../settings/paths";
import { useLiveHidden } from "../settings/useLiveHidden";
import SetupWizardV2 from "./SetupWizardV2";

/**
 * UI2-7 (UI v2 proposal §9 rule 8, §11): Settings → Setup at `/settings/setup`, shown only while `ui_v2` is on (the v2
 * shell routes it, src/shell/pagesV2.ts). It holds the setup wizard. P3: the go-live checklist moved to Settings →
 * Advanced in both shells; this page links to it ("Prepare for LIVE"). Nothing on the dashboard opens this page by
 * itself any more.
 */
export default function SetupPageV2(_props: PageProps = {}) {
  const { user, loading } = useAuth();
  // Settings redesign B3: "Prepare for LIVE" only once Phase 0 (PAPER only) is over.
  const liveHidden = useLiveHidden();
  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;
  return (
    <div className="mx-auto w-full max-w-[760px] space-y-space-4 md:space-y-space-6" data-page="setup-v2">
      <SetupWizardV2 />
      {user && !liveHidden && (
        <Link to={GO_LIVE_PATH_V2} data-block="prepare-live"
          className="inline-flex min-h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          Prepare for LIVE <ChevronRight size={16} aria-hidden />
        </Link>
      )}
    </div>
  );
}
