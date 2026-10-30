import { lazy, Suspense } from "react";
import { I18nextProvider } from "react-i18next";
import { useAuth } from "../../auth/AuthContext";
import "../copilot.css";
import FirstUseModal from "../components/FirstUseModal";
import { CopilotDataProvider } from "../data";
import { copilotI18n, useCopilotT } from "../i18n";
import { TabSkeleton } from "../tabs/shared";

/**
 * M60 default 3 (owner order, approved 30 Oct): the news part of the old Copilot Brief (the News Radar), now shown on
 * Learn › News & events under the calendar. The same tab component as before, behind the same first-use
 * acknowledgement and with the same disclaimer line; nothing here places an order (ADR-0006).
 */
const NewsRadar = lazy(() => import("../tabs/NewsRadarTab"));

function Body() {
  const t = useCopilotT();
  return (
    <section aria-labelledby="learn-news-radar" className="space-y-space-2" data-copilot-news>
      <h2 id="learn-news-radar" className="text-t15 font-semibold text-fg">{t("v2tabs.briefNews")}</h2>
      <FirstUseModal placeholder={null}>
        <CopilotDataProvider>
          <Suspense fallback={<TabSkeleton />}><NewsRadar /></Suspense>
        </CopilotDataProvider>
      </FirstUseModal>
      <p className="text-t12 text-fg-muted" data-disclaimer>{t("chat.disclaimer")}</p>
    </section>
  );
}

export default function CopilotNewsPartV2() {
  const { user } = useAuth();
  if (!user) return null;
  return <I18nextProvider i18n={copilotI18n}><Body /></I18nextProvider>;
}
