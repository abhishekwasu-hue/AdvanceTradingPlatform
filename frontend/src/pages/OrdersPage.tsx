import { errorSentence } from "../api/errors";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, DirectionBadge } from "../components/ui";
import type { TradeRecord } from "../types";
import { Badge, PageHeader, SkeletonRows, Table } from "../components/primitives";
import { EmptyStateV2 } from "../dashboard/CardV2";
import { formatDateTimeIST, formatNumber } from "../ui/format";

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
  // M63 I-7: null until the list answers (placeholder rows then, never "No orders yet" before the answer).
  const [trades, setTrades] = useState<TradeRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.listTrades().then(setTrades).catch((e) => setError(errorSentence(e)));
  }, [user]);

  const orders = useMemo(
    () => (trades ?? []).flatMap(tradeToOrders).sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()),
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

      <Card title={trades === null ? "Orders" : `Orders (${orders.length})`}>
        {trades === null ? (error ? null : <SkeletonRows rows={4} label="Loading your orders" />) : <Table
          caption="Orders"
          rows={orders}
          rowKey={(o) => o.key}
          empty={<EmptyStateV2 text="No orders yet - your entry and exit fills show here." />}
          columns={[
            { key: "time", header: "Time", cell: (o) => <span className="text-fg-muted">{formatDateTimeIST(o.time)}</span> },
            { key: "action", header: "Action", cell: (o) => <Badge tone={o.action === "ENTRY" ? "brand" : "neutral"}>{o.action}</Badge> },
            { key: "symbol", header: "Symbol", cell: (o) => <span className="font-medium">{o.symbol}</span> },
            { key: "direction", header: "Direction", cell: (o) => <DirectionBadge direction={o.direction as "LONG" | "SHORT"} /> },
            { key: "strategy", header: "Strategy", cell: (o) => <span className="text-fg-muted">{o.strategy_id}</span> },
            { key: "price", header: "Price", numeric: true, cell: (o) => formatNumber(o.price, 2) },
            { key: "qty", header: "Qty", numeric: true, cell: (o) => o.quantity },
            { key: "reason", header: "Reason", cell: (o) => <span className="text-fg-muted">{o.reason}</span> },
          ]}
        />}
      </Card>
    </div>
  );
}
