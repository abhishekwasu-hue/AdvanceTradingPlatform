import { Bell, CircleCheck, LogIn, PauseCircle, ShieldAlert, TriangleAlert, UserCircle2, Zap, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import { Button } from "../components/primitives";
import { CodeForm, type BrokerLogin } from "../ui/BrokerGate";
import { loginNote } from "../ui/brokerSession";
import type { BrokerTokenInfo } from "../types";
import { CardV2, LINK_PRIMARY, LINK_SECONDARY, LoadingRowsV2 } from "./CardV2";
import type { AttentionIcon, AttentionRow } from "./model";

/**
 * UI2-2 (UI v2 proposal §5 block 4): Needs attention - up to five rows, most urgent first, each with what happened and
 * one action. The first row's action is the page's one primary button; the others are secondary. Nothing waiting: the
 * card collapses to one line, "All clear. Nothing needs you right now."
 *
 * "Log in to <Broker>" runs that broker's existing login here (the broker gate's own login, shared through
 * useBrokerLogin), including the paste-the-code step for Zerodha and Fyers.
 */

const ICONS: Record<AttentionIcon, LucideIcon> = {
  approval: Zap, login: LogIn, paused: PauseCircle, lockout: ShieldAlert, alert: Bell, warning: TriangleAlert, account: UserCircle2,
};

export interface NeedsAttentionProps {
  rows: AttentionRow[] | null;
  /** The broker the "Log in" row is for, and the login in progress. */
  loginBroker: BrokerTokenInfo | null;
  login: BrokerLogin;
}

function Row({ row, primary, loginBroker, login }: { row: AttentionRow; primary: boolean; loginBroker: BrokerTokenInfo | null; login: BrokerLogin }) {
  const Icon = ICONS[row.icon];
  const action = row.action;
  const isLogin = !!action && "login" in action;
  const busy = isLogin && loginBroker !== null && login.busy !== null;
  const step = isLogin && login.codeStep ? login.codeStep : null;
  const detail = isLogin && loginBroker ? loginNote(loginBroker) : row.detail;
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-attention={row.id}>
      <div className="flex flex-col gap-space-3 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-space-3">
          <span aria-hidden className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-control ${row.tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg-muted"}`}>
            <Icon size={18} />
          </span>
          <div className="min-w-0">
            <p className="text-t15 font-semibold text-fg">{row.title}</p>
            {detail && <p className="mt-px text-t13 text-fg-muted">{detail}</p>}
          </div>
        </div>
        {action && !step && (
          "login" in action ? (
            <Button variant={primary ? "primary" : "secondary"} icon={<LogIn size={16} aria-hidden />} loading={busy}
              disabled={loginBroker === null || login.busy !== null} onClick={() => loginBroker && login.login(loginBroker)}
              className="h-11 px-space-4 text-t14 sm:ml-auto md:h-9">
              {action.label}
            </Button>
          ) : (
            <Link to={action.to} className={`${primary ? LINK_PRIMARY : LINK_SECONDARY} sm:ml-auto`}>{action.label}</Link>
          )
        )}
      </div>
      {step && (
        <div className="mt-space-3 rounded-control bg-surface-2 p-space-3 text-t14 text-fg-muted sm:ml-12">
          <CodeForm step={step} onSubmit={login.submitCode} busy={login.busy !== null} />
        </div>
      )}
      {isLogin && login.error && (
        <p role="alert" className="mt-space-2 flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn sm:ml-12">
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" /><span>{login.error}</span>
        </p>
      )}
    </li>
  );
}

export default function NeedsAttentionV2({ rows, loginBroker, login }: NeedsAttentionProps) {
  if (rows !== null && rows.length === 0) {
    return (
      <section aria-label="Needs attention" data-block="attention" data-tour="dashboard-attention"
        className="flex min-h-11 items-center gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 shadow-card md:px-space-5">
        <CircleCheck size={18} aria-hidden className="shrink-0 text-fg-muted" />
        <p className="text-t14 text-fg"><span className="font-semibold">All clear.</span> Nothing needs you right now.</p>
      </section>
    );
  }
  return (
    <CardV2 title="Needs attention" count={rows?.length} testId="attention" tour="dashboard-attention">
      {rows === null ? <LoadingRowsV2 /> : (
        <ul className="divide-y divide-subtle">
          {rows.map((row, i) => <Row key={row.id} row={row} primary={i === 0} loginBroker={loginBroker} login={login} />)}
        </ul>
      )}
    </CardV2>
  );
}
