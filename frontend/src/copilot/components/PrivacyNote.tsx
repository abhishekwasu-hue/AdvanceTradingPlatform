import { ShieldCheck } from "lucide-react";
import { useCopilotT } from "../i18n";

/** DPDP: what leaves the platform for an outside AI provider, in plain words - shown where the trader types. */
export default function PrivacyNote({ compact = false }: { compact?: boolean }) {
  const t = useCopilotT();
  return (
    <details className="group rounded-lg border border-border bg-surface-2/50 px-3 py-2 text-xs text-fg-muted">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-fg-muted hover:text-fg">
        <ShieldCheck size={13} className="text-ai" />{t("privacy.title")}
      </summary>
      <ul className={`mt-2 list-disc space-y-1 pl-5 ${compact ? "" : "leading-relaxed"}`}>
        <li>{t("privacy.sent")}</li>
        <li>{t("privacy.notSent")}</li>
        <li>{t("privacy.rules")}</li>
        <li>{t("privacy.logged")}</li>
      </ul>
    </details>
  );
}
