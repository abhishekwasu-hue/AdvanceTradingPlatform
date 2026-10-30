import { PageTabs } from "../../ui/PageTabs";
import { useCopilotT } from "../i18n";
import { COPILOT_V2_TABS, type CopilotV2Tab } from "../tabs";

/**
 * CP-UX CU4 (owner order 30 Oct, `ui_v2`): the ONE Copilot's tab bar - Ask (the chat) · Brief (Market Pulse + News
 * Radar) · Build (a link to Strategies › Build: the one builder, no second one here) · Review (Coach) · Signals
 * (Watchtower). Each tab is a link with its own address; every target is at least 44 px (the shared primary tab row).
 */
export default function CopilotTabsV2({ active }: { active: CopilotV2Tab }) {
  const t = useCopilotT();
  return (
    <PageTabs kind="links" label={t("v2tabs.label")} value={active} data={{ "copilot-tabs": "v2" }}
      items={COPILOT_V2_TABS.map((tab) => ({ key: tab.key, label: t(`v2tabs.${tab.key}`), to: tab.to, data: { "copilot-tab": tab.key } }))} />
  );
}
