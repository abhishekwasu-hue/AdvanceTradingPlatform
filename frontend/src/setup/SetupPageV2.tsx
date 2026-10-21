import { Navigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import GoLiveChecklist from "../components/GoLiveChecklist";
import type { PageProps } from "../routes";
import { SIGN_IN_PATH } from "../ui/BrokerGate";
import SetupWizardV2 from "./SetupWizardV2";

/**
 * UI2-7 (UI v2 proposal §9 rule 8, §11): Settings → Setup at `/settings/setup`, shown only while `ui_v2` is on (the v2
 * shell routes it, src/shell/pagesV2.ts). It holds the setup wizard and the go-live checklist (`GET /api/readiness`),
 * which the v2 dashboard no longer shows. With the flag off this page does not exist and the checklist stays on
 * today's dashboard, unchanged.
 */
export default function SetupPageV2({ onNavigate }: PageProps = {}) {
  const { user, loading } = useAuth();
  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;
  return (
    <div className="mx-auto w-full max-w-[760px] space-y-space-4 md:space-y-space-6" data-page="setup-v2">
      <SetupWizardV2 />
      {user && (
        <div data-block="go-live">
          <GoLiveChecklist kind="tenant" onNavigate={onNavigate} />
        </div>
      )}
    </div>
  );
}
