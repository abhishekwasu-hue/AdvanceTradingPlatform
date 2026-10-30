import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * UI2-2: the v2 dashboard's API replies for the e2e specs (hand-written, made-up data in fixtures/dashboard_v2.json: no
 * real account, price or id). Registered after mockApi() and before page.goto(), so they answer first. Every read the
 * dashboard makes is answered, so a screenshot of `/` never depends on 404s or on when a block finished loading.
 */
export type Fixture = Record<string, unknown>;
export const F = JSON.parse(readFileSync(new URL("./fixtures/dashboard_v2.json", import.meta.url), "utf8")) as Fixture;
export const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);

export interface DashboardMocks {
  /** "connected" (default), "login" (stored, not logged in today), "none" (no broker stored), "empty" (nothing yet). */
  state?: "connected" | "login" | "none" | "empty";
  /** Replaces the strip's reply. */
  strip?: unknown;
  /** Also answer /auth/me with the fixture's user (the dashboard spec); left out, the caller's user stays. */
  me?: boolean;
}

/** UI2-7: GET /api/me/setup as the server works it out for these replies (the wizard already seen once). P3: with the
 * Get started card's three steps (broker, trading bot, Telegram), none of its marks set. */
export function setupAnswer(state: DashboardMocks["state"] = "connected") {
  const done: Record<string, boolean> = { broker: state !== "none", risk: true, strategy: state === "connected" || state === "login", alerts: false };
  const steps = ["broker", "risk", "strategy", "alerts"].map((key) => ({ key, done: done[key], skipped: false, skipped_at: null }));
  const card = [{ key: "broker", done: done.broker }, { key: "paper_bot", done: done.strategy }, { key: "telegram", done: false }];
  return {
    steps, done_count: steps.filter((s) => s.done).length, next: steps.find((s) => !s.done)?.key ?? null, complete: false,
    get_started: { steps: card, done_count: card.filter((s) => s.done).length, complete: false, hidden_at: null, shown_at: null, visible: true },
    stored: { steps: Object.fromEntries(steps.map((s) => [s.key, { skipped_at: null }])), opened_at: "2026-10-12T09:00:00Z", dismissed_at: null,
      get_started_hidden_at: null, get_started_shown_at: null },
    version: 1,
  };
}

export async function mockDashboard(page: Page, { state = "connected", strip, me = false }: DashboardMocks = {}): Promise<void> {
  const empty = state === "empty" || state === "none";
  const replies: [RegExp, unknown][] = [
    ...(me ? [[api("/auth/me$"), F.me] as [RegExp, unknown]] : []),
    [api("/market/strip$"), strip ?? (state === "login" ? F.strip_memory : F.strip)],
    [api("/option-chain/banners$"), empty ? { banners: [] } : F.oi_banners],
    [api("/portfolio/exposure"), empty ? { ...(F.exposure as object), open_positions: 0, unrealised_pnl: 0, realised_today: 0, by_symbol: [], priced_symbols: [] } : F.exposure],
    [api("/positions$"), empty ? [] : F.positions],
    [api("/ai/actions(\\?|$)"), empty ? [] : F.actions],
    [api("/deployments(\\?|$)"), empty ? [] : F.deployments],
    [api("/paper/risk$"), F.paper_risk],
    [api("/notifications\\?unread_only=true$"), empty ? [] : F.notifications_unread],
    [api("/analytics/summary(\\?|$)"), F.analytics_summary],
    [api("/alert-channels$"), F.alert_channels],
    [api("/me/setup(\\?|$)"), setupAnswer(state)],
  ];
  for (const [re, json] of replies) await page.route(re, (route) => route.fulfill({ json }));
}
