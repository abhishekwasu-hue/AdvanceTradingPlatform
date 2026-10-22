/** MO4 slice 3 (`ui_v2` + `growth_v2`): what `/api/growth/*` and `/api/admin/console/activation` answer. */

export interface PlanMeter {
  key: string;
  label: string;
  used: number | null;
  /** null: the plan sets no limit on it (the row shows the count only). */
  limit: number | null;
  period: string;
  text: string;
  state: "ok" | "near" | "full";
  note: string | null;
}

export interface PlanPage {
  plan: { id: string; name: string; status: string };
  trial: { plan_name: string; ends_on: string; days_left: number } | null;
  includes: { key: string; text: string }[];
  meters: PlanMeter[];
  month_starts: string;
  resets: string;
}

export interface CardFact { key: string; label: string; value: string }

export interface DisciplineCard {
  title: string;
  week_label: string;
  facts: CardFact[];
  lines: string[];
  footer: string;
  empty: boolean;
  /** In-app only, never on the card image. */
  hints: string[];
}

export interface ActivationPerson {
  id: number; email: string; tenant: string; plan: string; signed_up_at: string | null;
  broker_at: string | null; paper_bot_at: string | null; journal_at: string | null; activated_at: string | null;
}

export interface Activation {
  days: number;
  since: string;
  funnel: { key: string; label: string; count: number }[];
  median_hours_to_activation: number | null;
  people: ActivationPerson[];
}
