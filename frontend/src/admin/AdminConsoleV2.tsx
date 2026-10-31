import { Stethoscope } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { Sheet, SkeletonRows } from "../components/primitives";
import AdminPage from "../pages/AdminPage";
import type { PageProps } from "../routes";
import { NotFound } from "../shell/common";
import { PageTabs } from "../ui/PageTabs";
import { SubTabs } from "../ui/SubTabs";
import { Details } from "../ui/Details";
import { useFeatures } from "../ui/useFeatures";
import AlertsPanelV2 from "./AlertsPanelV2";
import GrowthSettingsPanelV2 from "./GrowthSettingsPanelV2";
import ChainHistoryCard from "./ChainHistoryCard";
import CompliancePanelV2 from "./CompliancePanelV2";
import { ActivationPanel, AiCostPanel, BUTTON, Phase0Notice, Pill, SupportPanel, SystemPanels, TestingToolsPanel, UsersPanel } from "./AdminSectionsV2";
import { SupportDeskSettingsPanel, SupportTicketsPanel } from "./SupportDeskV2";
import { ADMIN_SECTIONS, CONTROLS_GROUPS, ago, consoleApi, isAdminSection, isControlsGroup, type AdminSection, type ConsoleDiagnostics, type ControlsGroup } from "./consoleApi";

/**
 * MO3 addition (a), behind `ui_v2`: the admin console at `/admin`, in sections - System, Controls, Users & business,
 * Testing tools, Support - and a Diagnostics drawer. The section is in the address (`/admin?section=controls`), so a
 * link or a reload keeps it. Each section shows its new read-only panels (src/admin/AdminSectionsV2.tsx) and then the
 * existing cards that belong to it (pages/AdminPage.tsx with `section`), unchanged.
 *
 * The server is the gate (every `/api/admin/*` route: SUPER_ADMIN + two-factor). Here a non-admin sees the plain "This
 * page does not exist" card, never a hint that a console exists. MO3-A1: Support › one person › "View as this person"
 * opens the app as they see it - a time-limited, audited, read-only session on the server (app/auth/view_as.py) with the
 * banner on top (src/admin/ViewAsBanner.tsx). ADM-1: Controls shows one group at a time - Safety, Risk limits, Trader
 * features, Feature flags (`&group=`).
 */
export default function AdminConsoleV2(_props: PageProps) {
  const { user, loading } = useAuth();
  const [params, setParams] = useSearchParams();
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const growth = useFeatures().on("growth_v2");     // MO4 slice 3: the activation events in Users & business
  const raw = params.get("section");
  const section: AdminSection = isAdminSection(raw) ? raw : "system";
  const rawGroup = params.get("group");
  const group: ControlsGroup = isControlsGroup(rawGroup) ? rawGroup : "safety";   // ADM-1: Controls in four groups
  const userParam = parseInt(params.get("user") ?? "", 10);
  const supportUser = Number.isNaN(userParam) ? null : userParam;

  if (loading) return null;
  if (user?.role !== "SUPER_ADMIN") return <NotFound />;

  const pick = (next: AdminSection) => setParams(next === "system" ? {} : { section: next });
  const current = ADMIN_SECTIONS.find((s) => s.id === section)!;
  return (
    <div className="space-y-space-4" data-admin-console={section}>
      <header className="flex flex-wrap items-start justify-between gap-space-3">
        <div className="min-w-0">
          <h1 className="text-t20 font-semibold text-fg">Admin console</h1>
          <Details id="admin.header" className="mt-space-1"><p>{current.hint}. Every change is audited on the affected organisation's trail.</p></Details>
        </div>
        <button type="button" onClick={() => setDiagnosticsOpen(true)} className={BUTTON} data-diagnostics-button>
          <Stethoscope size={16} aria-hidden />Diagnostics
        </button>
      </header>

      <PageTabs kind="links" label="Admin sections" value={section} onChange={pick}
        items={ADMIN_SECTIONS.map((s) => ({ key: s.id, label: s.label, to: s.id === "system" ? "/admin" : `/admin?section=${s.id}` }))} />

      {section === "system" && <><AlertsPanelV2 /><SystemPanels /><ChainHistoryCard /></>}
      {section === "controls" && (
        <>
          <SubTabs kind="links" label="Control groups" value={group} data={{ "data-controls-groups": group }}
            onChange={(next) => setParams(next === "safety" ? { section: "controls" } : { section: "controls", group: next })}
            items={CONTROLS_GROUPS.map((g) => ({ key: g.id, label: g.label, to: g.id === "safety" ? "/admin?section=controls" : `/admin?section=controls&group=${g.id}` }))} />
          <p className="text-t12 text-fg-muted" data-controls-hint>{CONTROLS_GROUPS.find((g) => g.id === group)!.hint}.</p>
          {group === "safety" && <Phase0Notice />}
        </>
      )}
      {section === "users" && <><UsersPanel />{growth && <ActivationPanel />}<GrowthSettingsPanelV2 /><AiCostPanel /></>}
      {section === "testing" && <TestingToolsPanel />}
      {section === "support" && (
        <>
          <SupportPanel userId={supportUser} onUser={(id) => setParams(id == null ? { section: "support" } : { section: "support", user: String(id) })} />
          {/* C12: the inbox as tickets, then the support contact and the response-time targets. */}
          <SupportTicketsPanel />
          <SupportDeskSettingsPanel />
        </>
      )}
      {section === "compliance" && <CompliancePanelV2 />}
      {section !== "testing" && section !== "support" && section !== "compliance" && <AdminPage section={section} controlsGroup={group} />}

      <Sheet open={diagnosticsOpen} onOpenChange={setDiagnosticsOpen} title="Diagnostics">
        <Details id="admin.diagnostics" className="mt-0 mb-space-3">What already went wrong, newest first. Search the server logs by the request id for the full story.</Details>
        {diagnosticsOpen && <DiagnosticsBody />}
      </Sheet>
    </div>
  );
}

function DiagnosticsBody() {
  const [data, setData] = useState<ConsoleDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    consoleApi.diagnostics().then((d) => { if (live) setData(d); }, (e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);
  if (!data) return error ? <p className="text-t12 text-down">Could not load: {error}</p> : <SkeletonRows rows={2} label="Loading" />;
  const failedDeliveries = Object.values(data.alert_deliveries_failed).reduce((a, n) => a + n, 0);
  return (
    <div className="space-y-space-4 text-t12" data-diagnostics>
      <ul className="flex flex-wrap gap-space-2">
        <li><Pill tone={data.server_errors.length ? "bad" : "ok"}>{data.server_errors.length} server errors</Pill></li>
        <li><Pill tone={failedDeliveries ? "warn" : "ok"}>{failedDeliveries} failed alert deliveries</Pill></li>
        <li><Pill tone={data.failed_sign_ins_24h > 20 ? "warn" : "off"}>{data.failed_sign_ins_24h} failed sign-ins (24 h)</Pill></li>
      </ul>
      <Group title="Server errors (this server process)" empty="No server error since the last restart." items={data.server_errors}
        row={(e) => <><code className="font-mono text-fg">{e.request_id}</code> <span className="text-fg-muted">{e.status} · {e.method} {e.route} · {ago(e.at)}</span></>} />
      <Group title="Worker errors" empty="No worker reports an error." items={data.worker_errors}
        row={(w) => <><span className="text-fg">{w.name.replace(/_/g, " ")}</span> <span className="text-fg-muted">{w.last_error}</span></>} />
      <Group title="Bots with an error" empty="No bot reports an error." items={data.bots_with_errors}
        row={(b) => <><span className="text-fg">#{b.id} org {b.tenant_id} · {b.strategy} · {b.symbol}</span> <span className="text-fg-muted">({b.failures_in_a_row} in a row) {b.error}</span></>} />
      <Group title="Failed AI calls" empty="No failed AI call." items={data.ai_failures}
        row={(a) => <span className="text-fg-muted">{ago(a.at)} · org {a.tenant_id} · {a.task} · {a.provider} · {a.status}</span>} />
      <Group title="Data quality" empty="No data-quality event." items={data.data_quality}
        row={(q) => <span className="text-fg-muted">{ago(q.at)} · {q.kind} · {q.instrument}{q.timeframe ? ` ${q.timeframe}` : ""} · {q.source}</span>} />
    </div>
  );
}

function Group<T>({ title, empty, items, row }: { title: string; empty: string; items: T[]; row: (item: T) => ReactNode }) {
  return (
    <section aria-label={title}>
      <h3 className="text-t14 font-semibold text-fg">{title} <span className="font-normal text-fg-muted">({items.length})</span></h3>
      {items.length === 0 ? <p className="text-fg-muted">{empty}</p> : (
        <ul className="mt-space-1 space-y-space-1">{items.map((item, i) => <li key={i} className="break-words">{row(item)}</li>)}</ul>
      )}
    </section>
  );
}
