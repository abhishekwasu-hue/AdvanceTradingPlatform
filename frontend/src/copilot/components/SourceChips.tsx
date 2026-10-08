import { BookOpen, Database, Gauge, GraduationCap, LayoutList, ListChecks, Ruler } from "lucide-react";
import type { ReactNode } from "react";
import { useCopilotT } from "../i18n";

export interface SourceItem { kind: "brief" | "coach" | "deployments" | "guide" | "memory" | "interview" | "concept" | "rules"; label?: string }

const ICON: Record<SourceItem["kind"], ReactNode> = {
  brief: <Gauge size={12} />, coach: <GraduationCap size={12} />, deployments: <LayoutList size={12} />, guide: <BookOpen size={12} />,
  memory: <Database size={12} />, interview: <ListChecks size={12} />, concept: <BookOpen size={12} />, rules: <Ruler size={12} />,
};

/** "Based on: ..." under every Copilot answer - the facts the answer was built from. */
export default function SourceChips({ items }: { items: SourceItem[] }) {
  const t = useCopilotT();
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]" aria-label={t("ask.basedOn")}>
      <span className="text-fg-muted">{t("ask.basedOn")}</span>
      {items.map((s, i) => (
        <span key={`${s.kind}-${s.label ?? ""}-${i}`} className="inline-flex items-center gap-1 rounded-full border border-ai/30 bg-ai/10 px-2 py-0.5 text-fg">
          {ICON[s.kind]}{s.label ?? t(`ask.source.${s.kind}`)}
        </span>
      ))}
    </div>
  );
}
