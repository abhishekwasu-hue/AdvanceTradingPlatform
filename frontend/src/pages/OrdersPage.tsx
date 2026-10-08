import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, DirectionBadge } from "../components/ui";
import type { TradeRecord } from "../types";
import { Badge, EmptyState, PageHeader, Table } from "../components/primitives";

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
        <PageHeader title="Orders" />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to see your order blotter.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Orders" description="Every entry and exit fill from your paper trades, most recent first - a broker-style order blotter view." />

      {error && <div className="text-sm text-down">{error}</div>}

      <Card title={`Orders (${orders.length})`}>
        <Table
          caption="Orders"
          rows={orders}
          rowKey={(o) => o.key}
          empty={<EmptyState title="No orders yet" body="Execute a signal from the Signals page; its entry and exit fills appear here." />}
          columns={[
            { key: "time", header: "Time", cell: (o) => <span className="text-fg-muted">{new Date(o.time).toLocaleString()}</span> },
            { key: "action", header: "Action", cell: (o) => <Badge tone={o.action === "ENTRY" ? "brand" : "neutral"}>{o.action}</Badge> },
            { key: "symbol", header: "Symbol", cell: (o) => <span className="font-medium">{o.symbol}</span> },
            { key: "direction", header: "Direction", cell: (o) => <DirectionBadge direction={o.direction as "LONG" | "SHORT"} /> },
            { key: "strategy", header: "Strategy", cell: (o) => <span className="text-fg-muted">{o.strategy_id}</span> },
            { key: "price", header: "Price", numeric: true, cell: (o) => o.price.toFixed(2) },
            { key: "qty", header: "Qty", numeric: true, cell: (o) => o.quantity },
            { key: "reason", header: "Reason", cell: (o) => <span className="text-fg-muted">{o.reason}</span> },
          ]}
        />
      </Card>
    </div>
  );
}
