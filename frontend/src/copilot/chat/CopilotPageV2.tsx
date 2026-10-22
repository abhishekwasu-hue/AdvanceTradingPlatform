import { lazy, Suspense } from "react";
import { I18nextProvider } from "react-i18next";
import { useParams } from "react-router";
import type { PageProps } from "../../routes";
import "../copilot.css";
import { copilotI18n } from "../i18n";
import { isCopilotTab } from "../tabs";
import CopilotChatGate from "./CopilotChatV2";

/**
 * Item 5 (owner's handover): the Copilot in the v2 shell. `/copilot` is the chat-first page; `/copilot/<tab>` keeps the
 * existing seven-tab Copilot (Market Pulse, Strategy Lab, Coach...) so every tool built so far stays one link away. With
 * `ui_v2` off the v2 shell is not used and today's Copilot renders exactly as before.
 */
const LegacyCopilot = lazy(() => import("../../pages/AiCopilotPage"));

export default function CopilotPageV2(_props: PageProps = {}) {
  const { tab } = useParams<{ tab?: string }>();
  if (isCopilotTab(tab)) return <Suspense fallback={null}><LegacyCopilot /></Suspense>;
  return <I18nextProvider i18n={copilotI18n}><CopilotChatGate /></I18nextProvider>;
}
