import { Check, X } from "lucide-react";
import { useEffect, useId } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { toast } from "../components/Toast";
import { CARD, LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { getStartedCard, hideGetStarted, shouldHideForGood, type GetStartedCard } from "./getStarted";
import { useSetup } from "./useSetup";
import { ProgressStat } from "../ui/numbersFirst";

/**
 * P3 (the owner's "simple front page"): the "Get started" card at the top of the Dashboard, in both shells - three
 * steps, each with one button and a tick when done (src/setup/getStarted.ts). It reads the stored setup progress (the
 * same store as the wizard, `GET /api/me/setup`), disappears for good once all three are done, and can be hidden; Help
 * -> "Show Get started" brings it back (ShowGetStartedButton). It never opens the setup wizard.
 *
 * `primaryNext`: the first open step's button is the primary one. The v2 dashboard passes false, as its one primary
 * button is the first "Needs attention" action (UI v2 §9).
 */
const DONE_LINK = "inline-flex min-h-11 shrink-0 items-center rounded-control text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

export const HIDDEN_TOAST = "Get started is hidden. Bring it back any time: Settings, Help, Show Get started.";

export default function GetStartedCardV2({ primaryNext = true }: { primaryNext?: boolean }) {
  const { user } = useAuth();
  const setup = useSetup(user?.id ?? null);
  const card = getStartedCard(setup.view, { role: user?.role });
  const finished = setup.fresh && shouldHideForGood(setup.view);

  // All three done: store the "for good" mark once, on the server's answer (never on the cached copy).
  useEffect(() => {
    if (finished) setup.update((stored) => hideGetStarted(stored, new Date().toISOString()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  if (!card) return null;
  const hide = () => {
    setup.update((stored) => hideGetStarted(stored, new Date().toISOString()));
    toast.info(HIDDEN_TOAST);
  };
  return <GetStartedViewV2 card={card} primaryNext={primaryNext} onHide={hide} />;
}

/** The card as drawn, from its steps (rendered by the unit tests in every state). */
export function GetStartedViewV2({ card, primaryNext = true, onHide }: { card: GetStartedCard; primaryNext?: boolean; onHide?: () => void }) {
  const titleId = useId();
  const next = card.steps.find((s) => !s.done)?.key ?? null;
  return (
    <section aria-labelledby={titleId} data-block="get-started" className={`${CARD} p-space-4 md:p-space-5`}>
      <div className="flex items-start justify-between gap-space-3">
        <div className="min-w-0">
          <h2 id={titleId} className="text-t15 font-semibold text-fg">Get started</h2>
          {/* UX-11 U3: progress as "1 of 3 done" with a thin bar. */}
          {card.complete ? <p className="mt-px text-t13 text-fg-muted">All set. Happy trading.</p>
            : <ProgressStat label="Steps" done={card.doneCount} of={card.steps.length} unit="done" className="mt-px w-40 max-w-full" />}
        </div>
        <button type="button" onClick={onHide} aria-label="Hide Get started"
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors duration-fast hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <X size={18} aria-hidden />
        </button>
      </div>
      <ol className="mt-space-3 divide-y divide-subtle">
        {card.steps.map((step, i) => (
          <li key={step.key} data-step={step.key} data-done={step.done ? "true" : "false"}
            className={`flex gap-space-3 py-space-3 first:pt-0 last:pb-0 ${step.done ? "items-center" : "items-start"}`}>
            <span aria-hidden
              className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-pill text-t13 font-semibold ${step.done ? "bg-up/10 text-up" : "mt-px bg-surface-2 text-fg-muted"}`}>
              {step.done ? <Check size={16} strokeWidth={2.5} /> : i + 1}
            </span>
            {step.done ? (
              // A done step: one line, the tick and a quiet link.
              <div className="flex min-w-0 flex-1 items-center justify-between gap-space-3">
                <p className="min-w-0 text-t14 font-medium text-fg-muted">
                  {step.title}
                  <span className="sr-only"> (done)</span>
                </p>
                <Link to={step.action.to} className={DONE_LINK}>{step.action.label}</Link>
              </div>
            ) : (
              <div className="flex min-w-0 flex-1 flex-col gap-space-2 sm:flex-row sm:items-center sm:justify-between sm:gap-space-3">
                <p className="min-w-0 pt-space-1 text-t14 font-medium text-fg sm:pt-0">{step.title}</p>
                <Link to={step.action.to} className={`self-start sm:self-auto ${primaryNext && step.key === next ? LINK_PRIMARY : LINK_SECONDARY}`}>
                  {step.action.label}
                </Link>
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
