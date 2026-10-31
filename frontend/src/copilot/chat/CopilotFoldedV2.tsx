import { lazy, Suspense, type ReactNode } from "react";
import { useAuth } from "../../auth/AuthContext";
import CopilotMotion from "../components/CopilotMotion";
import FirstUseModal from "../components/FirstUseModal";
import { CopilotDataProvider } from "../data";
import { useCopilotT } from "../i18n";
import type { FoldedTab } from "../tabs";
import { TabSkeleton } from "../tabs/shared";

/**
 * M60 (approved 30 Oct): Read the chart = Market Pulse; Coach = Review + the Guide (today's Coach page, whole).
 * CP-UX CU4 (owner order 30 Oct, `ui_v2`): the older Copilot tabs folded into the one Copilot - Brief = Market Pulse +
 * News Radar, Review = Coach, Signals = Watchtower (its own entry approvals already link to the Signals page). The same
 * tab components as before (nothing rewritten, nothing removed: the seven older tabs keep their addresses under
 * "Advanced" until the owner's ✅). One disclaimer line closes the page. Nothing here places an order (ADR-0006).
 */
const MarketPulse = lazy(() => import("../tabs/MarketPulseTab"));
const NewsRadar = lazy(() => import("../tabs/NewsRadarTab"));
const Coach = lazy(() => import("../tabs/CoachTab"));
const Watchtower = lazy(() => import("../tabs/WatchtowerTab"));
// M60 default 3: the Coach tab holds today's Coach page whole - the review (Review) and the concept guide.
const Guide = lazy(() => import("../../components/GuideChat"));

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
  // M60: Read the chart is the market pulse (its news part moved to Learn › News & events).
  if (tab === "read") return <Part id="copilot-read-pulse" title={t("v2tabs.briefPulse")}><MarketPulse /></Part>;
  if (tab === "coach") {
    return (
      <>
        <Part id="copilot-coach-review" title={t("v2tabs.review")}><Coach /></Part>
        <Part id="copilot-coach-guide" title={t("v2tabs.coachGuide")}><Guide plain /></Part>
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
