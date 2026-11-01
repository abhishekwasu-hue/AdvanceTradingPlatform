import { lazy, Suspense, useMemo } from "react";
import { I18nextProvider } from "react-i18next";
import { useParams, useSearchParams } from "react-router";
import type { PageProps } from "../../routes";
import "../copilot.css";
import { askContextFrom, type AskContext } from "../askContext";
import { copilotI18n } from "../i18n";
import { ANALYZE_TAB, isCopilotTab, isFoldedTab, v2TabOf } from "../tabs";
import CopilotChatGate from "./CopilotChatV2";
import CopilotTabsV2 from "./CopilotTabsV2";

/**
 * Item 5 (owner's handover): the Copilot in the v2 shell. `/copilot` is the chat-first page. CP-UX CU4 (owner order
 * 30 Oct) + M60 (owner order, approved 30 Oct): ONE Copilot - a tab bar (Ask · Read the chart · Analyze a chart · Coach)
 * over the chat, the folded older tabs (`/copilot/read`, `/copilot/coach`) and the Analyze a chart tool
 * (`/copilot/analyze`, the same page as /chart-analyze). `/copilot/<old tab>` keeps the existing seven-tab Copilot
 * (Market Pulse, Strategy Lab...) - listed under "Advanced" - except /copilot/coach, which is now the Coach tab.
 * With `ui_v2` off the v2 shell is not used and today's Copilot renders exactly as before.
 */
const LegacyCopilot = lazy(() => import("../../pages/AiCopilotPage"));
const Folded = lazy(() => import("./CopilotFoldedV2"));
const ChartAnalyze = lazy(() => import("../../chartAnalyze/ChartAnalyzePageV2"));

const LegacyTool = lazy(() => import("../../pages/AiCopilotPage").then((m) => ({ default: m.CopilotToolBody })));

/** What a Copilot address shows under the tab bar: the chat, a folded tab or Analyze a chart (`inPanel`, M75 NAV-2:
 * the in-place Copilot panel also shows one of the older tools, without the older page's hero and tab bar). */
export function CopilotBody({ tab, inPanel = false, context = null }: { tab?: string; inPanel?: boolean; context?: AskContext | null }) {
  const folded = isFoldedTab(tab) ? tab : null;
  if (tab === ANALYZE_TAB) return <Suspense fallback={null}><ChartAnalyze /></Suspense>;
  if (folded) return <Suspense fallback={null}><Folded tab={folded} /></Suspense>;
  if (inPanel && isCopilotTab(tab)) return <Suspense fallback={null}><LegacyTool tab={tab} /></Suspense>;
  return <CopilotChatGate context={context} />;
}

export default function CopilotPageV2(_props: PageProps = {}) {
  const { tab } = useParams<{ tab?: string }>();
  // M72 PR-3 (F8): the address's context (`?symbol=`, `tf`, `ctx`, `from`) - read here, before it was dropped
  const [search] = useSearchParams();
  const key = search.toString();
  const context = useMemo(() => askContextFrom((k) => new URLSearchParams(key).get(k)), [key]);
  const folded = isFoldedTab(tab) ? tab : null;
  const analyze = tab === ANALYZE_TAB;
  if (!folded && !analyze && isCopilotTab(tab)) return <Suspense fallback={null}><LegacyCopilot /></Suspense>;
  const active = analyze ? v2TabOf(ANALYZE_TAB) : v2TabOf(folded);
  return (
    <I18nextProvider i18n={copilotI18n}>
      <div className="space-y-space-4" data-copilot-v2-tab={active}>
        <CopilotTabsV2 active={active} />
        <CopilotBody tab={tab} context={context} />
      </div>
    </I18nextProvider>
  );
}
