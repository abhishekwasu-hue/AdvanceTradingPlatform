import { useState, type ReactNode } from "react";
import { AlertRulesPanel } from "../components/AlertRulesPanel";
import { SubTabs } from "../ui/SubTabs";

/**
 * TERM-2 step 3 (owner 30 Oct; TradingView's Account Manager pattern): ONE bottom panel with Positions · Orders · Alerts
 * as tabs. Each tab places the component the app already has - the position list (the same as My trades, with Exit all
 * for PAPER positions), the order list, and the alert rules (the same as the Alerts page). Nothing is copied.
 */
export type AccountTab = "positions" | "orders" | "alerts";

export function AccountPanelV2({ positions, orders, counts = {} }: {
  positions: ReactNode;
  orders: ReactNode;
  counts?: Partial<Record<AccountTab, number>>;
}) {
  const [tab, setTab] = useState<AccountTab>("positions");
  const label = (key: AccountTab, text: string) => {
    const n = counts[key];
    return typeof n === "number" && n > 0 ? <>{text}<span className="ml-space-1 font-tabular text-fg-muted">{n}</span></> : text;
  };
  return (
    <div className="flex h-full min-h-0 flex-col gap-space-2" data-panel-body="account" data-account-tab={tab}>
      <SubTabs label="Positions, orders and alerts" value={tab} onChange={setTab} size="sm" idPrefix="terminal-account"
        items={[{ key: "positions", label: label("positions", "Positions"), data: { "account-tab": "positions" } },
          { key: "orders", label: "Orders", data: { "account-tab": "orders" } },
          { key: "alerts", label: "Alerts", data: { "account-tab": "alerts" } }]} />
      <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel" id={`terminal-account-panel-${tab}`} aria-labelledby={`terminal-account-tab-${tab}`}>
        {tab === "positions" && positions}
        {tab === "orders" && orders}
        {tab === "alerts" && <AlertRulesPanel />}
      </div>
    </div>
  );
}
