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


/** Answers every /api call from the captured fixtures (a real backend's responses for the demo account). */
export async function mockApi(page: Page, overrides: Record<string, unknown> = {}) {
  const table: [RegExp, unknown][] = [
    [/\/auth\/refresh$/, { access_token: "e2e-token", token_type: "bearer" }],
    [/\/auth\/me$/, me],
    [/\/system\/status$/, systemStatus],
    [/\/ai\/acknowledgement$/, overrides.ack ?? ack],
    [/\/ai\/brief/, brief],
    [/\/ai\/actions\?status=PROPOSED/, actionsProposed],
    [/\/ai\/actions/, actions],
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
    const url = route.request().url().replace(/^https?:\/\/[^/]+\/api/, "");
    if (route.request().method() === "POST" && /\/ai\/acknowledgement$/.test(url)) {
      return route.fulfill({ json: { ...(ack as object), accepted: true } });
    }
    const hit = table.find(([re]) => re.test(url));
    if (hit) return route.fulfill({ json: hit[1] });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
}
