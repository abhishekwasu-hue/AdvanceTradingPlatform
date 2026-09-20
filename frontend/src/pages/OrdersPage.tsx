import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, DirectionBadge } from "../components/ui";
import type { TradeRecord } from "../types";

interface OrderRow {
  key: string;
  time: string;
  action: "ENTRY" | "EXIT";
  symbol: string;
  direction: string;
  strategy_id: string;
  price: number;
  quantity: number;
  reason: string;
}

function tradeToOrders(trade: TradeRecord): OrderRow[] {
  const rows: OrderRow[] = [
    {
      key: `${trade.id}-entry`, time: trade.entry_time, action: "ENTRY", symbol: trade.symbol,
      direction: trade.direction, strategy_id: trade.strategy_id, price: trade.entry_price,
      quantity: trade.quantity, reason: trade.direction === "LONG" ? "Buy to open" : "Sell to open",
    },
  ];
  if (trade.exit_time && trade.exit_price !== null) {
    rows.push({
      key: `${trade.id}-exit`, time: trade.exit_time, action: "EXIT", symbol: trade.symbol,
      direction: trade.direction, strategy_id: trade.strategy_id, price: trade.exit_price,
      quantity: trade.quantity, reason: trade.exit_reason ?? "Closed",
    });
  }
  return rows;
}

export default function OrdersPage() {
  const { user, loading: authLoading } = useAuth();
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listTrades().then(setTrades).catch((e) => setError(String(e)));
  }, [user]);

  const orders = useMemo(
    () => trades.flatMap(tradeToOrders).sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()),
    [trades],
  );

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-orange-400">Orders</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your order blotter.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-orange-400">Orders</h1>
        <p className="text-sm font-semibold text-orange-400/60">
          Every entry and exit fill from your paper trades, most recent first - a broker-style order blotter view.
        </p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <Card title={`Orders (${orders.length})`}>
        {orders.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">No orders yet - execute a signal from the Signals tab.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left">
                  <th className="py-1 pr-3">Time</th>
                  <th className="py-1 pr-3">Action</th>
                  <th className="py-1 pr-3">Symbol</th>
                  <th className="py-1 pr-3">Direction</th>
                  <th className="py-1 pr-3">Strategy</th>
                  <th className="py-1 pr-3">Price</th>
                  <th className="py-1 pr-3">Qty</th>
                  <th className="py-1 pr-3">Reason</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.key} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted">{new Date(o.time).toLocaleString()}</td>
                    <td className="py-1 pr-3">
                      <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${o.action === "ENTRY" ? "border-sky-500/40 text-sky-400" : "border-border text-muted"}`}>
                        {o.action}
                      </span>
                    </td>
                    <td className="py-1 pr-3 font-medium text-slate-200">{o.symbol}</td>
                    <td className="py-1 pr-3">
                      <DirectionBadge direction={o.direction as "LONG" | "SHORT"} />
                    </td>
                    <td className="py-1 pr-3 text-muted">{o.strategy_id}</td>
                    <td className="py-1 pr-3">{o.price.toFixed(2)}</td>
                    <td className="py-1 pr-3">{o.quantity}</td>
                    <td className="py-1 pr-3 text-muted">{o.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
