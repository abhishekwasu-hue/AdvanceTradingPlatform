import type { ReactNode } from "react";
import { useHasPageHelpSlot, useRegisterPageSubtitle } from "../../shell/pageHelp";

/** The one page header: title, a one-line description, and the page's actions on the right (wrapping on phones).
 * PROSE-1: inside the v2 shell the description goes behind the (?) beside the page title (src/shell/pageHelp.tsx). */
export function PageHeader({ title, description, actions, meta }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  const inShell = useHasPageHelpSlot();
  useRegisterPageSubtitle(inShell ? description : null);
  return (
    <header className="mb-4 flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-border pb-4">
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && !inShell && <p data-page-subtitle className="mt-1 max-w-3xl text-sm text-fg-muted">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
