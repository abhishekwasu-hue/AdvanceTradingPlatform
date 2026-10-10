import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixture = (name: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), "utf8"));
const ack = fixture("ack");
const actions = fixture("actions");
const actionsProposed = fixture("actions_proposed");
const brief = fixture("brief");
const coach = fixture("coach");
const copilotReply = fixture("copilot_reply");
const drafts = fixture("drafts");
const interviewStart = fixture("interview_start");
const me = fixture("me");
const memory = fixture("memory");
const newsItems = fixture("news_items");
const newsStatus = fixture("news_status");
const notifications = fixture("notifications");
const provider = fixture("provider");
const systemStatus = fixture("system_status");
const thesis = fixture("thesis");
const thesisHistory = fixture("thesis_history");
const thesisReport = fixture("thesis_report");
const interviewPlan = fixture("interview_plan");

type Action = { id: number; status: string; decision_note: string | null; decided_at: string | null } & Record<string, unknown>;

export interface MockOptions {
  ack?: unknown;
  /** Approving an action answers 403 "fresh two-factor check" until POST /auth/mfa/step-up (a LIVE deployment's action). */
  stepUp?: boolean;
}

/** What the page sent - the tests assert on it. */
export interface MockCalls { posts: { url: string; method: string; body: unknown }[] }


/** Answers every /api call from the captured fixtures (a real backend's responses for the demo account). */
export async function mockApi(page: Page, overrides: MockOptions = {}): Promise<MockCalls> {
  const calls: MockCalls = { posts: [] };
  // /ai/actions/*: a small state machine (PROPOSED -> APPROVED / REJECTED), so the lists the page reloads after a
  // decision show it - every action is answered with the exact shape the backend returns.
  const actionsState = new Map<number, Action>();
  for (const a of [...(actions as Action[]), ...(actionsProposed as Action[])]) actionsState.set(a.id, { ...a });
  let stepUpVerified = !overrides.stepUp;
  const table: [RegExp, unknown][] = [
    [/\/auth\/refresh$/, { access_token: "e2e-token", token_type: "bearer" }],
    [/\/auth\/me$/, me],
    [/\/system\/status$/, systemStatus],
    [/\/ai\/acknowledgement$/, overrides.ack ?? ack],
    [/\/ai\/brief/, brief],
    [/\/ai\/market-memory$/, memory],
    [/\/ai\/thesis\/history/, thesisHistory],
    [/\/ai\/thesis\/report/, thesisReport],
    [/\/ai\/thesis\//, thesis],
    [/\/ai\/coach/, coach],
    [/\/ai\/copilot$/, copilotReply],
    [/\/ai\/drafts$/, drafts],
    [/\/ai\/interview\/start$/, interviewStart],
    [/\/ai\/provider$/, provider],
    [/\/ai\/strategist\/parse$/, { symbol: "NIFTY 50", style: "intraday", direction: "both", language: "en", matched: {}, text: "" }],
    [/\/news-feed\/status$/, newsStatus],
    [/\/news-feed\/items/, newsItems],
    [/\/news-feed\/feedback\/mine$/, { verdicts: {} }],
    [/\/news-feed\/feedback\/summary$/, { window_days: 30, trust: { ratings: 0, trust: 1, applied: false, useful: 0, noise: 0, wrong_direction: 0, note: null }, by_source: [], by_category: [] }],
    [/\/notifications/, notifications],
    [/\/telegram\/inbound\/status$/, { configured: false, inbound_enabled: false, allowed_chat_ids: [], approvers: [], has_secret: false, webhook_url: "", telegram_actions: [], note: "" }],
  ];
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const method = request.method();
    const url = request.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, "");   // the client calls /api/v1/...
    const json = (): unknown => { try { return request.postDataJSON(); } catch { return request.postData(); } };   // not every body is JSON
    if (method !== "GET") calls.posts.push({ url, method, body: json() ?? null });
    if (method === "POST" && /\/ai\/acknowledgement$/.test(url)) {
      return route.fulfill({ json: { ...(ack as object), accepted: true } });
    }
    if (method === "POST" && /\/auth\/mfa\/step-up$/.test(url)) {
      stepUpVerified = true;
      return route.fulfill({ status: 204, body: "" });
    }
    const decide = url.match(/^\/ai\/actions\/(\d+)\/(approve|reject)$/);
    if (decide && method === "POST") {
      const action = actionsState.get(Number(decide[1]));
      if (!action) return route.fulfill({ status: 404, json: { detail: "Action not found" } });
      if (action.status !== "PROPOSED") return route.fulfill({ status: 409, json: { detail: `Action is already ${action.status}` } });
      if (decide[2] === "approve" && !stepUpVerified) {
        return route.fulfill({ status: 403, json: { detail: "Approving an action on a LIVE deployment requires a fresh two-factor check on this session - enter your authenticator code" } });
      }
      const note = (json() as { note?: string } | null)?.note ?? null;
      const decided = { ...action, status: decide[2] === "approve" ? "APPROVED" : "REJECTED", decision_note: note, decided_at: "2026-10-08T14:00:00", decided_by: 1 };
      actionsState.set(action.id, decided);
      return route.fulfill({ json: decided });
    }
    if (/^\/ai\/actions(\?|$)/.test(url) && method === "GET") {
      const status = new URL(request.url()).searchParams.get("status");
      const all = [...actionsState.values()].sort((a, b) => b.id - a.id);
      return route.fulfill({ json: status ? all.filter((a) => a.status === status) : all });
    }
    if (method === "PUT" && /\/risk-settings$/.test(url)) return route.fulfill({ json: json() });
    if (method === "POST" && /\/ai\/interview\/plan$/.test(url)) return route.fulfill({ json: interviewPlan });
    if (method === "POST" && /\/ai\/interview\/deploy$/.test(url)) {
      return route.fulfill({ json: { candidate_id: (json() as { candidate_id: number }).candidate_id, mode: "PAPER",
                                     deployment: { id: 77, name: "Interview template", mode: "PAPER", status: "ACTIVE" } } });
    }
    const hit = table.find(([re]) => re.test(url));
    if (hit) return route.fulfill({ json: hit[1] });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  return calls;
}
