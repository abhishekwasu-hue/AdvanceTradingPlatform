import OiAlertsCard from "../components/OiAlertsCard";
import OiSettingsCard from "../components/OiSettingsCard";
import { Details } from "../ui/Details";
import MultiExpiryV2 from "./MultiExpiryV2";
import OiHistoryV2 from "./OiHistoryV2";
import { OiTab } from "./OiTabV2";

/**
 * UI-28 U6.1 (owner, 28 Oct): the "OI" view of the v2 option chain, beside Chain and Charts.
 * M61 (owner, 30 Oct): one "Data as of HH:MM" for the tab, a numbers-first summary card (PCR, max pain, DTE, the
 * highest call and put OI strikes, a neutral read), the OI history with only the readings that changed, and OI by
 * strike with readable labels - calls and puts in one order and colour (./OiTabV2.tsx, rules in ./oiTab.ts). The
 * banner's settings and alerts stay folded under one closed "Settings and alerts" (the components the old chain page
 * mounts, nothing rebuilt).
 * M96 N1: the expiries side by side (./MultiExpiryV2.tsx) and the OI change between two times with its replay
 * (./OiHistoryV2.tsx), from the user's own data.
 */
export default function OiViewV2({ underlying }: { underlying: string }) {
  const u = underlying.trim().toUpperCase();
  return (
    <div className="space-y-space-4" data-oi-view={u}>
      <OiTab underlying={u} />
      <MultiExpiryV2 underlying={u} />
      <OiHistoryV2 underlying={u} />
      <Details id="optionChain.oi.settings" label="Settings and alerts">
        <div className="grid gap-space-4 lg:grid-cols-2">
          <OiSettingsCard underlying={u} />
          <OiAlertsCard underlying={u} />
        </div>
      </Details>
    </div>
  );
}
