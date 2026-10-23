import { apiUrl } from "../api/base";
import { formatEodDate, formatIstTime } from "../components/dataTier";

/** Z10-e (G7): the public status page's data (`GET /api/v1/status`, no login). Pure helpers, no React. The backend
 * sends only states, short public sentences, an EOD date, a lag in seconds and a session count - never an error text,
 * a version, a host or anything about one organisation. */

export type ComponentState = "operational" | "degraded" | "outage" | "unknown";

export interface StatusComponent {
  state: ComponentState;
  summary: string;
}

export interface PublicStatus {
  status: ComponentState;
  checked_at: string;
  components: {
    api: StatusComponent;
    worker: StatusComponent & { seconds_since_heartbeat: number | null };
    data_feed: StatusComponent & {
      market_open: boolean | null;
      eod: StatusComponent & { date: string | null };
      tier_a: { enabled: boolean | null; sessions: number | null; summary: string };
    };
    alerts: StatusComponent & { lag_seconds: number | null };
  };
  banner: {
    maintenance: boolean;
    maintenance_message: string | null;
    /** A2: `note` is the one public sentence the operator wrote for this incident (absent when none). */
    incidents: { impact: "minor" | "major"; state: "investigating" | "monitoring"; started_at: string | null; note?: string }[];
  };
}

export type StatusLoad =
  | { kind: "loading" }
  | { kind: "ok"; status: PublicStatus }
  | { kind: "disabled" }
  | { kind: "unreachable" };

export const STATUS_PATH = apiUrl("/status");
export const REFRESH_MS = 60_000;

/** One fetch of the public status. 200 and 503 both carry a body (503 = the platform's database is not answering);
 * 404 = the operator has not turned the page on; anything else, or no answer at all, reads as unreachable. */
export async function loadPublicStatus(fetcher: typeof fetch = fetch): Promise<StatusLoad> {
  try {
    const response = await fetcher(STATUS_PATH, { credentials: "omit", headers: { Accept: "application/json" } });
    if (response.status === 404) return { kind: "disabled" };
    if (response.status !== 200 && response.status !== 503) return { kind: "unreachable" };
    const body = (await response.json()) as PublicStatus;
    if (!body || typeof body.status !== "string" || !body.components) return { kind: "unreachable" };
    return { kind: "ok", status: body };
  } catch {
    return { kind: "unreachable" };
  }
}

export const STATE_LABEL: Record<ComponentState, string> = {
  operational: "Operational",
  degraded: "Degraded",
  outage: "Outage",
  unknown: "Unknown",
};

/** Text tone per state. The label always says the state in words too, so colour is never the only signal. */
export const STATE_TONE: Record<ComponentState, string> = {
  operational: "text-up",
  degraded: "text-warn",
  outage: "text-down",
  unknown: "text-fg-muted",
};

export function headline(state: ComponentState): string {
  switch (state) {
    case "operational": return "All systems operational";
    case "degraded": return "Some systems are degraded";
    case "outage": return "Part of the platform is down";
    default: return "Status not known yet";
  }
}

/** A2: the headline for the whole page - planned maintenance says so when nothing is worse. */
export function pageHeadline(status: PublicStatus): { text: string; maintenance: boolean } {
  if (status.banner.maintenance && status.status === "operational") return { text: "Planned maintenance: no new entries", maintenance: true };
  return { text: headline(status.status), maintenance: false };
}

export const COMPONENT_NAMES: { key: keyof PublicStatus["components"]; name: string }[] = [
  { key: "api", name: "Website and API" },
  { key: "worker", name: "Trading engine" },
  { key: "data_feed", name: "Market data" },
  { key: "alerts", name: "Alert delivery" },
];

export function incidentText(i: PublicStatus["banner"]["incidents"][number]): string {
  const impact = i.impact === "major" ? "Major incident" : "Incident";
  const state = i.state === "monitoring" ? "a fix is in place and we are monitoring" : "we are investigating";
  const since = i.started_at ? formatIstTime(i.started_at) : null;
  return `${impact}: ${state}${since ? ` (since ${since.replace(/:\d{2} IST$/, " IST")})` : ""}.`;
}

/** "Checked at 10:42 IST". */
export function checkedAt(iso: string): string {
  const t = formatIstTime(iso);
  return t ? `Checked at ${t.replace(/:\d{2} IST$/, " IST")}` : "Checked just now";
}

export function eodLine(date: string | null): string {
  const day = formatEodDate(date);
  return day ? `End-of-day data as of ${day}` : "No end-of-day data yet";
}

export function marketLine(open: boolean | null): string {
  return open === null ? "NSE session: not known" : open ? "NSE session: open" : "NSE session: closed";
}
