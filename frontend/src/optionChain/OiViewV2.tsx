import OiAlertsCard from "../components/OiAlertsCard";
import OiBanner from "../components/OiBanner";
import OiHistoryTable from "../components/OiHistoryTable";
import OiSettingsCard from "../components/OiSettingsCard";
import StrikeOiChart from "../components/StrikeOiChart";
import { Details } from "../ui/Details";

/**
 * UI-28 U6.1 (owner, 28 Oct): the "OI" view of the v2 option chain, beside Chain and Charts - the OI banner (state,
 * Put / Call, PCR), the OI history (newest first) and OI by strike, with the banner's settings and alerts folded under
 * one closed "Settings and alerts". The OI components are the ones the old chain page mounts (nothing rebuilt); this
 * file only lays them out in v2 cards.
 */
const CARD = "min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-4";

export default function OiViewV2({ underlying }: { underlying: string }) {
  const u = underlying.trim().toUpperCase();
  return (
    <div className="space-y-space-4" data-oi-view={u}>
      <OiBanner underlying={u} />
      <div className="grid gap-space-4 lg:grid-cols-2">
        <section aria-label={`${u} OI history`} className={CARD} data-card>
          <h3 className="mb-space-2 text-t15 font-semibold text-fg">OI history</h3>
          <div className="overflow-x-auto"><OiHistoryTable underlying={u} /></div>
        </section>
        <section aria-label={`${u} OI by strike`} className={CARD} data-card>
          <h3 className="mb-space-2 text-t15 font-semibold text-fg">OI by strike</h3>
          <StrikeOiChart underlying={u} />
        </section>
      </div>
      <Details id="optionChain.oi.settings" label="Settings and alerts">
        <div className="grid gap-space-4 lg:grid-cols-2">
          <OiSettingsCard underlying={u} />
          <OiAlertsCard underlying={u} />
        </div>
      </Details>
    </div>
  );
}
