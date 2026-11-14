import { request } from "../api/client";
import { formatNumber } from "../ui/format";

/**
 * Runner S7 (docs/design/USER_RUNNER.md section 7): the data of the "My runner" page - the user's own runners on his own
 * VPS, read from the S3 API (`/api/runner/...`, flag `user_runner` + `ui_v2`) and his `runner` preferences
 * (`/api/me/prefs/runner`). The page only reads and relays HIS choices: ATP never starts, stops or changes a runner
 * (rule R-1); the kill switch stays his, and exits are never blocked (ADR-0004).
 */

export { RUNNER_ADDRESS, RUNNER_FLAG } from "./address";
export { RUNNER_PLAN_FIELD, planAllowsRunner } from "./plan";

export type DeviceStatus = "PAIRED" | "ONLINE" | "OFFLINE" | "REVOKED";

/** One runner as `GET /api/runner/devices` answers it (`service.device_dict`). */
export interface RunnerDevice {
  device_id: string;
  name: string;
  status: DeviceStatus;
  version: string | null;
  paired_at: string | null;
  last_heartbeat_at: string | null;
  revoked_at: string | null;
  run_state: "running" | "paused" | "stopped" | null;
  kill_switch_on: boolean | null;
  open_positions: number | null;
  broker_session: "valid" | "expired" | "none" | null;
  egress_ip: string | null;
  /** Runner S4 (the link): true once ATP can relay HIS signed kill-switch command to this runner. Absent before S4. */
  kill_relay?: boolean;
}

/** His `runner` preferences (backend/app/me/prefs.py RunnerPrefs). */
export interface RunnerSettings {
  mirror_upload: boolean;
  offline_after_minutes: number;
  offline_telegram: boolean;
}

export const DEFAULT_SETTINGS: RunnerSettings = { mirror_upload: true, offline_after_minutes: 3, offline_telegram: true };
export const OFFLINE_MINUTES_MIN = 1;
export const OFFLINE_MINUTES_MAX = 240;

export interface DevicesAnswer { devices: RunnerDevice[]; settings: RunnerSettings }

/** How long a pairing code lives (backend service.PAIRING_CODE_MINUTES); the code's own expiry comes from the server. */
export const PAIRING_MINUTES = 10;

export interface PairingCode { code: string; expires_at: string; expires_in_minutes: number }

export interface MirrorPosition {
  ref: string | null; deployment_ref: string | null; mode: "PAPER" | "LIVE"; symbol: string; exchange: string;
  status: "OPEN" | "CLOSED"; quantity: number; average_price: number | null; last_price: number | null;
  unrealised_pnl: number | null; realised_pnl: number | null; exit_reason: string | null;
  opened_at: string | null; closed_at: string | null; updated_at: string | null;
}

export interface MirrorOrder {
  ref: string | null; deployment_ref: string | null; mode: "PAPER" | "LIVE"; symbol: string; exchange: string;
  side: "BUY" | "SELL"; order_type: string; quantity: number; price: number | null; trigger_price: number | null;
  status: string; filled_quantity: number | null; average_price: number | null; reason: string | null;
  placed_at: string | null; updated_at: string | null;
}

export interface MirrorAnswer { device: RunnerDevice; orders: MirrorOrder[]; positions: MirrorPosition[] }

export const runnerApi = {
  devices: () => request<DevicesAnswer>("/runner/devices"),
  createCode: () => request<PairingCode>("/runner/pairing-codes", { method: "POST" }),
  revoke: (id: string) => request<RunnerDevice>(`/runner/devices/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
  mirror: (id: string) => request<MirrorAnswer>(`/runner/devices/${encodeURIComponent(id)}/mirror`),
  /** S4: relays HIS command to his runner (it is signed for his action there); only offered while `kill_relay` is true. */
  setKill: (id: string, on: boolean) =>
    request<unknown>(`/runner/devices/${encodeURIComponent(id)}/kill-switch`, { method: "POST", body: JSON.stringify({ on }) }),
  saveSettings: (s: RunnerSettings) =>
    request<{ value: RunnerSettings }>("/me/prefs/runner", { method: "PUT", body: JSON.stringify(s) }),
};

// --- the commands he types on his VPS (docs/RUNNER_INSTALL.md) ---------------------------------------------------------

/** The one install line (docs/RUNNER_INSTALL.md step 2), from the latest runner release. */
export const INSTALL_LINE =
  "curl -fsSL https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/releases/latest/download/install.sh -o atp-runner-install.sh && sudo bash atp-runner-install.sh";
/** Pair a runner that is already installed: the installer asks for the code again (install.sh `pair_and_keys`). */
export const PAIR_AGAIN_LINE = "sudo bash atp-runner-install.sh update";
/** His broker keys go into the runner's own keystore (runner S5, `python -m app.runner.keys add angel_one`). */
export const BROKER_KEYS_LINE = "sudo docker exec -it atp-runner python -m app.runner.keys add angel_one";
export const KILL_ON_LINE = "sudo docker exec atp-runner python -m app.runner kill";
export const KILL_OFF_LINE = "sudo docker exec atp-runner python -m app.runner resume";
export const STATUS_LINE = "sudo bash atp-runner-install.sh status";

// --- plain helpers -------------------------------------------------------------------------------------------------------

/** The runners he still has (a removed one is listed apart, under Advanced). */
export function activeDevices(devices: readonly RunnerDevice[]): RunnerDevice[] {
  return devices.filter((d) => d.status !== "REVOKED" && !d.revoked_at);
}

export type Online = "online" | "offline" | "waiting";

export function onlineState(d: RunnerDevice): Online {
  if (d.status === "ONLINE") return "online";
  if (d.status === "OFFLINE") return "offline";
  return "waiting";                                            // paired, nothing reported yet
}

export const ONLINE_WORD: Record<Online, string> = { online: "Online", offline: "Offline", waiting: "Waiting for its first report" };

/** "just now", "4 min ago", "2 h ago", "3 days ago". */
export function ago(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} days ago`;
}

/** The broker login line. */
export function loginLine(d: RunnerDevice): { text: string; ok: boolean | null } {
  switch (d.broker_session) {
    case "valid": return { text: "Broker login: active", ok: true };
    case "expired": return { text: "Broker login: expired - it logs in again by itself, or log in on your runner", ok: false };
    case "none": return { text: "Broker login: no broker keys on the runner yet", ok: false };
    default: return { text: "Broker login: not reported yet", ok: null };
  }
}

export const RUN_WORD: Record<NonNullable<RunnerDevice["run_state"]>, string> = { running: "Running", paused: "Paused", stopped: "Stopped" };

/** Seconds left on a pairing code (never below 0). */
export function secondsLeft(expiresAt: string, now: number): number {
  const t = Date.parse(expiresAt);
  return Number.isNaN(t) ? 0 : Math.max(0, Math.ceil((t - now) / 1000));
}

/** "9:58" */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The offline-alert minutes he typed, or null when it is not a whole number in range. */
export function parseMinutes(text: string): number | null {
  if (!/^\d{1,3}$/.test(text.trim())) return null;
  const n = Number(text.trim());
  return n >= OFFLINE_MINUTES_MIN && n <= OFFLINE_MINUTES_MAX ? n : null;
}

/** "1,23,456.50" (Indian grouping; src/ui/format.ts). */
export function money(v: number | null | undefined): string {
  return formatNumber(v ?? null, 2);
}
