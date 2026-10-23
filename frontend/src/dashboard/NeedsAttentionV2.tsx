import { Bell, CircleCheck, LogIn, PauseCircle, ShieldAlert, TriangleAlert, UserCircle2, X, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { resendVerification } from "../ui/verifyEmail";
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
 *
 * Settings redesign D: a row marked `dismissible` (the broker login, a paused bot, the setup-health rows) has a quiet
 * "Hide for today" button next to its action (`onHide`, src/dashboard/dismissToday.ts).
 */

const ICONS: Record<AttentionIcon, LucideIcon> = {
  approval: Zap, login: LogIn, paused: PauseCircle, lockout: ShieldAlert, alert: Bell, warning: TriangleAlert, account: UserCircle2,
};

export interface NeedsAttentionProps {
  rows: AttentionRow[] | null;
  /** The broker the "Log in" row is for, and the login in progress. */
  loginBroker: BrokerTokenInfo | null;
  login: BrokerLogin;
  /** Hides a dismissible row until tomorrow; without it no row can be hidden. */
  onHide?: (id: string) => void;
}

const HIDE = "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors duration-fast hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9";

function Row({ row, primary, loginBroker, login, onHide }: { row: AttentionRow; primary: boolean; loginBroker: BrokerTokenInfo | null; login: BrokerLogin; onHide?: (id: string) => void }) {
  const Icon = ICONS[row.icon];
  const action = row.action;
  const isLogin = !!action && "login" in action;
  const busy = isLogin && loginBroker !== null && login.busy !== null;
  const step = isLogin && login.codeStep ? login.codeStep : null;
  const detail = isLogin && loginBroker ? loginNote(loginBroker) : row.detail;
  const hideable = !!row.dismissible && !!onHide;
  const [resent, setResent] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const resend = () => { setSending(true); void resendVerification().then((t) => { setResent(t); setSending(false); }); };
  const button = action && !step;
  const hide = hideable ? (
    <button type="button" className={HIDE} aria-label="Hide for today" title="Hide for today" data-hide={row.id} onClick={() => onHide?.(row.id)}>
      <X size={18} aria-hidden />
    </button>
  ) : null;
  return (
    <li className="py-space-3 first:pt-0 last:pb-0" data-attention={row.id}>
      <div className="flex flex-col gap-space-3 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-space-3">
          <span aria-hidden className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-control ${row.tone === "warn" ? "bg-warn-tint text-warn" : "bg-surface-2 text-fg-muted"}`}>
            <Icon size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-t15 font-semibold text-fg">{row.title}</p>
            {detail && <p className="mt-px text-t13 text-fg-muted">{detail}</p>}
            {resent && <p role="status" className="mt-px text-t13 text-fg" data-resend-note>{resent}</p>}
          </div>
          {/* A notice with nothing to open: the hide button sits beside the words (no empty line on a phone). */}
          {!button && hide && <span className="-my-space-1 shrink-0">{hide}</span>}
        </div>
        {button ? (
          <div className="flex items-center gap-space-2 sm:ml-auto">
            {(
              "login" in action ? (
                <Button variant={primary ? "primary" : "secondary"} icon={<LogIn size={16} aria-hidden />} loading={busy}
                  disabled={loginBroker === null || login.busy !== null} onClick={() => loginBroker && login.login(loginBroker)}
                  className="h-11 flex-1 px-space-4 text-t14 sm:flex-none md:h-9">
                  {action.label}
                </Button>
              ) : "resend" in action ? (
                <Button variant={primary ? "primary" : "secondary"} loading={sending} disabled={sending} onClick={resend}
                  className="h-11 flex-1 px-space-4 text-t14 sm:flex-none md:h-9">
                  {action.label}
                </Button>
              ) : (
                <Link to={action.to} className={`${primary ? LINK_PRIMARY : LINK_SECONDARY} flex-1 sm:flex-none`}>{action.label}</Link>
              )
            )}
            {hide}
          </div>
        ) : null}
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

export default function NeedsAttentionV2({ rows, loginBroker, login, onHide }: NeedsAttentionProps) {
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
          {rows.map((row, i) => <Row key={row.id} row={row} primary={i === 0} loginBroker={loginBroker} login={login} onHide={onHide} />)}
        </ul>
      )}
    </CardV2>
  );
}
