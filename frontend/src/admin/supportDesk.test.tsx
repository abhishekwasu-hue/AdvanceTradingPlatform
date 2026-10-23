import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TicketRow, dueLine, hours } from "./SupportDeskV2";
import deskSource from "./SupportDeskV2.tsx?raw";
import consoleSource from "./AdminConsoleV2.tsx?raw";
import type { SupportTicket } from "./supportDeskApi";

/** C12: Admin › Support - tickets with a status, an assignee and the response-time targets (none hard-coded). */

const T = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();
const base: SupportTicket = {
  id: 7, tenant_id: 3, user_id: 9, email: "trader@example.com", message: "The chart does not load", app_version: "0.1.0", screen: "/charts",
  created_at: T(-90), kind: "problem", status: "open", assignee_id: null, assignee_email: null, first_response_at: null, closed_at: null,
  context: { error_id: "req-1", error_screen: "/positions", error_found: true, error_route: "/api/positions", error_status: 503 },
  first_response_due_at: null, first_response_late: null, resolve_due_at: null, resolve_late: null,
};
const row = (t: SupportTicket) => renderToStaticMarkup(<ul><TicketRow t={t} assignees={[{ id: 1, email: "ops@example.com" }]} onChange={() => {}} /></ul>);

describe("a ticket", () => {
  it("shows the status, kind, masked e-mail and the error the user attached", () => {
    const html = row(base);
    expect(html).toContain('data-ticket-status="open"');
    expect(html).toContain("Problem");
    expect(html).toContain("tr****@example.com");
    expect(html).not.toContain("trader@example.com");
    expect(html).toContain("req-1");
    expect(html).toContain("503 /api/positions");
    expect(html).toContain("op*@example.com");      // the assignee list is masked too
  });

  it("an error this server no longer holds says where to look", () => {
    expect(row({ ...base, context: { error_id: "req-2", error_found: false } })).toContain("search the logs");
  });

  it("is late only against a target the owner set", () => {
    expect(dueLine(base)).toBeNull();
    expect(dueLine({ ...base, first_response_due_at: T(-30), first_response_late: true })?.late).toBe(true);
    expect(dueLine({ ...base, first_response_due_at: T(30), first_response_late: false })?.late).toBe(false);
    expect(dueLine({ ...base, first_response_at: T(-60), resolve_due_at: T(-5), resolve_late: true })).toEqual({ text: "Past the time to resolve", late: true });
    expect(dueLine({ ...base, status: "closed", resolve_due_at: T(-5), resolve_late: true })).toBeNull();
    expect(row({ ...base, first_response_due_at: T(-30), first_response_late: true })).toContain("data-ticket-late");
  });

  it("targets read in plain words; none means no target", () => {
    expect(hours(null)).toBe("no target");
    expect(hours(4)).toBe("4 h");
    expect(hours(0.5)).toBe("30 min");
  });
});

describe("wiring", () => {
  it("the Support section shows the tickets and the desk settings; nothing is hard-coded", () => {
    expect(consoleSource).toContain("<SupportTicketsPanel />");
    expect(consoleSource).toContain("<SupportDeskSettingsPanel />");
    expect(deskSource).not.toMatch(/\+91\d|wa\.me\/\d|first_response_hours:\s*\d/);
  });
});
