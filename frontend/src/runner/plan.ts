/** Runner S7: the plan field that carries the runner (Route 3 B6, backend `app/plans/registry.py` `Plan.runner`: Pro).
 * A tiny module on purpose: the navigation model reads it. */
export const RUNNER_PLAN_FIELD = "runner";

/**
 * The plan hook: a caller that knows his plan's fields passes them; `runner: false` there hides My runner. No answer,
 * or an answer without the field, leaves it to the flag alone (the field is defined on the plans, not enforced yet).
 */
export function planAllowsRunner(plan: Readonly<Record<string, unknown>> | null | undefined): boolean {
  return !plan || plan[RUNNER_PLAN_FIELD] !== false;
}
