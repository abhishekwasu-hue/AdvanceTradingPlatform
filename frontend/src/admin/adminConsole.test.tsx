import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ role: "SUPER_ADMIN" as string | null }));
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: auth.role ? { id: 1, email: "ops@example.com", tenant_id: 1, role: auth.role, scopes: [] } : null, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import AdminPage from "../pages/AdminPage";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import AdminConsoleV2 from "./AdminConsoleV2";
import consoleSource from "./AdminConsoleV2.tsx?raw";
import sectionsSource from "./AdminSectionsV2.tsx?raw";
import apiSource from "./consoleApi.ts?raw";
import { ADMIN_SECTIONS, ago, isAdminSection, maskEmail, orgName } from "./consoleApi";

/** MO3 addition (a): the admin console in sections (ui_v2), its masks, and that a non-admin sees nothing of it. */

const at = (path: string) => renderToStaticMarkup(<MemoryRouter initialEntries={[path]}><AdminConsoleV2 /></MemoryRouter>);

beforeEach(() => { auth.role = "SUPER_ADMIN"; });

describe("who sees the console", () => {
  it.each(["OWNER", "USER", "STRATEGY_CREATOR", "VIEWER", null])("%s gets the plain not-found card, no hint of a console", (role) => {
    auth.role = role;
    const html = at("/admin?section=controls");
    expect(html).toContain("This page does not exist.");
    expect(html).not.toContain("Admin console");
    expect(html).not.toContain("Diagnostics");
    expect(html).not.toContain("kill switch");
  });

  it("a SUPER_ADMIN gets the sections and the Diagnostics button", () => {
    const html = at("/admin");
    for (const s of ADMIN_SECTIONS) expect(html).toContain(`>${s.label.replace("&", "&amp;")}<`);
    expect(html).toContain("data-diagnostics-button");
    expect(html).toContain('data-admin-console="system"');
  });

  it("the v2 shell renders it at /admin", () => {
    expect(PAGE_COMPONENTS_V2.admin).toBeDefined();
  });
});

describe("the sections", () => {
  it("the section is read from the address; an unknown one opens System", () => {
    expect(at("/admin?section=users")).toContain('data-admin-console="users"');
    expect(at("/admin?section=nope")).toContain('data-admin-console="system"');
    expect(isAdminSection("support")).toBe(true);
    expect(isAdminSection("live")).toBe(false);
  });

  it("each section holds its own existing cards and no other section's", () => {
    // ADM-1: Controls shows one group at a time (Safety first); every card is still in one of the groups.
    const controls = at("/admin?section=controls");
    expect(controls).toContain("Global kill switch");
    expect(controls).toContain("Platform controls");
    expect(controls).toContain('data-admin-cards="controls"');
    expect(controls).not.toContain("Tenants (");
    expect(controls).toContain('data-controls-groups="safety"');
    expect(controls).not.toContain("Risk ceilings");
    expect(controls).not.toContain(">Feature flags</div>");
    const limits = at("/admin?section=controls&group=limits");
    expect(limits).toContain("Risk ceilings");
    expect(limits).not.toContain("Global kill switch");
    const flagsGroup = at("/admin?section=controls&group=flags");
    expect(flagsGroup).toContain(">Feature flags</div>");
    expect(flagsGroup).not.toContain("Risk ceilings");
    expect(flagsGroup).not.toContain("Global kill switch");
    expect(at("/admin?section=controls&group=nope")).toContain('data-controls-groups="safety"');
    const system = at("/admin");
    expect(system).toContain('data-admin-cards="system"');
    expect(system).toContain("Incidents (");
    expect(system).not.toContain("Global kill switch");
    const users = at("/admin?section=users");
    expect(users).toContain("Tenants (");
    expect(users).toContain('data-console-card="users"');
    expect(users).toContain('data-console-card="ai-cost"');
    expect(users).not.toContain("Feature flags");
    expect(at("/admin?section=testing")).toContain('data-console-card="testing-tools"');
    const support = at("/admin?section=support&user=7");
    expect(support).toContain('data-console-card="support-user"');
    expect(support).toContain('data-console-card="feedback"');
  });

  it("with ui_v2 off (no section) today's page is unchanged: every card, its own header", () => {
    const html = renderToStaticMarkup(<MemoryRouter><AdminPage /></MemoryRouter>);
    expect(html).toContain("Admin Console");
    for (const card of ["Global kill switch", "Platform controls", "Risk ceilings", "Feature flags", "Incidents (", "Tenants (", "Platform audit trail"]) {
      expect(html).toContain(card);
    }
    expect(html).toContain('data-admin-cards="all"');
  });

  it("the new parts only read: no write call, no LIVE switch", () => {
    expect(apiSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"/);
    for (const src of [consoleSource, sectionsSource]) {
      expect(src).not.toMatch(/adminSetFlag|live_trading"\s*,\s*true|method:/);
    }
    expect(sectionsSource).toContain('on("live_trading")');        // shown, read-only
  });
});

describe("masks", () => {
  it("e-mail addresses keep the first two letters and the domain", () => {
    expect(maskEmail("abhi.wasu@example.com")).toBe("ab*******@example.com");
    expect(maskEmail("a@example.com")).toBe("a*@example.com");
    expect(maskEmail(null)).toBe("-");
    expect(orgName("owner.one@example.com")).toBe("ow*******@example.com");
    expect(orgName("Desk One")).toBe("Desk One");
  });

  it("ago", () => {
    const now = Date.parse("2026-10-22T10:00:00Z");
    expect(ago("2026-10-22T09:59:30Z", now)).toBe("30 s ago");
    expect(ago("2026-10-22T09:00:00Z", now)).toBe("1 h ago");
    expect(ago(null, now)).toBe("never");
  });
});
