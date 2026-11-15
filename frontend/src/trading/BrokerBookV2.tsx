import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { CardV2, ChipV2, EmptyStateV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { useInPlace } from "../inplace/context";
import { brokerRow } from "../proposals/brokerLinks";
import type { BrokerBook, BrokerBookAccount, BrokerBookHolding, BrokerBookOrder, BrokerBookPart, BrokerBookPosition } from "../types";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { formatINR, formatNumber, formatSignedINR, formatTimeIST } from "../ui/format";
import { BUTTON_SECONDARY, NoteV2 } from "./PartsV2";

/**
 * Owner 14 Nov "Ho" (flags `ui_v2` + `broker_book`): "From your broker" - what his OWN broker reports (GET
 * /api/me/broker-book), one list per page: Positions shows the broker's positions, Portfolio its holdings, Orders its
 * order book for today. Read-only: no Exit / Modify here; each broker's block links to its own app ("Trade in your
 * broker app", the #1074 broker table) where one is known. A list the broker refused shows its plain sentence.
 */
const WHAT: Record<BrokerBookPart, string> = { positions: "positions", holdings: "holdings", orders: "orders today" };

export type BrokerBookLoad = { status: "loading" } | { status: "error" } | { status: "ok"; data: BrokerBook };

const pnlClass = (v: number | null) => (v === null || v === 0 ? "text-fg" : v > 0 ? "text-up" : "text-down");
const price = (v: number | null) => (v === null ? null : formatINR(v));
const join = (parts: (string | null | false | undefined)[]) => parts.filter(Boolean).join(" · ");

export function rowsOf(account: BrokerBookAccount, part: BrokerBookPart): number {
  return account[part]?.rows.length ?? 0;
}

export default function BrokerBookV2({ part }: { part: BrokerBookPart }) {
  const inPlace = useInPlace();
  const [load, setLoad] = useState<BrokerBookLoad>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const live = useRef(true);

  const read = useCallback(async (refresh: boolean) => {
    setBusy(true);
    try {
      const data = await api.brokerBook([part], refresh);
      if (live.current) setLoad({ status: "ok", data });
    } catch {
      if (live.current) setLoad((was) => (was.status === "ok" ? was : { status: "error" }));
    } finally {
      if (live.current) setBusy(false);
    }
  }, [part]);

  useEffect(() => {
    live.current = true;
    void read(false);
    return () => { live.current = false; };
  }, [read]);

  return <BrokerBookView part={part} load={load} busy={busy} onRefresh={() => void read(true)} onConnect={() => inPlace.open(CONNECT_BROKER_PATH)} />;
}

/** The card itself (pure; the tests render it as markup). */
export function BrokerBookView({ part, load, busy, onRefresh, onConnect }: {
  part: BrokerBookPart; load: BrokerBookLoad; busy: boolean; onRefresh: () => void; onConnect: () => void;
}) {
  const accounts = load.status === "ok" ? load.data.accounts : [];
  const total = accounts.reduce((n, a) => n + rowsOf(a, part), 0);
  return (
    <CardV2 title="From your broker" count={total} testId="broker-book">
      <div className="mb-space-3 flex flex-wrap items-center justify-between gap-space-2">
        <p className="text-t13 text-fg-muted">Read-only. Make changes in your broker app.</p>
        <button type="button" data-broker-book-refresh onClick={onRefresh} disabled={busy} className={BUTTON_SECONDARY}>
          <RefreshCw size={16} aria-hidden className={busy ? "animate-spin" : undefined} /> Refresh
        </button>
      </div>
      {load.status === "loading" ? <LoadingRowsV2 /> : load.status === "error" ? <LoadFailedV2 what="your broker's list" /> : accounts.length === 0 ? (
        <EmptyStateV2 text={`Connect your broker to see its own ${WHAT[part]} here.`} action={{ label: "Connect your broker", onClick: onConnect }} />
      ) : (
        <div className="space-y-space-4">
          {accounts.map((a) => <AccountBlock key={`${a.broker}-${a.account_label ?? ""}`} account={a} part={part} />)}
        </div>
      )}
    </CardV2>
  );
}

function AccountBlock({ account, part }: { account: BrokerBookAccount; part: BrokerBookPart }) {
  const list = account[part];
  const app = brokerRow(account.broker);
  const name = account.account_label ? `${account.broker_label} · ${account.account_label}` : account.broker_label;
  return (
    <div data-broker-account={account.broker}>
      <div className="mb-space-2 flex flex-wrap items-center justify-between gap-x-space-3 gap-y-space-1">
        <h3 className="text-t14 font-semibold text-fg">{name}</h3>
        {app && (
          <a href={app.home} target="_blank" rel="noopener noreferrer" data-broker-app className="text-t13 font-medium text-brand hover:underline">
            Trade in your broker app
          </a>
        )}
      </div>
      {!list ? null : list.message ? <NoteV2 kind="warn" testId="broker-book-message">{list.message}</NoteV2> : list.rows.length === 0 ? (
        <p className="text-t14 text-fg-muted" data-empty>{`No ${WHAT[part]} at ${account.broker_label}.`}</p>
      ) : (
        <ul className="divide-y divide-subtle">
          {part === "positions" && (list.rows as BrokerBookPosition[]).map((r, i) => <PositionRow key={`${r.symbol}-${r.product}-${i}`} row={r} />)}
          {part === "holdings" && (list.rows as BrokerBookHolding[]).map((r, i) => <HoldingRow key={`${r.symbol}-${i}`} row={r} />)}
          {part === "orders" && (list.rows as BrokerBookOrder[]).map((r, i) => <OrderRow key={`${r.symbol}-${r.placed_at ?? ""}-${i}`} row={r} />)}
        </ul>
      )}
    </div>
  );
}

const ROW = "py-space-3 first:pt-0 last:pb-0";
const LINE1 = "flex flex-wrap items-center gap-space-2";
const SYMBOL = "min-w-0 break-all text-t15 font-semibold text-fg";
const LINE2 = "mt-px font-tabular text-t13 text-fg-muted";

function PositionRow({ row }: { row: BrokerBookPosition }) {
  return (
    <li className={ROW} data-broker-row="position">
      <div className={LINE1}>
        <span className={SYMBOL}>{row.symbol}</span>
        {row.side === "Closed" ? <ChipV2>Closed</ChipV2>
          : <span className={`text-t13 font-medium ${row.side === "Long" ? "text-up" : "text-down"}`}>{row.side}</span>}
        <span className={`ml-auto font-tabular text-t14 font-semibold ${pnlClass(row.pnl)}`}>{row.pnl === null ? "" : formatSignedINR(row.pnl)}</span>
      </div>
      <div className={LINE2}>{join([`Qty ${formatNumber(Math.abs(row.quantity))}`, row.product, row.exchange,
        price(row.average_price) && `avg ${price(row.average_price)}`, price(row.ltp) && `LTP ${price(row.ltp)}`])}</div>
    </li>
  );
}

function HoldingRow({ row }: { row: BrokerBookHolding }) {
  return (
    <li className={ROW} data-broker-row="holding">
      <div className={LINE1}>
        <span className={SYMBOL}>{row.symbol}</span>
        <span className={`ml-auto font-tabular text-t14 font-semibold ${pnlClass(row.pnl)}`}>{row.pnl === null ? "" : formatSignedINR(row.pnl)}</span>
      </div>
      <div className={LINE2}>{join([`Qty ${formatNumber(row.quantity)}`, price(row.average_price) && `avg ${price(row.average_price)}`,
        price(row.ltp) && `LTP ${price(row.ltp)}`, price(row.value) && `value ${price(row.value)}`])}</div>
    </li>
  );
}

function OrderRow({ row }: { row: BrokerBookOrder }) {
  const done = row.average_price !== null && row.filled_quantity > 0;
  return (
    <li className={ROW} data-broker-row="order">
      <div className={LINE1}>
        <span className={SYMBOL}>{row.symbol}</span>
        <ChipV2>{row.side}</ChipV2>
        <ChipV2 kind={row.status === "REJECTED" ? "warn" : "outline"}>{row.status.toLowerCase()}</ChipV2>
        <span className="ml-auto font-tabular text-t13 text-fg-muted">{row.placed_at ? formatTimeIST(row.placed_at) : ""}</span>
      </div>
      <div className={LINE2}>{join([`${formatNumber(row.filled_quantity)} of ${formatNumber(row.quantity)} filled`, row.order_type, row.product,
        done ? `avg ${price(row.average_price)}` : price(row.price) && `at ${price(row.price)}`])}</div>
    </li>
  );
}
