import { AlertTriangle, Bot, CircleDot, Pause, Play, Plus, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import BrokerTokenBanner from "../components/BrokerTokenBanner";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { Card, StatTile } from "../components/ui";
import {
  BASE_TIMEFRAMES,
  type ContractPreview,
  type CustomStrategyResponse,
  type Deployment,
  type ExecutionMode,
  type ExpiryRule,
  type InstrumentKind,
  type OptionPosition,
  type StrikeRule,
  type StoredBrokerInfo,
  type StrategyInfo,
  type WorkerStatus,
} from "../types";

// Index symbols have no cash leg: picking one switches the form to options (server rejects UNDERLYING on an index).
const INDEX_SYMBOLS = new Set(["NIFTY 50", "NIFTY", "NIFTY BANK", "BANKNIFTY", "NIFTY FIN SERVICE", "FINNIFTY", "NIFTY MID SELECT", "MIDCPNIFTY", "NIFTY NEXT 50", "SENSEX", "BANKEX"]);

function StatusBadge({ status }: { status: Deployment["status"] }) {
  const cls =
    status === "ACTIVE"
      ? "border-accent/40 text-accent bg-accent/10"
      : status === "PAUSED"
        ? "border-warn/40 text-warn bg-warn/10"
        : "border-border text-muted bg-panel2";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-[11px] font-bold tracking-wide ${cls}`}>{status}</span>;
}

function ModeBadge({ mode }: { mode: ExecutionMode }) {
  const cls = mode === "LIVE" ? "border-rose-500/50 text-rose-400 bg-rose-500/10" : "border-sky-500/40 text-sky-400 bg-sky-500/10";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-[11px] font-bold tracking-wide ${cls}`}>{mode}</span>;
}

function ist(iso: string): string {
  return `${new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })} IST`;
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return new Date(iso).toLocaleString();
}

export default function DeploymentsPage() {
  const { user, loading: authLoading } = useAuth();
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [customStrategies, setCustomStrategies] = useState<CustomStrategyResponse[]>([]);
  const [brokers, setBrokers] = useState<StoredBrokerInfo[]>([]);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [includeStopped, setIncludeStopped] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [strategyId, setStrategyId] = useState("");
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [exchange, setExchange] = useState("NSE");
  const [timeframe, setTimeframe] = useState<string>("1min");
  const [mode, setMode] = useState<ExecutionMode>("PAPER");
  const [brokerName, setBrokerName] = useState<string>("");
  const [confirmLive, setConfirmLive] = useState(false);
  const [liveTyped, setLiveTyped] = useState("");
  // Phase F2: what to trade when the strategy signals on the symbol.
  const [kind, setKind] = useState<InstrumentKind>("OPTION");  // the default symbol is an index
  const [position, setPosition] = useState<OptionPosition>("BUY");
  const [expiryRule, setExpiryRule] = useState<ExpiryRule>("NEAREST");
  const [strikeRule, setStrikeRule] = useState<StrikeRule>("ATM");
  const [strikeOffset, setStrikeOffset] = useState(1);
  const [premiumStop, setPremiumStop] = useState<string>("");
  const [maxLots, setMaxLots] = useState<string>("");
  const [spot, setSpot] = useState<string>("");
  const [preview, setPreview] = useState<ContractPreview | null>(null);

  function contractRules() {
    if (kind === "UNDERLYING") return { instrument_kind: kind as InstrumentKind };
    if (kind === "FUTURE") return { instrument_kind: kind as InstrumentKind, expiry_rule: expiryRule, max_lots: maxLots ? Number(maxLots) : null };
    return {
      instrument_kind: kind as InstrumentKind, option_position: position, expiry_rule: expiryRule, strike_rule: strikeRule,
      strike_offset: strikeRule === "ATM" ? 0 : strikeOffset, premium_stop_pct: premiumStop ? Number(premiumStop) : null,
      max_lots: maxLots ? Number(maxLots) : null,
    };
  }

  async function runPreview() {
    setBusy(true); setError(null);
    try {
      setPreview(await api.previewContract({ ...contractRules(), symbol, spot: spot ? Number(spot) : null }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const [stepUp, setStepUp] = useState<{ reason: string; retry: () => Promise<unknown> } | null>(null);

  function refresh() {
    if (!user) return;
    Promise.all([api.listDeployments(includeStopped), api.workerStatus()])
      .then(([d, w]) => {
        setDeployments(d);
        setWorker(w);
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    if (!user) return;
    api.listStrategies().then((s) => {
      setStrategies(s);
      if (!strategyId && s.length) setStrategyId(s[0].id);
    });
    api.listCustomStrategies().then(setCustomStrategies).catch(() => {});
    api.listStoredBrokerCredentials().then((b) => {
      setBrokers(b);
      if (b.length === 1) setBrokerName(b[0].broker_name);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, includeStopped]);

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.id === strategyId) ?? null,
    [strategies, strategyId],
  );

  async function submit() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const created = await api.createDeployment({
        strategy_id: strategyId, symbol, exchange, timeframe, mode, broker_name: brokerName || null, ...contractRules(),
      });
      setMessage(`Deployment #${created.id} is ${created.status}: ${created.strategy_id} on ${created.symbol} (${created.mode}).`);
      setConfirmLive(false);
      setLiveTyped("");
      refresh();
    } catch (e) {
      if (isStepUpError(e)) setStepUp({ reason: String(e).replace(/^Error: 403[^:]*: /, ""), retry: submit });
      else setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function handleCreate() {
    if (mode === "LIVE") {
      setConfirmLive(true);
      return;
    }
    void submit();
  }

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(label);
      refresh();
    } catch (e) {
      if (isStepUpError(e)) setStepUp({ reason: String(e).replace(/^Error: 403[^:]*: /, ""), retry: () => act(label, fn) });
      else setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-green-400">Autopilot</h1>
        <Card>
          <p className="text-sm text-muted">
            Log in from the Account tab to deploy strategies. A deployment tells the background
            trading worker to run a strategy on a symbol every minute - paper or live - while
            every browser is closed.
          </p>
        </Card>
      </div>
    );
  }

  const active = deployments.filter((d) => d.status === "ACTIVE").length;
  const openPositions = deployments.reduce((n, d) => n + d.open_positions, 0);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-green-400">Autopilot</h1>
        <p className="text-sm font-semibold text-green-400/60">
          Strategy deployments the background worker trades on its own: live broker candles every
          minute, the same risk engine and kill switches as a manual execute, exits monitored
          continuously, everything squared off before 15:20 IST.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile
          icon={Bot} label="Trading worker"
          value={worker === null ? "…" : worker.running ? "Running" : "Stopped"}
          tone={worker === null ? "default" : worker.running ? "up" : "down"}
        />
        <StatTile
          icon={CircleDot} label="Market"
          value={worker === null ? "…" : worker.market_open ? "Open" : "Closed"}
          tone={worker === null ? "default" : worker.market_open ? "up" : "default"}
          accentClass="text-slate-300"
        />
        <StatTile icon={Play} label="Active deployments" value={active} accentClass="text-green-400" />
        <StatTile icon={CircleDot} label="Open positions" value={openPositions} accentClass="text-sky-400" />
      </div>

      {worker && !worker.running && (
        <div className="flex items-start gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>
            No worker heartbeat {worker.last_seen_at ? `since ${ist(worker.last_seen_at)}` : "yet"}.
            Nothing is being evaluated or monitored. Start the <code className="font-mono">worker</code> service
            (docker compose up worker) - see docs/OPERATIONS.md.
          </span>
        </div>
      )}
      {worker && worker.running && !worker.market_open && (
        <div className="rounded-lg border border-border bg-panel2 px-3 py-2 text-xs text-muted">
          {worker.market_status}
          {worker.next_market_open && ` - next session opens ${ist(worker.next_market_open)}`}.
          The worker is idle until then.
        </div>
      )}

      {stepUp && (
        <StepUpDialog reason={stepUp.reason} onCancel={() => setStepUp(null)} onVerified={() => { const r = stepUp.retry; setStepUp(null); void r(); }} />
      )}

      <BrokerTokenBanner />

      <Card title="Deploy a strategy">
        <div className="grid sm:grid-cols-6 gap-3 items-end">
          <div className="sm:col-span-2">
            <label className="block text-xs text-muted mb-1">Strategy</label>
            <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              <optgroup label="Inbuilt">
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </optgroup>
              {customStrategies.length > 0 && (
                <optgroup label="My strategies">
                  {customStrategies.map((c) => <option key={c.strategy_id} value={c.strategy_id}>{c.config.name}</option>)}
                </optgroup>
              )}
            </select>
            {selectedStrategy && <div className="text-[11px] text-muted mt-1">Timeframes: {selectedStrategy.timeframes.join(" / ")}</div>}
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Symbol</label>
            <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={symbol} onChange={(e) => {
              const next = e.target.value;
              setSymbol(next);
              setPreview(null);
              if (INDEX_SYMBOLS.has(next.trim().toUpperCase()) && kind === "UNDERLYING") setKind("OPTION");
            }} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Exchange</label>
            <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={exchange} onChange={(e) => setExchange(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Base candles</label>
            <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {BASE_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Broker</label>
            <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm capitalize" value={brokerName} onChange={(e) => setBrokerName(e.target.value)}>
              <option value="">{brokers.length === 1 ? `auto (${brokers[0].broker_name})` : "select…"}</option>
              {brokers.map((b) => <option key={b.broker_name} value={b.broker_name} className="capitalize">{b.broker_name}</option>)}
            </select>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border bg-panel2/40 p-3">
          <div className="grid sm:grid-cols-6 gap-3 items-end">
            <div>
              <label className="block text-xs text-muted mb-1">Trade as</label>
              <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={kind} onChange={(e) => { setKind(e.target.value as InstrumentKind); setPreview(null); }}>
                <option value="UNDERLYING">Underlying (cash)</option>
                <option value="OPTION">Option</option>
                <option value="FUTURE">Future</option>
              </select>
            </div>
            {kind === "OPTION" && (
              <div>
                <label className="block text-xs text-muted mb-1">Position</label>
                <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={position} onChange={(e) => { setPosition(e.target.value as OptionPosition); setPreview(null); }}>
                  <option value="BUY">Buy (LONG→CE, SHORT→PE)</option>
                  <option value="WRITE">Write (LONG→sell PE, SHORT→sell CE)</option>
                </select>
              </div>
            )}
            {kind !== "UNDERLYING" && (
              <div>
                <label className="block text-xs text-muted mb-1">Expiry</label>
                <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={expiryRule} onChange={(e) => { setExpiryRule(e.target.value as ExpiryRule); setPreview(null); }}>
                  <option value="NEAREST">Nearest</option>
                  <option value="NEXT">Next</option>
                  <option value="MONTHLY">Monthly</option>
                </select>
              </div>
            )}
            {kind === "OPTION" && (
              <>
                <div>
                  <label className="block text-xs text-muted mb-1">Strike</label>
                  <div className="flex gap-1">
                    <select className="flex-1 rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strikeRule} onChange={(e) => { setStrikeRule(e.target.value as StrikeRule); setPreview(null); }}>
                      <option value="ATM">ATM</option>
                      <option value="ITM">ITM</option>
                      <option value="OTM">OTM</option>
                    </select>
                    {strikeRule !== "ATM" && (
                      <input type="number" min={1} max={10} className="w-14 rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={strikeOffset} onChange={(e) => setStrikeOffset(Number(e.target.value))} title="strike steps" />
                    )}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">Premium {position === "BUY" ? "stop" : "ceiling"} %</label>
                  <input type="number" min={5} max={95} placeholder={position === "BUY" ? "30" : "50"} className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={premiumStop} onChange={(e) => setPremiumStop(e.target.value)} />
                </div>
              </>
            )}
            {kind !== "UNDERLYING" && (
              <div>
                <label className="block text-xs text-muted mb-1">Max lots</label>
                <input type="number" min={1} max={500} placeholder="risk-based" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={maxLots} onChange={(e) => setMaxLots(e.target.value)} />
              </div>
            )}
          </div>
          {kind !== "UNDERLYING" && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted">
                {kind === "OPTION"
                  ? "The strategy signals on the underlying; at signal time the contract is picked from the instrument master and the spot. Exits follow the strategy's underlying levels, with the premium " + (position === "BUY" ? "stop" : "ceiling") + " as a safety net."
                  : "The strategy signals on the underlying; the future of the chosen expiry is traded in the signal's direction."}
              </span>
              {kind === "OPTION" && (
                <input type="number" placeholder="spot (optional)" className="w-36 rounded bg-panel2 border border-border px-2 py-1 text-xs" value={spot} onChange={(e) => setSpot(e.target.value)} />
              )}
              <button disabled={busy || !symbol} onClick={runPreview} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 disabled:opacity-50">Preview contract</button>
            </div>
          )}
          {preview && preview.contracts && (
            <div className="mt-2 grid sm:grid-cols-2 gap-2 text-xs">
              {(["LONG", "SHORT"] as const).map((dir) => {
                const c = preview.contracts![dir];
                return (
                  <div key={dir} className="rounded border border-border bg-panel2/60 p-2">
                    <div className="text-[10px] uppercase tracking-wide text-muted">{dir} signal → {"error" in c ? "unresolved" : `${c.entry_side} ${c.tradingsymbol}`}</div>
                    {"error" in c ? (
                      <div className="text-warn mt-1">{c.error}</div>
                    ) : (
                      <div className="text-slate-300 mt-1">{c.exchange} · expiry {c.expiry}{c.strike ? ` · strike ${c.strike}` : ""}{c.right ? ` ${c.right}` : ""} · lot {c.lot_size}</div>
                    )}
                  </div>
                );
              })}
              <div className="sm:col-span-2 text-[11px] text-muted">{preview.rules}{preview.spot ? ` · spot ${preview.spot} (${preview.spot_source})` : ""}</div>
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted">Mode</span>
            {(["PAPER", "LIVE"] as ExecutionMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`rounded-md border px-3 py-1 text-xs font-bold tracking-wide ${
                  mode === m
                    ? m === "LIVE" ? "border-rose-500 text-rose-400 bg-rose-500/10" : "border-sky-500 text-sky-400 bg-sky-500/10"
                    : "border-border text-muted hover:text-slate-200"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
          <button
            onClick={handleCreate}
            disabled={busy || !strategyId || !symbol}
            className={`flex items-center gap-1.5 rounded text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50 ${
              mode === "LIVE" ? "bg-rose-600 hover:bg-rose-700" : "bg-brand hover:bg-brand-dim"
            }`}
          >
            <Plus size={14} /> {mode === "LIVE" ? "Deploy LIVE" : "Deploy paper"}
          </button>
          {brokers.length === 0 && (
            <span className="text-xs text-warn">Store broker credentials in Settings first - even paper trading needs a broker session for live candles.</span>
          )}
        </div>

        {confirmLive && (
          <div className="mt-4 rounded-lg border border-rose-500/50 bg-rose-500/10 p-4 space-y-3">
            <div className="flex items-center gap-2 text-rose-400 font-bold text-sm">
              <AlertTriangle size={16} /> This will place real orders with real money.
            </div>
            <ul className="text-xs text-slate-300 list-disc pl-5 space-y-1">
              <li><b>{strategyId}</b> on <b>{symbol}</b> ({exchange}) via <b className="capitalize">{brokerName || "your stored broker"}</b>, every minute during market hours{kind !== "UNDERLYING" ? <> - trading <b>{kind === "OPTION" ? `${position === "BUY" ? "bought" : "written"} options` : "futures"}</b> on it</> : ""}.</li>
              {kind === "OPTION" && position === "WRITE" && <li className="text-rose-300">Written options have open-ended risk until the underlying stop or the premium ceiling exits; margin is blocked at the broker.</li>}
              <li>Every entry passes your Risk Management limits and kill switches; a broker-side stop-loss is placed with each fill.</li>
              <li>You can pause or stop it here at any time; Emergency Exit (Risk Management) flattens everything.</li>
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="rounded bg-panel2 border border-rose-500/40 px-2 py-1.5 text-sm w-40"
                placeholder='type LIVE to confirm'
                value={liveTyped}
                onChange={(e) => setLiveTyped(e.target.value)}
              />
              <button
                onClick={() => void submit()}
                disabled={busy || liveTyped !== "LIVE"}
                className="rounded bg-rose-600 hover:bg-rose-700 text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
              >
                {busy ? "Deploying…" : "Confirm LIVE deployment"}
              </button>
              <button onClick={() => { setConfirmLive(false); setLiveTyped(""); }} className="text-xs text-muted hover:text-slate-200">Cancel</button>
            </div>
          </div>
        )}

        {error && <div className="mt-3 text-sm text-danger">{error}</div>}
        {message && <div className="mt-3 text-sm text-accent">{message}</div>}
      </Card>

      <Card title={`Deployments (${deployments.length})`}>
        <div className="flex justify-end mb-2">
          <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
            <input type="checkbox" checked={includeStopped} onChange={(e) => setIncludeStopped(e.target.checked)} />
            show stopped
          </label>
        </div>
        {deployments.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">Nothing deployed yet. Deploy a strategy above and the worker picks it up on its next cycle.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left">
                  <th className="py-1 pr-3">#</th>
                  <th className="py-1 pr-3">Strategy</th>
                  <th className="py-1 pr-3">Symbol</th>
                  <th className="py-1 pr-3">Mode</th>
                  <th className="py-1 pr-3">Broker</th>
                  <th className="py-1 pr-3">Status</th>
                  <th className="py-1 pr-3">Open</th>
                  <th className="py-1 pr-3">Evaluated</th>
                  <th className="py-1 pr-3">Last signal</th>
                  <th className="py-1 pr-3">Note</th>
                  <th className="py-1 pr-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {deployments.map((d) => (
                  <tr key={d.id} className="border-t border-border align-top">
                    <td className="py-1.5 pr-3 text-muted">{d.id}</td>
                    <td className="py-1.5 pr-3 font-medium text-slate-200">{d.strategy_id}<div className="text-[10px] text-muted">{d.timeframe} base</div></td>
                    <td className="py-1.5 pr-3 text-slate-200">{d.symbol}<div className="text-[10px] text-muted">{d.exchange}{d.instrument_kind !== "UNDERLYING" ? ` · ${d.contract_rules}` : ""}</div></td>
                    <td className="py-1.5 pr-3"><ModeBadge mode={d.mode} /></td>
                    <td className="py-1.5 pr-3 capitalize text-slate-300">{d.broker_name ?? "-"}</td>
                    <td className="py-1.5 pr-3"><StatusBadge status={d.status} /></td>
                    <td className="py-1.5 pr-3 font-tabular text-slate-200">{d.open_positions}</td>
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{ago(d.last_evaluated_at)}</td>
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{ago(d.last_signal_at)}</td>
                    <td className="py-1.5 pr-3 max-w-[16rem]">
                      {d.pause_reason && d.status !== "ACTIVE" && <div className="text-warn">{d.pause_reason}</div>}
                      {d.last_error && <div className="text-muted">{d.last_error}</div>}
                    </td>
                    <td className="py-1.5 pl-3 text-right whitespace-nowrap">
                      {d.status === "ACTIVE" && (
                        <button title="Pause" disabled={busy} onClick={() => act(`Deployment #${d.id} paused.`, () => api.pauseDeployment(d.id, "paused from Autopilot tab"))} className="p-1 text-warn hover:bg-panel2 rounded disabled:opacity-50"><Pause size={14} /></button>
                      )}
                      {d.status === "PAUSED" && (
                        <button title="Resume" disabled={busy} onClick={() => act(`Deployment #${d.id} resumed.`, () => api.resumeDeployment(d.id))} className="p-1 text-accent hover:bg-panel2 rounded disabled:opacity-50"><Play size={14} /></button>
                      )}
                      {d.status !== "STOPPED" && (
                        <button title="Stop (no new trades; open positions stay monitored)" disabled={busy} onClick={() => act(`Deployment #${d.id} stopped.`, () => api.stopDeployment(d.id, "stopped from Autopilot tab"))} className="p-1 text-danger hover:bg-panel2 rounded disabled:opacity-50"><Square size={14} /></button>
                      )}
                      {d.status === "STOPPED" && (
                        <button title="Delete" disabled={busy} onClick={() => act(`Deployment #${d.id} deleted.`, () => api.deleteDeployment(d.id))} className="p-1 text-muted hover:text-danger hover:bg-panel2 rounded disabled:opacity-50"><Trash2 size={14} /></button>
                      )}
                    </td>
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
