import { request } from "../api/client";

/** A2: Admin > System > Alerts (`/api/admin/ops-alerts`). Plain states and sentences; never a chat id or an address. */
export type OpsState = "ok" | "warn" | "fail" | "unknown";
export interface OpsCheck { key: string; label: string; state: OpsState; detail: string; source: "app" | "server"; since: string | null; alerted: OpsState | null }
export interface OpsAlerts {
  checked_at: string | null;
  source: "server" | "admin" | null;
  checks: OpsCheck[];
  schedule: { server_reported_at: string | null; late: boolean };
  last_message: { at: string; lines: number; delivered: boolean } | null;
  channels: {
    telegram: { available: boolean; connected: boolean; from: "linked" | "env" | "none"; pending: boolean };
    email: { available: boolean; recipients: number };
  };
  error_tracker: { on: boolean };
}
export interface OpsDelivery { telegram: boolean | null; email: boolean | null }

/** The writes live here, apart from the read-only console reads (consoleApi.ts). */
export const opsApi = {
  view: () => request<OpsAlerts>("/admin/ops-alerts"),
  run: () => request<OpsAlerts>("/admin/ops-alerts/run", { method: "POST" }),
  test: () => request<OpsDelivery>("/admin/ops-alerts/test", { method: "POST" }),
  link: () => request<{ url: string; expires_at: string }>("/admin/ops-alerts/telegram", { method: "POST" }),
  unlink: () => request<OpsAlerts>("/admin/ops-alerts/telegram", { method: "DELETE" }),
};
