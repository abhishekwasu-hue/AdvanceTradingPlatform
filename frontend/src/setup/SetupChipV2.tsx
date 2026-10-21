import { ChevronRight, X } from "lucide-react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { CARD } from "../dashboard/CardV2";
import { SETUP_PATH, setupChip } from "./model";
import { useSetup } from "./useSetup";

/**
 * UI2-7 (UI v2 proposal §5 block 2, §11): the Setup chip - "Setup 2 of 4 · Next: set your risk limits" - linking to
 * Settings → Setup, where the wizard opens on that step. The words come from the stored progress (setup/model.ts
 * setupChip). P3: the dashboard shows the Get started card instead (GetStartedCardV2); the chip stays in Settings
 * (v2 only) until setup is complete.
 */
export default function SetupChipV2({ label, next, to, onDismiss }: { label: string; next: string; to: string; onDismiss?: () => void }) {
  return (
    <div data-block="setup" className={`${CARD} flex min-h-12 items-center gap-space-1 py-0 pl-space-4 md:pl-space-5 ${onDismiss ? "pr-space-1" : "pr-space-4 md:pr-space-5"}`}>
      <Link to={to}
        className="flex min-h-11 min-w-0 flex-1 items-center gap-space-3 rounded-control transition-colors duration-fast hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <span className="inline-flex h-7 shrink-0 items-center rounded-pill bg-brand/10 px-space-3 text-t13 font-semibold text-brand">{label}</span>
        <span className="min-w-0 flex-1 text-t14 text-fg">{next}</span>
        <ChevronRight size={18} aria-hidden className="shrink-0 text-fg-muted" />
      </Link>
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Hide the setup reminder"
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors duration-fast hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <X size={18} aria-hidden />
        </button>
      )}
    </div>
  );
}

/**
 * Settings → Setup from today's Settings page while `ui_v2` is on (until UI2-6 brings the Settings v2 groups): the chip
 * until setup is complete (a closed dashboard chip still shows here), then a plain link to the wizard. The go-live
 * checklist is under Settings → Advanced (P3).
 */
export function SetupLinkV2() {
  const { user } = useAuth();
  const { view } = useSetup(user?.id ?? null);
  const chip = setupChip(view, { ignoreDismissal: true });
  return chip ? <SetupChipV2 {...chip} /> : <SetupChipV2 label="Setup" next="Your setup steps" to={SETUP_PATH} />;
}
