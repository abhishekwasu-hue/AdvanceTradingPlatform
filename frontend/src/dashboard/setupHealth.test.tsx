import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { cleanHealth, cleanView } from "../setup/model";
import type { SetupHealthItem } from "../setup/model";
import type { BrokerGateState } from "../ui/brokerSession";
import type { BrokerLogin } from "../ui/BrokerGate";
import dashboardSource from "./DashboardV2.tsx?raw";
import { hiddenToday, storageKey, withHidden } from "./dismissToday";
import { AI_FIX_PATH, TELEGRAM_FIX_PATH, attentionRows, type AttentionInput } from "./model";
import modelSource from "./model.ts?raw";
import NeedsAttentionV2 from "./NeedsAttentionV2";

/** Settings redesign D ("Setup health", review R8): the setup-health rows of the Needs attention block. */

const NOW = Date.parse("2026-10-19T11:00:00+05:30");
const upstox = { broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null } as const;
const CONNECTED: BrokerGateState = { kind: "connected", brokers: [{ ...upstox }] };
const LOGIN: BrokerGateState = { kind: "login-needed", broker: { ...upstox, needs_login: true, token_status: "EXPIRED" }, others: [], expired: true };
const base: AttentionInput = { nowMs: NOW, broker: CONNECTED, brokerRows: [upstox], pending: [], deployments: [], risk: null,
  exposure: null, unread: [], status: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }, emailVerified: true,
  approvalsPath: "/copilot/watchtower" };
const idle: BrokerLogin = { busy: null, error: null, codeStep: null, login: () => undefined, submitCode: () => undefined };
const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

const rowsWith = (health: SetupHealthItem[], extra: Partial<AttentionInput> = {}) => attentionRows({ ...base, health, ...extra });
const BUY_SELL = /\b(buy|sell|target|stop[- ]?loss)\b/i;

describe("Setup health: one row per state, each with the one button that opens the fix", () => {
  it("nothing when all is fine: no health rows (the block keeps its 'All clear' line)", () => {
    expect(rowsWith([])).toEqual([]);
    expect(attentionRows({ ...base, health: null })).toEqual([]);
    expect(attentionRows(base)).toEqual([]);
  });

  it("the Upstox Analytics Token expired / ending today -> Renew on the Broker page", () => {
    const [expired] = rowsWith([{ key: "analytics_token", state: "expired" }]);
    expect(expired).toMatchObject({ id: "renew-token", title: "Your Upstox Analytics Token has expired", action: { label: "Renew", to: "/broker" }, dismissible: true });
    const [ending] = rowsWith([{ key: "analytics_token", state: "expiring" }]);
    expect(ending.title).toBe("Your Upstox Analytics Token ends today");
  });

  it("the broker session ended -> the existing Log in row (reused), now hideable for the day", () => {
    const [login] = attentionRows({ ...base, broker: LOGIN, brokerRows: [{ ...upstox, needs_login: true }] });
    expect(login).toMatchObject({ id: "login", action: { label: "Log in", login: true }, dismissible: true });
  });

  it("the AI key out of credit or failing -> Fix in Settings, AI; the plain sentence as the detail", () => {
    const [credit] = rowsWith([{ key: "ai_key", state: "no_credit", message: "This key has no credit left." }]);
    expect(credit).toMatchObject({ id: "ai-key", title: "Your AI key is out of credit", action: { label: "Fix", to: AI_FIX_PATH } });
    const [failing] = rowsWith([{ key: "ai_key", state: "failing", message: "This key did not work. Check that you copied all of it." }]);
    expect(failing).toMatchObject({ title: "Your AI key is not working", detail: "This key did not work. Check that you copied all of it." });
    expect(AI_FIX_PATH).toBe("/settings#ai");
  });

  it("Telegram not connected -> Connect in Settings, Alerts, only once the Get started card is hidden", () => {
    const tg: SetupHealthItem[] = [{ key: "telegram", state: "not_connected" }];
    expect(rowsWith(tg, { getStartedShowing: true })).toEqual([]);
    const [row] = rowsWith(tg, { getStartedShowing: false });
    expect(row).toMatchObject({ id: "telegram", title: "Get your alerts on Telegram", action: { label: "Connect", to: TELEGRAM_FIX_PATH } });
    expect(TELEGRAM_FIX_PATH).toBe("/settings#alerts");
  });

  it("the paper bot paused -> the existing Review row (reused), hideable for the day", () => {
    const [row] = attentionRows({ ...base, deployments: [{ strategy_id: "orb_nifty", symbol: "NIFTY", status: "PAUSED", pause_reason: null, mode: "PAPER" }] as never });
    expect(row).toMatchObject({ id: "paused", action: { label: "Review", to: "/deployments" }, dismissible: true });
  });

  it("the data feed stale -> 'Live prices are delayed', a notice with nothing to fix", () => {
    const [row] = rowsWith([{ key: "data_feed", state: "delayed" }]);
    expect(row).toMatchObject({ id: "data-delayed", title: "Live prices are delayed", dismissible: true });
    expect(row.action).toBeUndefined();
  });

  it("order: login, token, ... delayed prices, AI key, ... Telegram last; user wording only", () => {
    const rows = attentionRows({ ...base, broker: LOGIN, brokerRows: [{ ...upstox, needs_login: true }],
      health: [{ key: "telegram", state: "not_connected" }, { key: "ai_key", state: "failing", message: "" }, { key: "data_feed", state: "delayed" },
        { key: "analytics_token", state: "expired" }] });
    expect(rows.map((r) => r.id)).toEqual(["login", "renew-token", "data-delayed", "ai-key", "telegram"]);
    for (const r of rows) expect(`${r.title} ${r.detail}`).not.toMatch(BUY_SELL);
    expect(rows.find((r) => r.id === "ai-key")?.detail).toBe("Check the key or connect another one.");
  });
});

describe("Setup health: hide for the day", () => {
  afterEach(() => { try { localStorage.clear(); } catch { /* none */ } });

  it("a hidden row stays out today and comes back tomorrow (IST); rows that are not hideable never hide", () => {
    const stored = withHidden(null, "ai-key", NOW);
    expect([...hiddenToday(stored, NOW)]).toEqual(["ai-key"]);
    const health: SetupHealthItem[] = [{ key: "ai_key", state: "no_credit", message: "" }];
    expect(rowsWith(health, { dismissed: hiddenToday(stored, NOW) })).toEqual([]);
    const tomorrow = NOW + 24 * 60 * 60 * 1000;
    expect(hiddenToday(stored, tomorrow).size).toBe(0);
    expect(rowsWith(health, { dismissed: hiddenToday(stored, tomorrow) }).map((r) => r.id)).toEqual(["ai-key"]);
    // approvals cannot be hidden
    const pending = [{ id: 1, action: "ENTRY", status: "PROPOSED", evidence: { symbol: "NIFTY" }, reason: "r", created_at: "2026-10-19T05:00:00Z", expires_at: null }];
    expect(attentionRows({ ...base, pending: pending as never, dismissed: new Set(["approvals"]) }).map((r) => r.id)).toEqual(["approvals"]);
  });

  it("anything unreadable is nothing hidden; the key is per user", () => {
    expect(hiddenToday("not json", NOW).size).toBe(0);
    expect(hiddenToday(JSON.stringify({ day: "2026-10-19", ids: [1, "x"] }), NOW)).toEqual(new Set(["x"]));
    expect(storageKey(7)).not.toBe(storageKey(8));
    expect(JSON.parse(withHidden(withHidden(null, "a", NOW), "b", NOW))).toEqual({ day: "2026-10-19", ids: ["a", "b"] });
  });

  it("the block draws a 'Hide for today' button only on hideable rows, and only when it can hide", () => {
    const rows = [
      ...rowsWith([{ key: "ai_key", state: "no_credit", message: "" }, { key: "data_feed", state: "delayed" }]),
      { id: "approvals", icon: "approval" as const, tone: "neutral" as const, title: "1 trade is waiting for your approval", detail: "d", action: { label: "Review trade", to: "/x" } },
    ];
    const out = html(<NeedsAttentionV2 rows={rows} loginBroker={null} login={idle} onHide={() => undefined} />);
    expect((out.match(/data-hide=/g) ?? []).length).toBe(2);
    expect(out).toContain('data-hide="ai-key"');
    expect(out).toContain('data-hide="data-delayed"');
    expect(out).toContain('aria-label="Hide for today"');
    expect(out).toMatch(/href="\/settings#ai"[^>]*>Fix</);
    expect(html(<NeedsAttentionV2 rows={rows} loginBroker={null} login={idle} />)).not.toContain("data-hide=");
  });
});

describe("Setup health: the server's answer", () => {
  it("cleanHealth keeps only known items and states", () => {
    expect(cleanHealth(null)).toBeNull();
    expect(cleanHealth([{ key: "ai_key", state: "no_credit", message: "m" }, { key: "toString", state: "x" }, { key: "telegram", state: "odd" },
      { key: "data_feed", state: "delayed" }, "junk"])).toEqual([{ key: "ai_key", state: "no_credit", message: "m" }, { key: "data_feed", state: "delayed" }]);
    const view = cleanView({ steps: [], version: 0, stored: {}, health: [{ key: "telegram", state: "not_connected" }] });
    expect(view?.health).toEqual([{ key: "telegram", state: "not_connected" }]);
  });

  it("the Dashboard never calls the Telegram connect GET (it polls Telegram) and uses only the server's fresh answer", () => {
    expect(dashboardSource).not.toContain("telegram/connect");
    expect(modelSource).not.toContain("telegram/connect");
    expect(dashboardSource).toContain("setup.fresh ? setup.view?.health ?? null : null");
    expect(dashboardSource).toContain('user?.role === "VIEWER" ? serverHealth.filter((h) => h.key === "ai_key" || h.key === "data_feed")');
    expect(dashboardSource).toContain("getStartedShowing: card !== null");
  });
});
