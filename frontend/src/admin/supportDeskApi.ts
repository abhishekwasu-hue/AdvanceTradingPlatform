import { request } from "../api/client";
import type { FeedbackItem } from "./consoleApi";

/**
 * C12: Admin › Support's reads and writes - the support tickets and the desk settings (`/api/admin/support/*`,
 * SUPER_ADMIN with two-factor on the server; every change is audited there). Kept out of consoleApi.ts, whose reads
 * never write (adminConsole.test.tsx), like the A2 alerts in opsApi.ts.
 */

/** C12: one message of the support inbox as a ticket (`/api/admin/support/tickets`). */
export type TicketStatus = "open" | "answered" | "closed";
export interface SupportTicket extends Omit<FeedbackItem, "user_agent"> {
  kind: "problem" | "feedback"; status: TicketStatus; assignee_id: number | null; assignee_email: string | null;
  first_response_at: string | null; closed_at: string | null;
  context: { client_time?: string | null; error_id?: string; error_screen?: string | null; error_at?: string | null;
    error_route?: string | null; error_status?: number | null; error_found?: boolean };
  first_response_due_at: string | null; first_response_late: boolean | null; resolve_due_at: string | null; resolve_late: boolean | null;
}
export interface SupportTargets { first_response_hours: number | null; resolve_hours: number | null }
export interface SupportTickets {
  tickets: SupportTicket[]; counts: Record<TicketStatus, number>; late: number; assignees: { id: number; email: string }[]; targets: SupportTargets;
}
export interface SupportDeskSettings extends SupportTargets { whatsapp: string; email: string }

export const supportDeskApi = {
  tickets: (status?: TicketStatus) => request<SupportTickets>(`/admin/support/tickets?limit=100${status ? `&ticket_status=${status}` : ""}`),
  updateTicket: (id: number, change: { status?: TicketStatus; assignee_id?: number | null }) =>
    request<SupportTicket>(`/admin/support/tickets/${id}`, { method: "PUT", body: JSON.stringify(change) }),
  settings: () => request<SupportDeskSettings>("/admin/support/settings"),
  saveSettings: (change: Partial<SupportDeskSettings>) =>
    request<SupportDeskSettings>("/admin/support/settings", { method: "PUT", body: JSON.stringify(change) }),
};
