import type { ReadinessChecklist, ReadinessItem } from "../types";
import { ALERTS_PATH, connectBrokerPath } from "./getStarted";

/**
 * P3: the Go-live checklist under Settings → Advanced (linked from "Prepare for LIVE"), in plain words. The rows are
 * the server's (`GET /api/readiness`, backend app/platform/readiness.py); this decides how they read:
 * - the passing rows fold into "N checks OK" (titles only: no internal numbers for an item that is not failing);
 * - the blockers and warnings are open, each with one sentence and one button;
 * - the optional rows fold away under "N optional";
 * - no developer footer (the server's `note`), no environment variable names, no raw detail.
 */

export interface GoLiveRow {
  key: string;
  title: string;
  status: "todo" | "warn";
  sentence: string;
  action: { label: string; to: string } | { label: string; recheck: true };
}

export interface GoLiveView {
  ready: boolean;
  /** "Ready for LIVE", or "2 blockers and 1 warning". */
  headline: string;
  open: GoLiveRow[];
  ok: string[];
  optional: string[];
}

type Plain = { sentence: string; label: string; to?: string };

/** One plain sentence and one button per item the server knows today. `to` absent: the button checks again. */
function plain(key: string, brokerPath: string): Plain | null {
  const broker = { label: "Connect your broker", to: brokerPath };
  switch (key) {
    case "broker_credentials": return { sentence: "Connect your broker so prices and orders can go through it.", ...broker };
    case "broker_session": return { sentence: "Log in to your broker today; the login lasts one trading day.", ...broker };
    case "instrument_master": return { sentence: "Load the list of tradable contracts.", label: "Open Instruments", to: "/instruments" };
    case "worker": return { sentence: "The trading worker is not running, so nothing trades by itself. Ask your platform admin.", label: "Check again" };
    case "holidays": return { sentence: "This year's exchange holidays are not loaded yet. Ask your platform admin.", label: "Check again" };
    case "deployment": return { sentence: "Start a paper bot and let it run for a few sessions first.", label: "Open Autopilot", to: "/deployments" };
    case "deployment_errors": return { sentence: "A bot keeps hitting a problem. Open it to see why.", label: "Open Autopilot", to: "/deployments" };
    case "risk": return { sentence: "Set your capital, risk per trade and daily loss limit.", label: "Open Risk", to: "/risk-management" };
    case "alerts": return { sentence: "Get alerts on your phone so a stopped bot or a kill switch reaches you.", label: "Set up Telegram", to: ALERTS_PATH };
    case "kill_switch": return { sentence: "A kill switch is on. Turn it off once you know why it was used.", label: "Open Risk", to: "/risk-management" };
    case "broker_uncertain": return { sentence: "Your broker's positions do not match ours yet, so new live entries wait.", label: "Open Positions", to: "/positions" };
    case "mfa": return { sentence: "Turn on two-step sign-in for your account.", label: "Open Account", to: "/account" };
    case "email_verified": return { sentence: "Verify your email address from the link we sent you.", label: "Open Account", to: "/account" };
    case "algo_id": return { sentence: "Add your exchange-registered algo ID to your organisation.", label: "Open Team", to: "/team" };
    case "static_ip": return { sentence: "Register this server's IP address with your broker.", label: "Open Settings", to: "/settings" };
    default: return null;
  }
}

/** Plain titles for the items the server knows today (its own titles name internals: "Instrument master synced"). */
const TITLES: Record<string, string> = {
  broker_credentials: "Broker connected", broker_session: "Broker logged in today", instrument_master: "Contract list loaded",
  worker: "Trading worker running", holidays: "Exchange holidays loaded", deployment: "A bot is running",
  deployment_errors: "Bots running without problems", risk: "Risk limits set", alerts: "Alerts reach your phone",
  kill_switch: "No kill switch on", broker_uncertain: "Broker positions match", mfa: "Two-step sign-in on",
  email_verified: "Email verified", algo_id: "Algo ID set", static_ip: "Server IP registered with your broker",
  ai_provider: "AI provider key",
};
const titleOf = (item: ReadinessItem): string => TITLES[item.key] ?? item.title;

/** The page ids the server's `link` may name, as addresses (anything else - an environment variable - is no link). */
const PAGE_LINKS: Record<string, { label: string; to: string }> = {
  settings: { label: "Open Settings", to: "/settings" }, instruments: { label: "Open Instruments", to: "/instruments" },
  deployments: { label: "Open Autopilot", to: "/deployments" }, "risk-management": { label: "Open Risk", to: "/risk-management" },
  positions: { label: "Open Positions", to: "/positions" }, account: { label: "Open Account", to: "/account" }, team: { label: "Open Team", to: "/team" },
};

/** The first sentence of the server's own fix text (an item this app does not know yet), with no variable names. */
function firstSentence(text: string): string {
  const head = (text.split(/(?<=\.)\s/)[0] ?? "").trim();
  return head && !/[A-Z][A-Z0-9]*_[A-Z0-9_]+/.test(head) ? head : "Open the page named here to fix this.";
}

function row(item: ReadinessItem & { status: "todo" | "warn" }, brokerPath: string): GoLiveRow {
  const known = plain(item.key, brokerPath);
  if (known) {
    return { key: item.key, title: titleOf(item), status: item.status, sentence: known.sentence,
      action: known.to ? { label: known.label, to: known.to } : { label: known.label, recheck: true } };
  }
  const page = item.link ? PAGE_LINKS[item.link] : undefined;
  return { key: item.key, title: titleOf(item), status: item.status, sentence: firstSentence(item.fix || item.title),
    action: page ?? { label: "Check again", recheck: true } };
}

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function goLiveView(data: ReadinessChecklist, brokerPath: string = connectBrokerPath()): GoLiveView {
  const blockers = data.items.filter((i): i is ReadinessItem & { status: "todo" } => i.status === "todo");
  const warnings = data.items.filter((i): i is ReadinessItem & { status: "warn" } => i.status === "warn");
  const target = data.target === "LIVE" ? "LIVE" : "paper trading";
  const warned = count(warnings.length, "warning", "warnings");
  const headline = blockers.length
    ? `${count(blockers.length, "blocker", "blockers")}${warnings.length ? ` and ${warned}` : ""}`
    : warnings.length ? `Ready for ${target}, ${warned} to look at` : `Ready for ${target}`;
  return {
    ready: blockers.length === 0,
    headline,
    open: [...blockers, ...warnings].map((i) => row(i, brokerPath)),
    ok: data.items.filter((i) => i.status === "ok").map(titleOf),
    optional: data.items.filter((i) => i.status === "info").map(titleOf),
  };
}

export const checksOk = (n: number): string => `${n} ${n === 1 ? "check" : "checks"} OK`;
