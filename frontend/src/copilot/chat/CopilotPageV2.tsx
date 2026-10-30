import { lazy, Suspense } from "react";
import { I18nextProvider } from "react-i18next";
import { useParams } from "react-router";
import type { PageProps } from "../../routes";
import "../copilot.css";
import { copilotI18n } from "../i18n";
import { isCopilotTab, isFoldedTab } from "../tabs";
import CopilotChatGate from "./CopilotChatV2";
import CopilotTabsV2 from "./CopilotTabsV2";

/**
 * Item 5 (owner's handover): the Copilot in the v2 shell. `/copilot` is the chat-first page. CP-UX CU4 (owner order
 * 30 Oct): ONE Copilot - a tab bar (Ask · Brief · Build · Review · Signals) over the chat and the folded older tabs
 * (`/copilot/brief`, `/copilot/review`, `/copilot/signals`; Build opens Strategies › Build). `/copilot/<old tab>` keeps
 * the existing seven-tab Copilot (Market Pulse, Strategy Lab, Coach...) - listed under "Advanced" - until the owner's ✅.
 * With `ui_v2` off the v2 shell is not used and today's Copilot renders exactly as before.
 */
const LegacyCopilot = lazy(() => import("../../pages/AiCopilotPage"));
const Folded = lazy(() => import("./CopilotFoldedV2"));

export default function CopilotPageV2(_props: PageProps = {}) {
  const { tab } = useParams<{ tab?: string }>();
  if (isCopilotTab(tab)) return <Suspense fallback={null}><LegacyCopilot /></Suspense>;
  const folded = isFoldedTab(tab) ? tab : null;
  return (
    <I18nextProvider i18n={copilotI18n}>
      <div className="space-y-space-4" data-copilot-v2-tab={folded ?? "ask"}>
        <CopilotTabsV2 active={folded ?? "ask"} />
        {folded ? <Suspense fallback={null}><Folded tab={folded} /></Suspense> : <CopilotChatGate />}
      </div>
    </I18nextProvider>
  );
}
