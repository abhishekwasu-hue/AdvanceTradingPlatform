import { m } from "framer-motion";
import type { ReactNode } from "react";
import { cx } from "../../components/primitives";
import { STAGGER } from "../components/CopilotMotion";

/** Building blocks shared by the Copilot tabs. */

export function TabSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <div className="copilot-skeleton copilot-shimmer h-28" />
      <div className="grid gap-3 md:grid-cols-3">
        <div className="copilot-skeleton copilot-shimmer h-40" />
        <div className="copilot-skeleton copilot-shimmer h-40" />
        <div className="copilot-skeleton copilot-shimmer h-40" />
      </div>
    </div>
  );
}

/** A plain glass panel with a title row (the tilt is reserved for a tab's main cards). */
export function Panel({ title, icon, action, children, className, testId }: {
  title?: ReactNode; icon?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; testId?: string;
}) {
  return (
    <section className={cx("copilot-glass rounded-2xl p-4", className)} data-testid={testId}>
      {(title || action) && (
        <div className="mb-3 flex items-center gap-2">
          {icon && <span className="text-ai" aria-hidden>{icon}</span>}
          {title && <h2 className="text-sm font-semibold text-fg">{title}</h2>}
          {action && <div className="ml-auto flex items-center gap-2">{action}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/** Children appear one after another (stagger); a plain list when motion is reduced. */
export function Stagger({ children, className }: { children: ReactNode; className?: string }) {
  return <m.div className={className} variants={STAGGER.container} initial="hidden" animate="show">{children}</m.div>;
}
export function StaggerItem({ children, className }: { children: ReactNode; className?: string }) {
  return <m.div className={className} variants={STAGGER.item}>{children}</m.div>;
}

/** A tab's intro line: what it is for, in one sentence. */
export function TabIntro({ children }: { children: ReactNode }) {
  return <p className="mb-3 text-sm text-fg-muted">{children}</p>;
}

export const money = (v: number | null | undefined, digits = 0) =>
  v == null ? "-" : `${v < 0 ? "-" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: digits })}`;
export const num = (v: number | null | undefined, d = 2) => (v == null ? "-" : v.toLocaleString("en-IN", { maximumFractionDigits: d }));

/** "5 min", "3 h", "2 days". */
export function ageText(minutes: number): string {
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} min`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} days`;
}
