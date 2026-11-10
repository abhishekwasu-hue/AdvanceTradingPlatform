import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("../api/client", () => ({ request: (...args: unknown[]) => request(...args) }));

import { InviteRequestRow, inviteRequestsApi, type InviteRequestItem } from "./InviteRequestsPanel";
import consoleSource from "./AdminConsoleV2.tsx?raw";

/** M95 PR-3 (M95-Q3): Admin › Users & business › Invite requests. */

const base: InviteRequestItem = {
  id: 4, email: "friend@example.com", note: "A friend of the owner", status: "new", created_at: "2026-11-10T09:00:00Z",
  handled_by: null, handled_at: null,
};
const row = (r: InviteRequestItem) => renderToStaticMarkup(<ul><InviteRequestRow r={r} busy={false} onMark={() => {}} /></ul>);

describe("an invite request", () => {
  it("a new one: the address, the note, 'New' and the two marks", () => {
    const html = row(base);
    expect(html).toContain("friend@example.com");
    expect(html).toContain("A friend of the owner");
    expect(html).toContain(">New</span>");
    expect(html).toContain("Mark invited");
    expect(html).toContain("Dismiss");
  });

  it("a handled one: who and when, no marks", () => {
    const html = row({ ...base, status: "invited", handled_by: "admin@example.com", handled_at: "2026-11-10T10:00:00Z" });
    expect(html).toContain(">Invited</span>");
    expect(html).toContain("by admin@example.com");
    expect(html).not.toContain("Mark invited");
  });

  it("is shown in Users & business", () => {
    expect(consoleSource).toContain("<UsersPanel /><InviteRequestsPanel />");
  });
});

describe("the admin calls", () => {
  beforeEach(() => request.mockReset());

  it("list by status and mark one", async () => {
    request.mockResolvedValue({});
    await inviteRequestsApi.list("new");
    expect(request).toHaveBeenCalledWith("/admin/invite-requests?status=new");
    await inviteRequestsApi.mark(4, "dismissed");
    expect(request).toHaveBeenLastCalledWith("/admin/invite-requests/4", { method: "PATCH", body: JSON.stringify({ status: "dismissed" }) });
  });
});
