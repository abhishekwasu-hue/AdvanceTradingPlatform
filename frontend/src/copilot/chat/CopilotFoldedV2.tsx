import { lazy, Suspense, type ReactNode } from "react";
import { useAuth } from "../../auth/AuthContext";
import CopilotMotion from "../components/CopilotMotion";
import FirstUseModal from "../components/FirstUseModal";
import { CopilotDataProvider } from "../data";
import { useCopilotT } from "../i18n";
import type { FoldedTab } from "../tabs";
import { TabSkeleton } from "../tabs/shared";

/**
 * CP-UX CU4 (owner order 30 Oct, `ui_v2`): the older Copilot tabs folded into the one Copilot - Brief = Market Pulse +
 * News Radar, Review = Coach, Signals = Watchtower (its own entry approvals already link to the Signals page). The same
 * tab components as before (nothing rewritten, nothing removed: the seven older tabs keep their addresses under
 * "Advanced" until the owner's ✅). One disclaimer line closes the page. Nothing here places an order (ADR-0006).
 */
const MarketPulse = lazy(() => import("../tabs/MarketPulseTab"));
const NewsRadar = lazy(() => import("../tabs/NewsRadarTab"));
const Coach = lazy(() => import("../tabs/CoachTab"));
const Watchtower = lazy(() => import("../tabs/WatchtowerTab"));

function Part({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-space-2" data-folded-part={id}>
      <h2 id={id} className="text-t15 font-semibold text-fg">{title}</h2>
      <Suspense fallback={<TabSkeleton />}>{children}</Suspense>
    </section>
  );
}

export function FoldedBody({ tab }: { tab: FoldedTab }) {
  const t = useCopilotT();
  if (tab === "brief") {
    return (
      <>
        <Part id="copilot-brief-pulse" title={t("v2tabs.briefPulse")}><MarketPulse /></Part>
        <Part id="copilot-brief-news" title={t("v2tabs.briefNews")}><NewsRadar /></Part>
      </>
    );
  }
  return <Suspense fallback={<TabSkeleton />}>{tab === "review" ? <Coach /> : <Watchtower />}</Suspense>;
}

export default function CopilotFoldedV2({ tab }: { tab: FoldedTab }) {
  const t = useCopilotT();
  const { user } = useAuth();
  if (!user) return <p className="text-t14 text-fg-muted">{t("shell.signIn")}</p>;
  return (
    <CopilotMotion>
      <div className="space-y-space-4" data-copilot-folded={tab}>
        <h1 className="sr-only">{t(`v2tabs.${tab}`)}</h1>
        {/* The first-use acknowledgement comes before anything AI-written (P0.8-D). */}
        <FirstUseModal placeholder={null}>
          <CopilotDataProvider>
            <div className="space-y-space-6"><FoldedBody tab={tab} /></div>
          </CopilotDataProvider>
        </FirstUseModal>
        <p className="text-t12 text-fg-muted" data-disclaimer>{t("chat.disclaimer")}</p>
      </div>
    </CopilotMotion>
  );
}
