import { apiUrl } from "../api/base";
import { formatEodDate, formatIstTime } from "../components/dataTier";

/** AUD-T8: uptime per market session, the published service targets and the in-app degradation notice
 * (`GET /api/v1/status/sessions`, no login; flag `status_page`, 404 while off). Pure helpers, no React. The backend sends
 * only states, times, percentages and plain sentences - never a user, a price, a host or an error text. */

export type SessionState = "operational" | "degraded" | "outage" | "unknown" | "closed";

export interface RecentSession {
  date: string;
  opens_at: string;
  closes_at: string;
  in_progress: boolean;
  uptime_pct: number | null;
}

export interface MarketSession {
  key: "NSE" | "MCX" | "CDS" | "CRYPTO";
  name: string;
  open: boolean;
  next_open: string | null;
  state: SessionState;
  recent: RecentSession[];
}

export interface StatusNotice { state: "degraded" | "outage"; text: string }

export interface StatusSessions {
  checked_at: string;
  measured: {
    seconds_since_heartbeat: number | null;
    heartbeat_limit_seconds: number;
    data_fresh: boolean | null;
    measured_since: string | null;
  };
  sessions: MarketSession[];
  slos: { id: string; objective: string; target: string }[];
  notice: StatusNotice | null;
  public_page: boolean;
}

export type SessionsLoad = { kind: "loading" } | { kind: "ok"; body: StatusSessions } | { kind: "off" };

export const SESSIONS_PATH = apiUrl("/status/sessions");
export const NOTICE_REFRESH_MS = 60_000;

/** One read. Anything but a 200 with a body reads as "off": the block and the notice are simply not shown. */
export async function loadStatusSessions(fetcher: typeof fetch = fetch): Promise<SessionsLoad> {
  try {
    const response = await fetcher(SESSIONS_PATH, { credentials: "omit", headers: { Accept: "application/json" } });
    if (response.status !== 200) return { kind: "off" };
    const body = (await response.json()) as StatusSessions;
    if (!body || !Array.isArray(body.sessions) || !body.measured) return { kind: "off" };
    return { kind: "ok", body };
  } catch {
    return { kind: "off" };
  }
}

export const SESSION_LABEL: Record<SessionState, string> = {
  operational: "Open · Operational",
  degraded: "Open · Degraded",
  outage: "Open · Outage",
  unknown: "Open · Not known yet",
  closed: "Closed",
};

/** Text tone per state; the label always says it in words too. */
export const SESSION_TONE: Record<SessionState, string> = {
  operational: "text-up",
  degraded: "text-warn",
  outage: "text-down",
  unknown: "text-fg-muted",
  closed: "text-fg-muted",
};

/** "99.85%", or "Not measured" before the first sample of that session. */
export function uptimeText(pct: number | null): string {
  if (pct === null) return "Not measured";
  return `${Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(2).replace(/0$/, "")}%`;
}

/** The newest session that has begun (in progress or the last one closed). */
export function latestSession(m: MarketSession): RecentSession | null {
  return m.recent[0] ?? null;
}

/** "This session: 99.9%" while open, "Last session (29 Oct 2026): 100%" while closed. */
export function headlineUptime(m: MarketSession): string {
  const s = latestSession(m);
  if (!s) return "No session yet";
  if (s.in_progress) return `This session: ${uptimeText(s.uptime_pct)}`;
  return `Last session (${formatEodDate(s.date) ?? s.date}): ${uptimeText(s.uptime_pct)}`;
}

export function sessionDayLine(s: RecentSession): string {
  const day = formatEodDate(s.date) ?? s.date;
  return `${day}${s.in_progress ? " (in progress)" : ""}: ${uptimeText(s.uptime_pct)}`;
}

function ageText(seconds: number): string {
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
}

/** The one shared line of measurements (said once, not per market). */
export function measuredLine(m: StatusSessions["measured"]): string {
  const engine = m.seconds_since_heartbeat === null ? "The engine has not checked in yet." : `Engine checked in ${ageText(m.seconds_since_heartbeat)}.`;
  const data = m.data_fresh === null ? "" : m.data_fresh ? " Market data on time." : " Market data running late.";
  return `${engine}${data}`;
}

export function nextOpenLine(m: MarketSession): string | null {
  if (m.open || !m.next_open) return null;
  const t = formatIstTime(m.next_open);
  const day = formatEodDate(m.next_open.slice(0, 10));
  return t && day ? `Opens ${day}, ${t.replace(/:\d{2} IST$/, " IST")}` : null;
}
