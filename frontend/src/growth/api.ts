import { request } from "../api/client";
import type { Activation, DishPage, DisciplineCard, PageDish, PlanPage } from "./types";

/** MO4 slice 3: the growth reads (404 while `ui_v2` or `growth_v2` is off). */
export const growthApi = {
  plan: () => request<PlanPage>("/growth/plan"),
  disciplineWeek: () => request<DisciplineCard>("/growth/share/discipline-week"),
  /** MO4 slice 4: a page's dish; Charts passes the symbol (and its exchange off NSE). */
  dish: (page: DishPage, symbol?: string | null, exchange?: string | null) => {
    const q = new URLSearchParams();
    if (symbol) q.set("symbol", symbol);
    if (exchange && exchange.toUpperCase() !== "NSE") q.set("exchange", exchange.toUpperCase());
    const query = q.toString();
    return request<PageDish>(`/growth/dish/${page}${query ? `?${query}` : ""}`);
  },
  activation: (days = 90) => request<Activation>(`/admin/console/activation?days=${days}`),
};
