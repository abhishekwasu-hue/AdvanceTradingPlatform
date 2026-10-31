import { lazy, Suspense } from "react";
import { useFeatures } from "../ui/useFeatures";

/**
 * M60 (owner order, approved 30 Oct): Learn › News & events at `/learn/news` (the old /news-events forwards here) -
 * today's News & events page as it is, and below it (default 3) the news part of the old Copilot Brief while the AI
 * Copilot is on. Nothing is rebuilt: both are the existing components.
 */
const NewsEvents = lazy(() => import("../pages/NewsEventsPage"));
const CopilotNews = lazy(() => import("../copilot/chat/CopilotNewsPartV2"));

export default function LearnNewsV2() {
  const copilotOn = useFeatures().on("ai_copilot");
  return (
    <div className="space-y-space-6" data-page="learn-news">
      <Suspense fallback={null}><NewsEvents /></Suspense>
      {copilotOn && <Suspense fallback={null}><CopilotNews /></Suspense>}
    </div>
  );
}
