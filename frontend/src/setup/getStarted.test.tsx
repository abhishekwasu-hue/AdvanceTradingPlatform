import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import dashboardSource from "../pages/DashboardPage.tsx?raw";
import { pageFromPath } from "../routes";
import dashboardV2Source from "../dashboard/DashboardV2.tsx?raw";
import settingsSource from "../pages/SettingsPage.tsx?raw";
import type { ReadinessChecklist, ReadinessItem } from "../types";
import { GetStartedViewV2 } from "./GetStartedCardV2";
import { GoLiveListV2 } from "./GoLiveChecklistV2";
import {
  ALERTS_PATH, BROKER_FALLBACK_PATH, BROKER_PATH, GO_LIVE_PATH, connectBrokerPath, getStartedCard, getStartedSteps, hideGetStarted,
  shouldHideForGood, showGetStarted,
} from "./getStarted";
import { goLiveView } from "./goLive";
import { paperOnlyFrom } from "./paperOnly";
import { topBarNotices } from "../shell/TopBarV2";
import appSource from "../App.tsx?raw";
import shellSource from "../shell/AppShellV2.tsx?raw";
import paperOnlySource from "./paperOnly.ts?raw";
import { cleanStored, cleanView, withStored, type GetStartedKey, type SetupStored, type SetupView } from "./model";

/** P3: the Dashboard's Get started card (states 0-3, hidden for good, back from Help), no LIVE on the front page in
 * PAPER, and the Go-live checklist's folds under Settings → Advanced. */

const T = "2026-10-21T09:30:00Z";
const KEYS: GetStartedKey[] = ["broker", "paper_bot", "telegram"];

function view(done: GetStartedKey[] = [], marks: Partial<SetupStored> = {}): SetupView {
  const v = cleanView({
    steps: ["broker", "risk", "strategy", "alerts"].map((key) => ({ key, done: false })), version: 2,
    stored: { ...cleanStored(null), ...marks },
    get_started: { steps: KEYS.map((key) => ({ key, done: done.includes(key) })), done_count: done.length },
  });
  if (!v) throw new Error("not a view");
  return v;
}

const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const brokerAt = (path: string) => connectBrokerPath((p) => p === path);

describe("P3 Get started: the three steps", () => {
  const states: GetStartedKey[][] = [[], ["broker"], ["broker", "paper_bot"], ["broker", "paper_bot", "telegram"]];

  it("0, 1 and 2 done: the card with a tick per done step; 3 done: no card", () => {
    states.forEach((done, n) => {
      const card = getStartedCard(view(done));
      if (n === 3) {
        expect(card).toBeNull();
        return;
      }
      expect(card?.doneCount).toBe(n);
      expect(card?.steps.map((s) => s.done)).toEqual(KEYS.map((k) => done.includes(k)));
      const out = html(<GetStartedViewV2 card={card!} />);
      expect(out).toContain("Get started");
      expect(out).toContain(`${n} of 3 done`);
      expect((out.match(/data-done="true"/g) ?? []).length).toBe(n);
      expect((out.match(/<svg/g) ?? []).length).toBe(n + 1);                 // a tick per done step, and the close icon
      expect((out.match(/<a /g) ?? []).length).toBe(3);                       // one button per step
      expect((out.match(/bg-brand /g) ?? []).length).toBe(1);                 // the next step's is the primary one
    });
  });

  it("names the three steps and where each button leads", () => {
    const card = getStartedSteps(view(), brokerAt("/nowhere"))!;
    expect(card.steps.map((s) => s.title)).toEqual(["Connect your broker", "Start your paper bot", "Get alerts on your phone (Telegram)"]);
    expect(card.steps.map((s) => s.action)).toEqual([
      { label: "Connect your broker", to: BROKER_FALLBACK_PATH }, { label: "Start paper bot", to: "/deployments" },
      { label: "Set up Telegram", to: ALERTS_PATH }]);
    const done = getStartedSteps(view(KEYS), BROKER_PATH)!;
    expect(done.steps.map((s) => s.action.label)).toEqual(["Your broker", "See your bot", "Alert settings"]);
  });

  it("'Connect your broker' leads to /broker once that page exists, else to today's broker settings", () => {
    expect(brokerAt(BROKER_PATH)).toBe("/broker");
    expect(brokerAt("/other")).toBe("/settings");
    // the Broker page lands in its own change: until then today's broker settings, after it /broker, with no edit here
    expect(connectBrokerPath()).toBe(pageFromPath(BROKER_PATH) === null ? BROKER_FALLBACK_PATH : BROKER_PATH);
  });

  it("the v2 dashboard's card has no primary button (its one primary is the first Needs attention action)", () => {
    const out = html(<GetStartedViewV2 card={getStartedCard(view(["broker"]))!} primaryNext={false} />);
    expect(out).not.toMatch(/bg-brand /);
  });

  it("no card without the server's answer, or for a VIEWER", () => {
    expect(getStartedCard(null)).toBeNull();
    expect(getStartedCard(cleanView({ steps: [], stored: {}, version: 1 }))).toBeNull();     // an older cache: no guess
    expect(getStartedCard(view(), { role: "VIEWER" })).toBeNull();
    expect(getStartedCard(view(), { role: "USER" })).not.toBeNull();
  });
});

describe("P3 Get started: hidden for good, and back from Help", () => {
  it("all done: hidden for good once (the mark is stored), and stays hidden if a step opens again", () => {
    expect(shouldHideForGood(view(KEYS))).toBe(true);
    expect(shouldHideForGood(view(["broker"]))).toBe(false);
    const hidden = withStored(view(KEYS), hideGetStarted(view(KEYS).stored, T));
    expect(shouldHideForGood(hidden)).toBe(false);
    expect(getStartedCard(hidden)).toBeNull();
    expect(getStartedCard(view(["broker"], hidden.stored))).toBeNull();        // the broker went: still gone
  });

  it("'Hide' hides an unfinished card; Help brings it back", () => {
    const hidden = hideGetStarted(view(["broker"]).stored, T);
    expect(hidden).toMatchObject({ get_started_hidden_at: T, get_started_shown_at: null });
    expect(getStartedCard(view(["broker"], hidden))).toBeNull();
    const back = showGetStarted(hidden, false, T);
    expect(back).toMatchObject({ get_started_hidden_at: null, get_started_shown_at: null });
    expect(getStartedCard(view(["broker"], back))?.doneCount).toBe(1);
    // later all three are done: it goes for good, as for a card never hidden
    expect(shouldHideForGood(view(KEYS, back))).toBe(true);
  });

  it("Help with every step done shows the three ticks until hidden again", () => {
    const back = showGetStarted(hideGetStarted(view(KEYS).stored, T), true, T);
    const card = getStartedCard(view(KEYS, back));
    expect(card?.complete).toBe(true);
    expect(shouldHideForGood(view(KEYS, back))).toBe(false);
    expect(html(<GetStartedViewV2 card={card!} />)).toContain("All set.");
    const again = hideGetStarted(back, T);
    expect(again.get_started_shown_at).toBeNull();
    expect(getStartedCard(view(KEYS, again))).toBeNull();
  });

  it("keeps the wizard's marks when the card's change", () => {
    const stored = { ...cleanStored(null), opened_at: T, steps: { ...cleanStored(null).steps, risk: { skipped_at: T } } };
    expect(hideGetStarted(stored, T)).toMatchObject({ opened_at: T, steps: { risk: { skipped_at: T } } });
    expect(cleanStored({ get_started_hidden_at: "soon", get_started_shown_at: T })).toMatchObject({ get_started_hidden_at: null, get_started_shown_at: T });
  });
});

describe("P3 nothing about LIVE on the front page in PAPER", () => {
  it("the Get started card never mentions LIVE, MFA, e-mail, the SEBI algo id or risk defaults", () => {
    for (const done of [[], ["broker"], KEYS] as GetStartedKey[][]) {
      const card = getStartedSteps(view(done))!;
      const text = html(<GetStartedViewV2 card={card} />).replace(/<[^>]+>/g, " ");
      expect(text).not.toMatch(/\blive\b|MFA|e-?mail|algo|SEBI|risk|default/i);
    }
  });

  it("PAPER is a deployments list that was read with no LIVE deployment; a failed or pending read is never PAPER", () => {
    expect(paperOnlyFrom([])).toBe(true);
    expect(paperOnlyFrom([{ mode: "PAPER" }])).toBe(true);
    expect(paperOnlyFrom([{ mode: "PAPER" }, { mode: "LIVE" }])).toBe(false);
    for (const unread of [null, undefined, { detail: "Not found" }, "error"]) expect(paperOnlyFrom(unread)).toBe(false);
  });

  it("the top bars leave out the LIVE-paused and e-mail notices on the front page in PAPER only", () => {
    const status = { maintenance_mode: true, maintenance_message: null, disabled_brokers: ["upstox"] } as never;
    const user = { id: 1, email: "a@b.c", tenant_id: 1, role: "OWNER", email_verified: false } as never;
    expect(topBarNotices(status, user).map((n) => n.text)).toEqual(["Maintenance: new trades are paused", "Live trading is paused on upstox", "Verify your email"]);
    expect(topBarNotices(status, user, true).map((n) => n.text)).toEqual(["Maintenance: new trades are paused"]);
    expect(paperOnlySource).toContain("api.listDeployments(true)");              // stopped LIVE deployments count
    expect(shellSource).toMatch(/usePaperOnly\(!!user && key === "dashboard"\)/);
    expect(appSource).toMatch(/usePaperOnly\(!!user && page === "dashboard"\)/);
    expect(appSource).toMatch(/email_verified === false && !paperOnly/);
    expect(appSource).toMatch(/disabled_brokers\.length > 0 && !paperOnly/);
  });

  it("neither dashboard shows the go-live checklist, the Setup chip or the wizard any more", () => {
    for (const src of [dashboardSource, dashboardV2Source]) {
      expect(src).not.toMatch(/import GoLiveChecklist|<GoLiveChecklist/);
      expect(src).toContain("GetStartedCardV2");
    }
    expect(dashboardV2Source).not.toMatch(/SetupChipV2|shouldAutoOpen|SETUP_PATH/);
  });

  it("today's Deployments tile names LIVE only when the trader has a LIVE deployment", () => {
    expect(dashboardSource).toMatch(/anyLive && <>.*\{live\} LIVE/);
  });

  it("Settings: 'Prepare for LIVE' leads to the Go-live checklist under Advanced, and Help offers Show Get started", () => {
    expect(GO_LIVE_PATH).toBe("/settings#go-live");
    expect(settingsSource).toContain("Prepare for LIVE");
    expect(settingsSource).toMatch(/id="go-live"[\s\S]*<GoLiveChecklistV2 \/>/);
    expect(settingsSource).toContain("<ShowGetStartedV2");
    expect(settingsSource).toContain('id="alerts"');
  });
});

// --- the Go-live checklist ----------------------------------------------------------------------------------------------

const item = (key: string, status: ReadinessItem["status"], extra: Partial<ReadinessItem> = {}): ReadinessItem =>
  ({ key, title: `Server title ${key}`, status, detail: "heartbeat 12s ago; 48211 rows", fix: "", link: null, scope: "PAPER", ...extra });

function checklist(items: ReadinessItem[], target = "LIVE"): ReadinessChecklist {
  const summary = { ok: 0, todo: 0, warn: 0, info: 0 };
  for (const i of items) summary[i.status] += 1;
  return { target, ready: !summary.todo, summary, items, checked_at: T, note: "Computed from the platform's own state; 'todo' blocks the target." };
}

describe("P3 Go-live checklist", () => {
  const data = checklist([
    item("worker", "ok"), item("holidays", "ok"), item("kill_switch", "ok"), item("instrument_master", "ok"),
    item("broker_session", "todo", { link: "settings", fix: "Log in to the broker under Settings > Brokers (Upstox: OAuth). Tokens expire every trading day." }),
    item("mfa", "todo", { scope: "LIVE" }),
    item("alerts", "warn", { detail: "TELEGRAM; last error on TELEGRAM: 401" }),
    item("ai_provider", "info", { scope: "OPTIONAL" }),
  ]);

  it("the passing rows fold into 'N checks OK'; only the blockers and warnings are open; optional rows fold away", () => {
    const v = goLiveView(data, "/settings");
    expect(v.ready).toBe(false);
    expect(v.headline).toBe("2 blockers and 1 warning");
    expect(v.open.map((r) => [r.key, r.status])).toEqual([["broker_session", "todo"], ["mfa", "todo"], ["alerts", "warn"]]);
    expect(v.ok).toEqual(["Automatic trading is running", "Exchange holidays loaded", "No kill switch on", "Contract list loaded"]);
    expect(v.optional).toEqual(["AI provider key"]);
    const out = html(<GoLiveListV2 view={v} onRecheck={() => undefined} />);
    expect(out).toContain("4 checks OK");
    expect(out).toContain("1 optional");
    expect((out.match(/<details/g) ?? []).length).toBe(2);
    expect(out).not.toMatch(/<details[^>]* open/);                           // both folds start closed
    expect((out.match(/data-row=/g) ?? []).length).toBe(3);
  });

  it("each open row has one sentence and one button; the broker login row says 'Log in to your broker' and leads to the Broker page", () => {
    const v = goLiveView(data, "/broker");
    for (const r of v.open) {
      expect(r.sentence.split(/(?<=\.)\s+(?=[A-Z])/).length).toBeLessThanOrEqual(2);
      expect(r.action.label).toBeTruthy();
    }
    expect(v.open[0].action).toEqual({ label: "Log in to your broker", to: "/broker" });
    expect(goLiveView(checklist([item("broker_credentials", "todo")]), "/broker").open[0].action).toEqual({ label: "Connect your broker", to: "/broker" });
    expect(goLiveView(checklist([item("worker", "todo")])).open[0]).toMatchObject({ title: "Automatic trading is running",
      sentence: "Automatic trading is not running, so nothing trades by itself. Ask your platform admin." });
    expect(v.open[1]).toMatchObject({ title: "Two-step sign-in on", action: { label: "Open Account", to: "/account" } });
    expect(v.open[2].action).toEqual({ label: "Set up Telegram", to: "/settings#alerts" });
    const out = html(<GoLiveListV2 view={v} onRecheck={() => undefined} />);
    const rows = out.split("data-row=").slice(1);
    for (const r of rows) expect((r.match(/<a |<button/g) ?? []).length).toBe(1);
  });

  it("no developer footer, no internal numbers, no variable names, no raw server text", () => {
    const out = html(<GoLiveListV2 view={goLiveView(data)} onRecheck={() => undefined} />).replace(/<[^>]+>/g, " ");
    expect(out).not.toMatch(/Computed from|todo|heartbeat|rows|\d{2,}|Server title|OAuth|Settings > Brokers|401/);
    const unknown = goLiveView(checklist([item("new_thing", "warn", { fix: "Set NEW_ENV_VAR on the server. Then restart." })]));
    expect(unknown.open[0].sentence).not.toMatch(/NEW_ENV_VAR/);
    expect(unknown.open[0].action).toEqual({ label: "Check again", recheck: true });
    const known = goLiveView(checklist([item("other", "todo", { fix: "Open the page and fix it. More words here.", link: "team" })]));
    expect(known.open[0]).toMatchObject({ sentence: "Open the page and fix it.", action: { label: "Open Team", to: "/team" } });
  });

  it("all passing: ready, nothing open", () => {
    const v = goLiveView(checklist([item("worker", "ok"), item("alerts", "warn")], "PAPER"));
    expect(v.headline).toBe("Ready for paper trading, 1 warning to look at");
    expect(goLiveView(checklist([item("worker", "ok")])).headline).toBe("Ready for LIVE");
    expect(goLiveView(checklist([item("worker", "ok")])).open).toEqual([]);
  });
});
