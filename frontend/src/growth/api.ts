import { request } from "../api/client";
import type { Activation, DisciplineCard, PlanPage } from "./types";

/** MO4 slice 3: the growth reads (404 while `ui_v2` or `growth_v2` is off). */
export const growthApi = {
  plan: () => request<PlanPage>("/growth/plan"),
  disciplineWeek: () => request<DisciplineCard>("/growth/share/discipline-week"),
  activation: (days = 90) => request<Activation>(`/admin/console/activation?days=${days}`),
};
