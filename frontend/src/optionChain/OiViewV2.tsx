import OiAlertsCard from "../components/OiAlertsCard";
import OiSettingsCard from "../components/OiSettingsCard";
import { Details } from "../ui/Details";
import { OiTab } from "./OiTabV2";

/**
 * UI-28 U6.1 (owner, 28 Oct): the "OI" view of the v2 option chain, beside Chain and Charts.
 * M61 (owner, 30 Oct): one "Data as of HH:MM" for the tab, a numbers-first summary card (PCR, max pain, DTE, the
 * highest call and put OI strikes, a neutral read), the OI history with only the readings that changed, and OI by
 * strike with readable labels - calls and puts in one order and colour (./OiTabV2.tsx, rules in ./oiTab.ts). The
 * banner's settings and alerts stay folded under one closed "Settings and alerts" (the components the old chain page
 * mounts, nothing rebuilt).
 */
export default function OiViewV2({ underlying }: { underlying: string }) {
  const u = underlying.trim().toUpperCase();
  return (
    <div className="space-y-space-4" data-oi-view={u}>
      <OiTab underlying={u} />
      <Details id="optionChain.oi.settings" label="Settings and alerts">
        <div className="grid gap-space-4 lg:grid-cols-2">
          <OiSettingsCard underlying={u} />
          <OiAlertsCard underlying={u} />
        </div>
      </Details>
    </div>
  );
}
