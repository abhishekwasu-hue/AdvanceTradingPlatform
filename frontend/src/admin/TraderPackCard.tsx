import { CheckCircle2, CircleAlert, CircleDot, CircleOff, Undo2, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { Card } from "../components/ui";

/**
 * Owner decision 23 Oct: "Enable trader features" - one audited, reversible admin action that turns ON every flag a
 * trader needs (backend app/platform/trader_pack.py) and shows, per flag, whether it is ON and can work. It never touches
 * the LIVE switches, the Phase 0 lock, the marketplace, public API, sign-up, automatic paper orders or any flag that waits
 * for an owner decision. "Undo" restores exactly the flag state from before the enable. Admin console v2 (ui_v2) only,
 * Controls section; SUPER_ADMIN + two-factor on the server.
 */
export type TraderPackState = "working" | "needs_setup" | "on" | "off";
export interface TraderPackFlag { name: string; plain: string; on: boolean; state: TraderPackState; check: string }
export interface TraderPackStatus {
  flags: TraderPackFlag[]; all_on: boolean; on_count: number; total: number; can_undo: boolean; enabled_at: string | null;
  changed?: string[]; restored?: string[];
}

export const traderPackApi = {
  status: () => request<TraderPackStatus>("/admin/controls/trader-pack"),
  enable: () => request<TraderPackStatus>("/admin/controls/trader-pack", { method: "POST" }),
  undo: () => request<TraderPackStatus>("/admin/controls/trader-pack/undo", { method: "POST" }),
};

const STATE: Record<TraderPackState, { label: string; tone: string; Icon: typeof CheckCircle2 }> = {
  working: { label: "Working", tone: "text-up", Icon: CheckCircle2 },
  needs_setup: { label: "Needs setup", tone: "text-warn", Icon: CircleAlert },
  on: { label: "On", tone: "text-fg", Icon: CircleDot },
  off: { label: "Off", tone: "text-fg-muted", Icon: CircleOff },
};

/** Needs setup first (what the admin must act on), then off, then the rest in pack order. */
export function orderRows(flags: TraderPackFlag[]): TraderPackFlag[] {
  const rank: Record<TraderPackState, number> = { needs_setup: 0, off: 1, working: 2, on: 3 };
  return flags.map((f, i) => ({ f, i })).sort((a, b) => rank[a.f.state] - rank[b.f.state] || a.i - b.i).map((x) => x.f);
}

export function summary(status: TraderPackStatus): string {
  const setup = status.flags.filter((f) => f.state === "needs_setup").length;
  const head = status.all_on ? "All trader features are on" : `${status.on_count} of ${status.total} trader features are on`;
  return setup ? `${head}; ${setup} need${setup === 1 ? "s" : ""} setup.` : `${head}.`;
}

/** The card's body for a given status (pure, so it is tested without the network). */
export function TraderPackView({ status, busy = false, onEnable, onUndo }: {
  status: TraderPackStatus; busy?: boolean; onEnable?: () => void; onUndo?: () => void;
}) {
  return (
    <div className="space-y-3 text-sm" data-trader-pack>
      <p className="text-xs text-fg-muted">
        Turns on everything a trader uses: the Copilot, strategy templates and the Pine Lab, the screens, the idea library,
        watchlists, the chart tools and the paper account with its risk tools. LIVE trading, the paper-only lock, the
        marketplace, developer API keys, open sign-up, automatic paper orders and every owner decision stay as they are.
        Each change is recorded in the audit log; Undo puts every switch back exactly as it was.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <span data-trader-pack-summary className="font-semibold text-fg">{summary(status)}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button type="button" disabled={busy || status.all_on} onClick={onEnable}
            className="inline-flex items-center gap-1 rounded bg-brand px-3 py-1 text-xs font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50">
            <Zap size={14} aria-hidden /> Enable trader features
          </button>
          <button type="button" disabled={busy || !status.can_undo} onClick={onUndo}
            className="inline-flex items-center gap-1 rounded border border-border px-3 py-1 text-xs text-fg hover:bg-surface-2 disabled:opacity-50">
            <Undo2 size={14} aria-hidden /> Undo
          </button>
        </div>
      </div>
      <ul className="divide-y divide-border border-y border-border">
        {orderRows(status.flags).map((f) => {
          const s = STATE[f.state];
          return (
            <li key={f.name} data-pack-flag={f.name} data-pack-state={f.state}
              className="grid gap-1 py-1.5 text-xs sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-3">
              <div className="min-w-0">
                <div className="font-medium text-fg">{f.plain}</div>
                {f.on && f.state !== "on" && <div className="text-fg-muted">{f.check}</div>}
              </div>
              <span className={`inline-flex items-center gap-1 font-semibold ${s.tone}`}>
                <s.Icon size={14} aria-hidden /> {s.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function TraderPackCard({ onChanged }: { onChanged?: () => void }) {
  const [status, setStatus] = useState<TraderPackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { traderPackApi.status().then(setStatus).catch(() => setError("Could not read the trader features.")); }, []);

  async function run(fn: () => Promise<TraderPackStatus>, done: (s: TraderPackStatus) => string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const next = await fn();
      setStatus(next);
      setNote(done(next));
      onChanged?.();
    } catch {
      setError("That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-console-card="trader-pack" aria-busy={status === null && !error}><Card title="Trader features">
      <div>
        {error && <div className="mb-2 text-xs text-down">{error}</div>}
        {note && <div className="mb-2 text-xs text-up" role="status">{note}</div>}
        {status ? (
          <TraderPackView status={status} busy={busy}
            onEnable={() => void run(traderPackApi.enable, (s) => (s.changed?.length ? `${s.changed.length} features turned on.` : "Everything was already on."))}
            onUndo={() => void run(traderPackApi.undo, (s) => (s.restored?.length ? `${s.restored.length} switches put back as they were.` : "Nothing to undo."))} />
        ) : !error && <p className="text-xs text-fg-muted">Loading…</p>}
      </div>
    </Card></div>
  );
}
