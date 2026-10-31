import { PageTabs } from "../../ui/PageTabs";
import { useFeatures } from "../../ui/useFeatures";
import { useCopilotT } from "../i18n";
import { COPILOT_V2_TABS, type CopilotV2Tab } from "../tabs";

/**
 * CP-UX CU4 + M60 (owner order, approved 30 Oct): the ONE Copilot's tab bar - Ask (the chat) · Read the chart (Market
 * Pulse) · Analyze a chart (the upload tool, while `chart_analyze` is on) · Coach (Review and the Guide). At most four
 * tabs; each is a link with its own address; every target is at least 44 px (the shared primary tab row).
 */
export default function CopilotTabsV2({ active }: { active: CopilotV2Tab }) {
  const t = useCopilotT();
  const { on } = useFeatures();
  return (
    <PageTabs kind="links" label={t("v2tabs.label")} value={active} data={{ "copilot-tabs": "v2" }}
      items={COPILOT_V2_TABS.filter((tab) => !tab.flag || on(tab.flag))
        .map((tab) => ({ key: tab.key, label: t(`v2tabs.${tab.key}`), to: tab.to, data: { "copilot-tab": tab.key } }))} />
  );
}
