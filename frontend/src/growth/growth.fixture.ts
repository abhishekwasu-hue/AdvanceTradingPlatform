import type { Activation, DisciplineCard, PlanPage } from "./types";

/** MO4 slice 3: made-up answers in the shape of backend/tests/test_mo4_growth.py (no real person, no real figure). */

export const DEMO_PLAN: PlanPage = {
  plan: { id: "free", name: "Free", status: "active" },
  trial: { plan_name: "Pro", ends_on: "2032-03-20", days_left: 4 },
  includes: [
    { key: "paper_bots", text: "Up to 2 paper bots running at once" },
    { key: "custom_strategies", text: "3 strategies of your own" },
    { key: "backtests", text: "50 backtests a month" },
    { key: "watchlists", text: "5 watchlists" },
    { key: "scan_size", text: "Up to 50 stocks in one scan" },
    { key: "brokers", text: "1 broker account" },
    { key: "alerts", text: "1 alert channel (for example Telegram)" },
    { key: "options", text: "Options tools: single options only" },
    { key: "ai", text: "AI tools with your own AI key" },
    { key: "team", text: "Just you (no team members)" },
  ],
  meters: [
    { key: "watchlists", label: "Watchlists", used: 2, limit: 5, period: "", text: "2 of 5", state: "ok", note: null },
    { key: "scans", label: "Scans", used: 3, limit: null, period: "this month", text: "3 this month", state: "ok", note: "Up to 50 stocks in one scan." },
    { key: "ai", label: "AI requests", used: 12, limit: null, period: "this month", text: "12 this month", state: "ok", note: "Requests on your own AI key are counted too." },
    { key: "analyses_today", label: "Chart analyses today", used: 4, limit: 5, period: "today", text: "4 of 5 today", state: "near", note: null },
    { key: "backtests", label: "Backtests (Strategy Lab)", used: 4, limit: 50, period: "this month", text: "4 of 50 this month", state: "ok", note: null },
    { key: "paper_bots", label: "Paper bots running", used: 2, limit: 2, period: "", text: "2 of 2", state: "full", note: null },
  ],
  month_starts: "2032-03-01",
  resets: "Monthly counts start again on the 1st of each month.",
};

export const DEMO_CARD: DisciplineCard = {
  title: "My discipline this week",
  week_label: "Week of 15 Mar",
  facts: [
    { key: "inside_limits", label: "Days inside my own limits", value: "2 of 3" },
    { key: "journal_streak", label: "Journal streak", value: "1 week" },
    { key: "discipline_score", label: "Discipline score", value: "82 / 100" },
    { key: "days_traded", label: "Days I traded", value: "2 of 3" },
  ],
  lines: [
    "I stayed inside my own daily limits on 2 of 3 market days.",
    "My own note on every closed trade, 1 week in a row.",
    "A day without a trade is a good day when nothing met my plan.",
  ],
  footer: "Paper trading on AiAlgoDesk. Discipline facts only: no stocks, no prices, no advice.",
  empty: false,
  hints: [],
};

export const DEMO_ACTIVATION: Activation = {
  days: 90,
  since: "2031-12-17T09:00:00+00:00",
  funnel: [
    { key: "signed_up", label: "Signed up", count: 10 },
    { key: "broker", label: "Connected a broker", count: 6 },
    { key: "paper_bot", label: "Started a first paper bot", count: 4 },
    { key: "journal", label: "First journal entry", count: 3 },
    { key: "activated", label: "All three (activated)", count: 2 },
  ],
  median_hours_to_activation: 30.5,
  people: [
    { id: 41, email: "fi**@example.com", tenant: "fi**@example.com", plan: "Free", signed_up_at: "2032-03-10T04:00:00+00:00",
      broker_at: "2032-03-10T05:00:00+00:00", paper_bot_at: "2032-03-10T06:00:00+00:00", journal_at: "2032-03-11T10:00:00+00:00",
      activated_at: "2032-03-11T10:00:00+00:00" },
    { id: 42, email: "se****@example.com", tenant: "Desk Two", plan: "Pro", signed_up_at: "2032-03-12T04:00:00+00:00",
      broker_at: "2032-03-12T05:00:00+00:00", paper_bot_at: null, journal_at: null, activated_at: null },
  ],
};
