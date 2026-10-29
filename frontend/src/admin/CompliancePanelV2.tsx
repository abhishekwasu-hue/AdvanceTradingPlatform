import { useEffect, useState } from "react";
import { request } from "../api/client";
import ExportCard from "../components/ExportCard";
import GoLiveChecklist from "../components/GoLiveChecklist";
import { Panel, Pill } from "./AdminSectionsV2";

/** AUD-G1.7 (owner audit 28 Oct; SEBI retail-algo framework, in force 1 Apr 2026): the platform owner's Compliance
 * section - per broker the algo-id tag format and how many organisations registered a static IP, the server's egress IP,
 * the go-live checklist and the audit-trail exports, on one screen. Read only (GET /api/compliance/overview). */
export interface ComplianceBroker {
  broker: string; name: string; available: boolean; generic_algo_id: boolean; orgs_with_static_ip: number;
  tag_format: { max_length: number | null; charset: string };
}
export interface ComplianceOverview {
  server_egress_ip: string | null; static_ip_required_for_live: boolean; ops_within_threshold: boolean; orgs_with_algo_id: number;
  brokers: ComplianceBroker[]; exports: string[];
}

export default function CompliancePanelV2() {
  const [data, setData] = useState<ComplianceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    request<ComplianceOverview>("/compliance/overview").then((d) => { if (live) setData(d); }, (e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);
  return (
    <>
      <Panel title="Algo trading readiness" id="compliance" note="Per broker: the algo-id tag and how many organisations registered a static IP.">
        {!data ? (error ? <p className="text-t12 text-down">Could not load: {error}</p> : <p className="text-t12 text-fg-muted" aria-busy="true">Loading…</p>) : (
          <div className="space-y-space-3 text-t13" data-compliance-overview>
            <ul className="flex flex-wrap gap-space-2">
              <li><Pill tone={data.server_egress_ip ? "ok" : "warn"}>Server IP {data.server_egress_ip ?? "not set"}</Pill></li>
              <li><Pill tone={data.static_ip_required_for_live ? "ok" : "off"}>Static IP for LIVE {data.static_ip_required_for_live ? "required" : "not required"}</Pill></li>
              <li><Pill tone={data.ops_within_threshold ? "ok" : "off"}>Orders per second {data.ops_within_threshold ? "within the threshold" : "throttle off"}</Pill></li>
              <li><Pill tone={data.orgs_with_algo_id ? "ok" : "off"}>{data.orgs_with_algo_id} organisations with an algo id</Pill></li>
            </ul>
            <div className="-mx-space-1 overflow-x-auto px-space-1">
              <table className="w-full text-t12">
                <thead><tr className="text-left text-fg-muted"><th className="py-space-1 pr-space-3 font-medium">Broker</th>
                  <th className="py-space-1 pr-space-3 font-medium">Algo-id tag</th><th className="py-space-1 pr-space-3 font-medium">Generic id</th>
                  <th className="py-space-1 pr-space-3 text-right font-medium">Static IPs</th></tr></thead>
                <tbody>
                  {data.brokers.map((b) => (
                    <tr key={b.broker} data-compliance-broker={b.broker} className="border-t border-border">
                      <td className="py-space-1 pr-space-3 text-fg">{b.name}{b.available ? "" : " (not available)"}</td>
                      <td className="py-space-1 pr-space-3 text-fg-muted">{b.tag_format.max_length ? `${b.tag_format.max_length} chars, ` : ""}{b.tag_format.charset}</td>
                      <td className="py-space-1 pr-space-3">{b.generic_algo_id ? <Pill tone="ok">yes</Pill> : <Pill tone="off">no</Pill>}</td>
                      <td className="py-space-1 pr-space-3 text-right font-tabular text-fg">{b.orgs_with_static_ip}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Panel>
      <GoLiveChecklist kind="platform" />
      <ExportCard scope="platform" />
    </>
  );
}
