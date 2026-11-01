import { errorSentence } from "../api/errors";
import { AlertTriangle, Bot, CircleDot, Pause, Play, Plus, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { PageProps } from "../routes";
import { useAuth } from "../auth/AuthContext";
import BrokerTokenBanner from "../components/BrokerTokenBanner";
import BrokerUncertainBanner from "../components/BrokerUncertainBanner";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { Card, StatTile } from "../components/ui";
import { PageHeader } from "../components/primitives";
import { Details } from "../ui/Details";
import { FROM_DEFAULTS, formPrefill, stillFromDefaults, useTradingDefaults, type FormPrefill } from "../settings/tradingDefaults";
import { useFeatures } from "../ui/useFeatures";
import { ENTRY_MODE_CHOICES, approvalModeOf, orderDefaultsOf, useTradeSettings, type EntryModeChoice } from "../settings/tradeSettings";
import { EMPTY_LEG, OTM_OFFSET_STRUCTURES, RATIO_STRUCTURES, RULE_STRIKE_STRUCTURES, completeLegs, optionDefaultsOf, optionsMissing, savedLegsOf, type LegDraft } from "../settings/optionDefaults";
import { SaveAsDefault } from "../settings/SaveAsDefault";
import {
  BASE_TIMEFRAMES,
  type BrokerAccount,
  type ContractPreview,
  type ContractRules,
  type CustomStrategyResponse,
  type Deployment,
  type ExecutionMode,
  type ExitRules,
  type ExpiryRule,
  type InstrumentKind,
  type OptionPosition,
  type OptionStrategy,
  type StrikeFilters,
  type StrikeRule,
  type StoredBrokerInfo,
  type StrategyInfo,
  type WorkerStatus,
  DEBIT_STRUCTURES,
  WINGED_STRUCTURES,
  HOLD_FOR_DAYS_LINE,
  canHoldForDays,
  PAYOFF_STRUCTURES,
  WIDTH_STRUCTURES,
  MAX_CUSTOM_LEGS,
  ROUTING_POLICIES,
  type RoutingPolicy,
} from "../types";
import { formatDateTimeIST, formatNumber } from "../ui/format";
import { toast } from "../components/Toast";

// Index symbols have no cash leg: picking one switches the form to options (server rejects UNDERLYING on an index).
const INDEX_SYMBOLS = new Set(["NIFTY 50", "NIFTY", "NIFTY BANK", "BANKNIFTY", "NIFTY FIN SERVICE", "FINNIFTY", "NIFTY MID SELECT", "MIDCPNIFTY", "NIFTY NEXT 50", "SENSEX", "BANKEX"]);

/** Settings part C: the mark next to a field that still shows the value it took from the user's defaults. */
function FromDefaults({ on }: { on: boolean }) {
  return on ? <span className="ml-1 text-xs font-medium text-brand" data-from-defaults>{FROM_DEFAULTS}</span> : null;
}

function StatusBadge({ status }: { status: Deployment["status"] }) {
  const cls =
    status === "ACTIVE"
      ? "border-up/40 text-up bg-up/10"
      : status === "PAUSED"
        ? "border-warn/40 text-warn bg-warn/10"
        : "border-border text-fg-muted bg-surface-2";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-xs font-bold tracking-wide ${cls}`}>{status}</span>;
}

function ModeBadge({ mode }: { mode: ExecutionMode }) {
  const cls = mode === "LIVE" ? "border-down/50 text-down bg-down/10" : "border-border text-fg bg-surface-2";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-xs font-bold tracking-wide ${cls}`}>{mode}</span>;
}

function ist(iso: string): string {
  return formatDateTimeIST(new Date(iso), { seconds: true });
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return formatDateTimeIST(iso);
}

/** UX2 fix 4: `paperOnly` is the v2 Trading bot page's "More ways to trade" - this form only, PAPER only (no mode switch, no
 * worker tiles, no list: the page shows the trader's bots itself); `onCreated` tells it a bot was started. */
export default function DeploymentsPage({ paperOnly = false, onCreated }: PageProps & { paperOnly?: boolean; onCreated?: () => void } = {}) {
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
  // M68 PR-8 (TS1-098): what to trade is his pick - no symbol, exchange or base candles pre-selected. An empty exchange /
  // base is sent as not set: the server takes the symbol's own exchange and the strategy's own smallest candle.
  const [symbol, setSymbol] = useState("");
  const [exchange, setExchange] = useState("");
  const [timeframe, setTimeframe] = useState<string>("");
  const [mode, setMode] = useState<ExecutionMode>("PAPER");
  const [brokerName, setBrokerName] = useState<string>("");
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [accountId, setAccountId] = useState<string>("");
  // Phase T: rule-based account choice at signal time.
  const [routingPolicy, setRoutingPolicy] = useState<RoutingPolicy | "">("");
  const [acrossBrokers, setAcrossBrokers] = useState(false);
  // P0.5 / T5: the entry style - MARKET, or PROTECTED_LIMIT (the fill capped at a band past the signal price).
  // M68 PR-1 (TS1-012): nothing pre-selected - his own Trade settings (Order defaults) when saved, else he picks here.
  const tradeView = useTradeSettings(true);
  const ownSettings = tradeView;                     // M68 PR-4 reads it as tradeView, PR-6 as ownSettings
  const own = orderDefaultsOf(tradeView);
  const [orderStyle, setOrderStyle] = useState<"" | "MARKET" | "PROTECTED_LIMIT">("");
  const [protectionPct, setProtectionPct] = useState("");
  const style = orderStyle || own.botOrderStyle || "";
  const band = protectionPct.trim() !== "" ? Number(protectionPct) : own.protectionPct ?? null;
  const entryMissing = !style ? "Choose how entries are sent (Entry: market or protected limit)."
    : style === "PROTECTED_LIMIT" && (band === null || !Number.isFinite(band)) ? "Enter the protected limit's band %." : null;
  const [confirmLive, setConfirmLive] = useState(false);
  const [liveTyped, setLiveTyped] = useState("");
  // Phase F2: what to trade when the strategy signals on the symbol.
  // M68 PR-4 (TS1-037): nothing pre-selected - his own Trade settings (Options) pre-fill it when saved, else he picks here.
  const [kind, setKind] = useState<InstrumentKind | "">("");
  const [position, setPosition] = useState<OptionPosition | "">("");
  const [expiryRule, setExpiryRule] = useState<ExpiryRule | "">("");
  const [strikeRule, setStrikeRule] = useState<StrikeRule | "">("");
  const [strikeOffset, setStrikeOffset] = useState(0);
  const [premiumStop, setPremiumStop] = useState<string>("");
  const [maxLots, setMaxLots] = useState<string>("");
  const [spot, setSpot] = useState<string>("");
  const [preview, setPreview] = useState<ContractPreview | null>(null);
  // Phase H: multi-leg structure and chain-based strike filters.
  const [structure, setStructure] = useState<OptionStrategy>("SINGLE");
  const [spreadWidth, setSpreadWidth] = useState(0);
  const [ratioShorts, setRatioShorts] = useState(0);     // M68 PR-4 (TS1-042): a ratio spread's sold legs per bought leg
  // Phase U: the free-form leg builder (CUSTOM structure). M68 PR-4 (TS1-038): it starts empty; he adds each leg.
  const [customLegs, setCustomLegs] = useState<LegDraft[]>([]);
  const updateLeg = (i: number, patch: Partial<LegDraft>) => { setCustomLegs((cur) => cur.map((l, j) => (j === i ? { ...l, ...patch } : l))); setPreview(null); };
  const removeLeg = (i: number) => { setCustomLegs((cur) => cur.filter((_, j) => j !== i)); setPreview(null); };
  const addLeg = () => { setCustomLegs((cur) => [...cur, { ...EMPTY_LEG }]); setPreview(null); };
  const [fromOwn, setFromOwn] = useState<Record<string, unknown>>({});
  const ownMark = (key: string, value: unknown) => key in fromOwn && fromOwn[key] === value;
  const optionMissing = optionsMissing({ kind, structure, position, expiryRule, strikeRule, strikeOffset, spreadWidth, ratio: ratioShorts, legs: customLegs },
    { kind: "deploy-kind", position: "deploy-position", expiry: "deploy-expiry", strike: "deploy-strike", offset: "deploy-offset",
      width: "deploy-width", ratio: "deploy-ratio", legs: "deploy-legs" });
  const [targetCredit, setTargetCredit] = useState<string>("");
  const [stopCredit, setStopCredit] = useState<string>("");
  const [underlyingExit, setUnderlyingExit] = useState(false);   // DEF-0: off unless the trader turns it on
  const [holdForDays, setHoldForDays] = useState(false);         // M55 T1: off unless the trader turns it on
  const [showFilters, setShowFilters] = useState(false);
  const [exitTrail, setExitTrail] = useState("");
  const [exitBreakEven, setExitBreakEven] = useState("");
  const [exitMinutes, setExitMinutes] = useState("");
  const [exitAt, setExitAt] = useState("");
  // Settings part C: on the v2 Trading bot page only (paperOnly, so PAPER), the user's own defaults pre-fill this form once;
  // each field that still shows its default is marked, and every field stays editable. No defaults: nothing changes.
  const features = useFeatures();
  // M68 PR-6 (TS1-061): "Ask me first" is his own choice - no organisation flag is needed to offer it.
  const approvalAllowed = true;
  const defaults = useTradingDefaults(paperOnly && !!user && features.on("ui_v2"));
  const [prefill, setPrefill] = useState<FormPrefill>({});
  // M68 PR-6 (TS1-060): how this bot's entries go - his Trade settings (Automation > New entries), else asked here.
  const [entryMode, setEntryMode] = useState<"" | EntryModeChoice>("");
  const ownMode = approvalModeOf(ownSettings);
  // M68 PR-6 (review): only a choice made for THIS bot is stored on it; "" = the bot follows his Trade settings at each entry.
  const entryModeNow = entryMode || ownMode || "";
  const prefilled = useRef(false);
  const marked = <K extends keyof FormPrefill>(key: K, value: FormPrefill[K]) => stillFromDefaults(prefill, key, value);
  const [regimes, setRegimes] = useState<string[]>([]);
  const OI_GATE_OPTIONS: [string, string][] = [
    ["OI_DIFF", "OI diff signal"], ["OI_CONFIRM", "OI confirmation"], ["PCR", "PCR limits"], ["IV_CHANGE", "IV jump"],
    ["SWING_OI", "Swing OI (matrix, PCR, max pain)"], ["OI_WALL", "OI wall at round level"],
  ];
  const REGIME_OPTIONS = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "QUIET"];
  const toggleRegime = (r: string) => setRegimes((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));
  // OI Banner O5: opt-in OI entry gates (checked before new entries only; fail-closed on missing or stale OI data).
  const [oiGates, setOiGates] = useState<string[]>([]);
  const toggleOiGate = (g: string) => setOiGates((cur) => (cur.includes(g) ? cur.filter((x) => x !== g) : [...cur, g]));

  function exitRules(): ExitRules | null {
    const r: ExitRules = {};
    if (exitTrail) r.trailing_stop_pct = Number(exitTrail);
    if (exitBreakEven) r.break_even_at_r = Number(exitBreakEven);
    if (exitMinutes) r.time_exit_minutes = Number(exitMinutes);
    if (exitAt) r.time_exit_at = exitAt;
    return Object.keys(r).length ? r : null;
  }
  const [filters, setFilters] = useState<Record<string, string>>({});

  function strikeFilters(): StrikeFilters | null {
    const out: Record<string, number> = {};
    let any = false;
    ["min_oi", "min_volume", "max_spread_pct", "min_iv_pct", "max_iv_pct", "target_delta", "min_premium", "max_premium"].forEach((k) => {
      if (filters[k] !== undefined && filters[k] !== "") { out[k] = Number(filters[k]); any = true; }
    });
    if (filters.search_steps) out.search_steps = Number(filters.search_steps);
    if (filters.delta_tolerance) out.delta_tolerance = Number(filters.delta_tolerance);     // M68 PR-4 (TS1-043): his own
    return any ? (out as StrikeFilters) : null;
  }

  function contractRules(): ContractRules {
    // M68 PR-4: only what he chose (or his own Trade settings pre-filled) is sent; a field Not set is not sent at all.
    if (kind === "UNDERLYING" || !kind) return { instrument_kind: (kind || "UNDERLYING") as InstrumentKind };
    if (kind === "FUTURE") return { instrument_kind: kind as InstrumentKind, expiry_rule: expiryRule || null, max_lots: maxLots ? Number(maxLots) : null };
    const base: ContractRules = {
      // a structure that names its own strike (a straddle, a condor's out-of-the-money shorts) gets no strike rule
      instrument_kind: kind as InstrumentKind, expiry_rule: expiryRule || null,
      strike_rule: RULE_STRIKE_STRUCTURES.includes(structure) ? strikeRule || null : null,
      strike_offset: OTM_OFFSET_STRUCTURES.includes(structure) ? strikeOffset
        : RULE_STRIKE_STRUCTURES.includes(structure) && (strikeRule === "ITM" || strikeRule === "OTM") ? strikeOffset : 0,
      max_lots: maxLots ? Number(maxLots) : null,
      strike_filters: strikeFilters(), option_strategy: structure,
    };
    if (structure === "SINGLE") {
      return { ...base, option_position: position || null, premium_stop_pct: premiumStop ? Number(premiumStop) : null };
    }
    return {
      ...base, ...(spreadWidth >= 1 ? { spread_width: spreadWidth } : {}), ...(RATIO_STRUCTURES.includes(structure) && ratioShorts >= 1 ? { ratio_shorts: ratioShorts } : {}),
      target_credit_pct: targetCredit ? Number(targetCredit) : null, stop_credit_pct: stopCredit ? Number(stopCredit) : null,
      underlying_breach_exit: underlyingExit || null,
      custom_legs: structure === "CUSTOM" ? completeLegs(customLegs) : null,
    };
  }

  async function runPreview() {
    if (optionMissing) { setError(optionMissing.text); document.getElementById(optionMissing.field)?.focus(); return; }
    setBusy(true); setError(null);
    try {
      setPreview(await api.previewContract({ ...contractRules(), symbol, spot: spot ? Number(spot) : null }));
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }
  const [stepUp, setStepUp] = useState<{ reason: string; retry: () => Promise<unknown> } | null>(null);

  function refresh() {
    if (!user || paperOnly) return;
    Promise.all([api.listDeployments(includeStopped), api.workerStatus()])
      .then(([d, w]) => {
        setDeployments(d);
        setWorker(w);
      })
      .catch((e) => setError(errorSentence(e)));
  }

  useEffect(() => {
    if (!user) return;
    api.listStrategies().then((s) => {
      setStrategies(s);
      if (!strategyId && s.length) setStrategyId(s[0].id);
    });
    api.listCustomStrategies().then(setCustomStrategies).catch((e) => toast.error(errorSentence(e)));
    api.listStoredBrokerCredentials().then((b) => {
      setBrokers(b);
      if (b.length === 1) setBrokerName(b[0].broker_name);
    }).catch((e) => toast.error(errorSentence(e)));
    api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  useEffect(() => {
    if (!defaults || prefilled.current) return;
    prefilled.current = true;
    const p = formPrefill(defaults, approvalAllowed);
    // The form opens on an index symbol, which has no cash leg: "the symbol itself" is not taken there.
    if (p.kind === "UNDERLYING" && INDEX_SYMBOLS.has(symbol.trim().toUpperCase())) delete p.kind;
    if (!Object.keys(p).length) return;
    setPrefill(p);
    if (p.kind) setKind(p.kind);
    if (p.structure) setStructure(p.structure);
    if (p.position) setPosition(p.position);
    if (p.expiryRule) setExpiryRule(p.expiryRule);
    if (p.strikeRule) setStrikeRule(p.strikeRule);
    if (p.strikeOffset !== undefined) setStrikeOffset(p.strikeOffset);
    if (p.premiumStop !== undefined) setPremiumStop(p.premiumStop);
    if (p.maxLots !== undefined) setMaxLots(p.maxLots);
    if (p.spreadWidth !== undefined) setSpreadWidth(p.spreadWidth);
    if (p.targetCredit !== undefined) setTargetCredit(p.targetCredit);
    if (p.stopCredit !== undefined) setStopCredit(p.stopCredit);
    if (p.exitTrail !== undefined) setExitTrail(p.exitTrail);
    if (p.exitBreakEven !== undefined) setExitBreakEven(p.exitBreakEven);
    if (p.exitMinutes !== undefined) setExitMinutes(p.exitMinutes);
    if (p.exitAt !== undefined) setExitAt(p.exitAt);
    if (p.approval) setEntryMode("APPROVAL");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaults]);

  // M68 PR-4 (TS1-037 / 038): his own Trade settings > Options fill a field that is still Not set, once, marked.
  const ownOptions = useMemo(() => optionDefaultsOf(tradeView), [tradeView]);
  const ownApplied = useRef(false);
  useEffect(() => {
    if (!tradeView || ownApplied.current) return;
    ownApplied.current = true;
    const o = ownOptions;
    const marks: Record<string, unknown> = {};
    if (!expiryRule && o.expiryRule) { setExpiryRule(o.expiryRule); marks.expiryRule = o.expiryRule; }
    if (!position && o.position) { setPosition(o.position); marks.position = o.position; }
    if (!strikeRule && o.strikeRule) { setStrikeRule(o.strikeRule); marks.strikeRule = o.strikeRule; }
    if (strikeOffset < 1 && o.strikeOffset) { setStrikeOffset(o.strikeOffset); marks.strikeOffset = o.strikeOffset; }
    if (spreadWidth < 1 && o.spreadWidth) { setSpreadWidth(o.spreadWidth); marks.spreadWidth = o.spreadWidth; }
    if (ratioShorts < 1 && o.ratio) { setRatioShorts(o.ratio); marks.ratio = o.ratio; }
    if (!customLegs.length && o.customLegs?.length) { setCustomLegs(o.customLegs); marks.customLegs = true; }
    setFromOwn(marks);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tradeView]);

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

  // Phase AS: a daily strategy is a swing deployment - daily candles, held overnight (CNC / NRML).
  const swing = !!selectedStrategy && selectedStrategy.timeframes.length === 1 && selectedStrategy.timeframes[0] === "day";
  // M55 T1: "Hold for days" is offered for a defined-risk credit structure only (the API refuses it for anything else).
  const canHold = kind !== "" && canHoldForDays(kind, structure);
  const held = swing || (canHold && holdForDays);

  async function submit() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const created = await api.createDeployment({
        strategy_id: strategyId, symbol, exchange: exchange.trim() || null, timeframe: swing ? "day" : timeframe || null, mode, broker_name: brokerName || null,
        holding: held ? "SWING" : "INTRADAY",
        broker_account_id: accountId ? Number(accountId) : null, exit_rules: exitRules(), regime_filter: regimes.length ? regimes : null, oi_gates: oiGates.length ? oiGates : null, ...contractRules(),
        routing_policy: mode === "LIVE" && routingPolicy ? routingPolicy : null, route_across_brokers: mode === "LIVE" && acrossBrokers,
        order_style: style || undefined, market_protection_pct: style === "PROTECTED_LIMIT" ? band : null,
        ...(entryMode ? { entry_mode: entryMode } : {}),
      });
      setMessage(paperOnly ? `Started on paper: ${created.symbol}. See it under Your bots.`
        : `Deployment #${created.id} is ${created.status}: ${created.strategy_id} on ${created.symbol} (${created.mode}).`);
      onCreated?.();
      setConfirmLive(false);
      setLiveTyped("");
      refresh();
    } catch (e) {
      if (isStepUpError(e)) setStepUp({ reason: String(e), retry: submit });
      else setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  function handleCreate() {
    if (entryMissing) { setError(entryMissing); document.getElementById("deploy-entry-style")?.focus(); return; }
    if (optionMissing) { setError(optionMissing.text); document.getElementById(optionMissing.field)?.focus(); return; }
    if (!entryModeNow) { setError("Choose how this bot's entries go (New entries)."); document.getElementById("deploy-entry-mode")?.focus(); return; }
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
      if (isStepUpError(e)) setStepUp({ reason: String(e), retry: () => act(label, fn) });
      else setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Autopilot" />
        <Card>
          <p className="text-sm text-fg-muted">
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
      {!paperOnly && <>
      <PageHeader title="Autopilot" description="Strategy deployments the background worker trades on its own: live broker candles every minute, the same risk engine and kill switches as a manual execute, exits monitored continuously, everything squared off before 15:20 IST." />

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
        />
        <StatTile icon={Play} label="Active deployments" value={active} />
        <StatTile icon={CircleDot} label="Open positions" value={openPositions} />
      </div>

      {worker && !worker.running && (
        <div className="flex items-start gap-2 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>
            No worker heartbeat {worker.last_seen_at ? `since ${ist(worker.last_seen_at)}` : "yet"}.
            Nothing is being evaluated or monitored.{" "}
            {user?.role === "SUPER_ADMIN"
              ? <>Start the <code className="font-mono">worker</code> service (see the operations guide).</>
              : "Ask your platform operator to start the trading worker."}
          </span>
        </div>
      )}
      {worker && worker.running && !worker.market_open && (
        <div className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          {worker.market_status}
          {worker.next_market_open && ` - next session opens ${ist(worker.next_market_open)}`}.
          The worker is idle until then.
        </div>
      )}

      {stepUp && (
        <StepUpDialog reason={stepUp.reason} onCancel={() => setStepUp(null)} onVerified={() => { const r = stepUp.retry; setStepUp(null); void r(); }} />
      )}

      <BrokerTokenBanner />
      <BrokerUncertainBanner />
      </>}

      <Card title={paperOnly ? "More ways to trade" : "Deploy a strategy"}>
        <div className="grid sm:grid-cols-6 gap-3 items-end">
          <div className="sm:col-span-2">
            <label className="block text-xs text-fg-muted mb-1">Strategy</label>
            <select className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              <optgroup label="Inbuilt">
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </optgroup>
              {customStrategies.length > 0 && (
                <optgroup label="My strategies">
                  {customStrategies.map((c) => <option key={c.strategy_id} value={c.strategy_id}>{c.config.name}</option>)}
                </optgroup>
              )}
            </select>
            {selectedStrategy && <div className="text-xs text-fg-muted mt-1">Timeframes: {selectedStrategy.timeframes.join(" / ")}</div>}
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Symbol</label>
            <input className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={symbol} placeholder="Choose a symbol" onChange={(e) => {
              const next = e.target.value;
              setSymbol(next);
              setPreview(null);
              if (INDEX_SYMBOLS.has(next.trim().toUpperCase()) && kind === "UNDERLYING") setKind("OPTION");
            }} />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Exchange</label>
            <input className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={exchange} placeholder="From the symbol" onChange={(e) => setExchange(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Base candles</label>
            {swing ? (
              <div className="rounded border border-warn/40 bg-warn/10 px-2 py-1.5 text-xs text-warn">day · swing: held overnight (CNC / NRML), never squared off at 15:15</div>
            ) : (
              <select className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                <option value="">From the strategy</option>
                {BASE_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            )}
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Broker</label>
            <select className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm capitalize" value={brokerName} onChange={(e) => setBrokerName(e.target.value)}>
              <option value="">{brokers.length === 1 ? `auto (${brokers[0].broker_name})` : "select…"}</option>
              {Array.from(new Set(brokers.map((b) => b.broker_name))).map((b) => <option key={b} value={b} className="capitalize">{b}</option>)}
            </select>
          </div>
        </div>
        {mode === "LIVE" && accounts.filter((a) => !brokerName || a.broker_name === brokerName).length > 1 && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-fg-muted">Account</span>
            <select className="rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">default account</option>
              {accounts.filter((a) => !brokerName || a.broker_name === brokerName).map((a) => (
                <option key={a.id} value={a.id} disabled={a.status !== "ACTIVE"}>
                  #{a.id} {a.display_name ?? `${a.broker_name} ${a.account_label}`}{a.is_default ? " ★" : ""}{a.status !== "ACTIVE" ? " (disabled)" : ""}
                </option>
              ))}
            </select>
            <span className="text-fg-muted ml-2">Routing</span>
            <select className="rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={routingPolicy} onChange={(e) => setRoutingPolicy(e.target.value as RoutingPolicy | "")}
              title={ROUTING_POLICIES.find((p) => p.value === routingPolicy)?.help ?? "Organisation default (Team page)"}>
              <option value="">organisation default</option>
              {ROUTING_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            {routingPolicy && routingPolicy !== "EXPLICIT" && (
              <label className="flex items-center gap-1 text-fg-muted">
                <input type="checkbox" checked={acrossBrokers} onChange={(e) => setAcrossBrokers(e.target.checked)} /> across brokers
              </label>
            )}
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs" data-deploy="entry-style">
          <label htmlFor="deploy-entry-style" className="text-fg-muted">Entry</label>
          <select id="deploy-entry-style" className="rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={style}
            onChange={(e) => setOrderStyle(e.target.value as "" | "MARKET" | "PROTECTED_LIMIT")} aria-invalid={!style || undefined}
            title="MARKET: fills at whatever the book offers. Protected limit: a marketable limit a small band past the signal price; an unfilled remainder is cancelled.">
            <option value="">Not set - choose</option>
            <option value="MARKET">market</option>
            <option value="PROTECTED_LIMIT">protected limit</option>
          </select>
          {!orderStyle && own.botOrderStyle && <span className="text-brand">{FROM_DEFAULTS}</span>}
          <label htmlFor="deploy-entry-mode" className="text-fg-muted ml-2">New entries</label>
          <select id="deploy-entry-mode" className="rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={entryMode}
            onChange={(e) => setEntryMode(e.target.value as "" | EntryModeChoice)} aria-invalid={!entryModeNow || undefined} data-deploy="entry-mode">
            <option value="">{ownMode ? `Follow my Trade settings (${ENTRY_MODE_CHOICES.find((c) => c.value === ownMode)?.label})`
              : "Not set - choose"}</option>
            {ENTRY_MODE_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label} (this bot only)</option>)}
          </select>
          <FromDefaults on={prefill.approval === true && entryMode === "APPROVAL"} />
          {style === "PROTECTED_LIMIT" && (
            <label className="flex items-center gap-1 text-fg-muted">
              band
              <input className="w-14 rounded bg-surface-2 border border-border px-1 py-0.5 text-xs" value={protectionPct}
                placeholder={own.protectionPct !== undefined ? String(own.protectionPct) : "Not set"} onChange={(e) => setProtectionPct(e.target.value)} inputMode="decimal" /> %
            </label>
          )}
        </div>

        <div className="mt-3 rounded-lg border border-border bg-surface-2/40 p-3">
          <div className="grid sm:grid-cols-6 gap-3 items-end">
            <div>
              <label className="block text-xs text-fg-muted mb-1">Trade as<FromDefaults on={marked("kind", kind || undefined)} /></label>
              <select id="deploy-kind" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={kind} aria-invalid={!kind || undefined} onChange={(e) => { setKind(e.target.value as InstrumentKind | ""); setPreview(null); }}>
                <option value="">Not set - choose</option>
                <option value="UNDERLYING">Underlying (cash)</option>
                <option value="OPTION">Option</option>
                <option value="FUTURE">Future</option>
              </select>
            </div>
            {kind === "OPTION" && (
              <div>
                <label className="block text-xs text-fg-muted mb-1">Structure<FromDefaults on={marked("structure", structure as FormPrefill["structure"])} /></label>
                <select className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={structure} onChange={(e) => { setStructure(e.target.value as OptionStrategy); setPreview(null); }}>
                  <option value="SINGLE">Single option</option>
                  <option value="BULL_PUT_SPREAD">Bull put spread (LONG)</option>
                  <option value="BEAR_CALL_SPREAD">Bear call spread (SHORT)</option>
                  <option value="IRON_CONDOR">Iron condor (either)</option>
                  <option value="IRON_BUTTERFLY">Iron butterfly (either)</option>
                  <option value="SHORT_STRADDLE">Short straddle (either, undefined risk)</option>
                  <option value="SHORT_STRANGLE">Short strangle (either, undefined risk)</option>
                  <option value="LONG_STRADDLE">Long straddle (either, debit)</option>
                  <option value="LONG_STRANGLE">Long strangle (either, debit)</option>
                  <option value="CALENDAR_SPREAD">Calendar spread (either, debit)</option>
                  <option value="CALL_RATIO_SPREAD">Call ratio spread (LONG, undefined risk above)</option>
                  <option value="PUT_RATIO_SPREAD">Put ratio spread (SHORT, undefined risk below)</option>
                  <option value="LONG_BUTTERFLY">Long butterfly 1:2:1 (either, debit)</option>
                  <option value="CUSTOM">Custom legs (builder)</option>
                </select>
              </div>
            )}
            {kind === "OPTION" && structure === "SINGLE" && (
              <div>
                <label htmlFor="deploy-position" className="block text-xs text-fg-muted mb-1">Position<FromDefaults on={marked("position", position as OptionPosition) || ownMark("position", position)} /></label>
                <select id="deploy-position" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={position} aria-invalid={!position || undefined} onChange={(e) => { setPosition(e.target.value as OptionPosition | ""); setPreview(null); }}>
                  <option value="">Not set - choose</option>
                  <option value="BUY">Buy (LONG→CE, SHORT→PE)</option>
                  <option value="WRITE">Write (LONG→sell PE, SHORT→sell CE)</option>
                </select>
              </div>
            )}
            {(kind === "OPTION" || kind === "FUTURE") && (
              <div>
                <label htmlFor="deploy-expiry" className="block text-xs text-fg-muted mb-1">Expiry<FromDefaults on={marked("expiryRule", expiryRule as ExpiryRule) || ownMark("expiryRule", expiryRule)} /></label>
                <select id="deploy-expiry" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={expiryRule} aria-invalid={!expiryRule || undefined} onChange={(e) => { setExpiryRule(e.target.value as ExpiryRule | ""); setPreview(null); }}>
                  <option value="">Not set - choose</option>
                  <option value="NEAREST">Nearest</option>
                  <option value="NEXT">Next</option>
                  <option value="MONTHLY">Monthly</option>
                </select>
              </div>
            )}
            {kind === "OPTION" && (
              <>
                {OTM_OFFSET_STRUCTURES.includes(structure) ? (
                  <div>
                    <label htmlFor="deploy-offset" className="block text-xs text-fg-muted mb-1">Short strikes out of the money<FromDefaults on={ownMark("strikeOffset", strikeOffset)} /></label>
                    <input id="deploy-offset" type="number" min={1} placeholder="Not set" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                      value={strikeOffset || ""} onChange={(e) => { setStrikeOffset(Number(e.target.value) || 0); setPreview(null); }} />
                  </div>
                ) : RULE_STRIKE_STRUCTURES.includes(structure) && (
                <div>
                  <label htmlFor="deploy-strike" className="block text-xs text-fg-muted mb-1">Strike<FromDefaults on={marked("strikeRule", strikeRule as StrikeRule) || marked("strikeOffset", strikeOffset) || ownMark("strikeRule", strikeRule) || ownMark("strikeOffset", strikeOffset)} /></label>
                  <div className="flex gap-1">
                    <select id="deploy-strike" className="flex-1 rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={strikeRule}
                      aria-invalid={(!strikeRule && RULE_STRIKE_STRUCTURES.includes(structure)) || undefined} onChange={(e) => { setStrikeRule(e.target.value as StrikeRule | ""); setPreview(null); }}>
                      <option value="">Not set - choose</option>
                      <option value="ATM">ATM</option>
                      <option value="ITM">ITM</option>
                      <option value="OTM">OTM</option>
                    </select>
                    {(strikeRule === "ITM" || strikeRule === "OTM") && (
                      <input id="deploy-offset" type="number" min={1} placeholder="Not set" aria-label="Strikes away from ATM" className="w-16 rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                        value={strikeOffset || ""} onChange={(e) => setStrikeOffset(Number(e.target.value) || 0)} title="strike steps" />
                    )}
                  </div>
                </div>
                )}
                {structure === "SINGLE" ? (
                  <div>
                    <label className="block text-xs text-fg-muted mb-1">Premium {position === "BUY" ? "stop" : "ceiling"} %<FromDefaults on={marked("premiumStop", premiumStop)} /></label>
                    <input type="number" min={5} max={95} placeholder={position === "BUY" ? "30" : "50"} className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={premiumStop} onChange={(e) => setPremiumStop(e.target.value)} />
                  </div>
                ) : (
                  <>
                    {WIDTH_STRUCTURES.includes(structure) && (
                      <div>
                        <label htmlFor="deploy-width" className="block text-xs text-fg-muted mb-1">{WINGED_STRUCTURES.includes(structure) || structure === "LONG_BUTTERFLY" ? "Wing width (steps)" : "Short strike distance (steps)"}<FromDefaults on={marked("spreadWidth", spreadWidth) || ownMark("spreadWidth", spreadWidth)} /></label>
                        <input id="deploy-width" type="number" min={1} placeholder="Not set" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={spreadWidth || ""} onChange={(e) => { setSpreadWidth(Number(e.target.value) || 0); setPreview(null); }} />
                      </div>
                    )}
                    {RATIO_STRUCTURES.includes(structure) && (
                      <div>
                        <label htmlFor="deploy-ratio" className="block text-xs text-fg-muted mb-1">Sold per bought leg<FromDefaults on={ownMark("ratio", ratioShorts)} /></label>
                        <input id="deploy-ratio" type="number" min={1} placeholder="Not set" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={ratioShorts || ""} onChange={(e) => { setRatioShorts(Number(e.target.value) || 0); setPreview(null); }} />
                      </div>
                    )}
                    <div>
                      <label className="block text-xs text-fg-muted mb-1">Target / stop (% of {PAYOFF_STRUCTURES.includes(structure) ? "max profit / risk basis" : DEBIT_STRUCTURES.includes(structure) ? "debit" : "credit"})<FromDefaults on={marked("targetCredit", targetCredit) || marked("stopCredit", stopCredit)} /></label>
                      <div className="flex gap-1">
                        <input type="number" min={5} max={95} placeholder="Not set" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={targetCredit} onChange={(e) => setTargetCredit(e.target.value)}
                          title={DEBIT_STRUCTURES.includes(structure) ? "take profit once the structure is worth this % more than the debit" : "take profit once this % of the credit is captured"} />
                        <input type="number" min={10} max={DEBIT_STRUCTURES.includes(structure) ? 100 : 500} placeholder="Not set" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={stopCredit} onChange={(e) => setStopCredit(e.target.value)}
                          title={DEBIT_STRUCTURES.includes(structure) ? "stop when this % of the debit is lost (max 100)" : "stop when the loss reaches this % of the credit"} />
                      </div>
                      <p className="mt-1 text-xs text-fg-muted">Blank = no target / stop.</p>
                    </div>
                    <label className="flex items-center gap-2 text-sm" data-field="underlying-exit">
                      <input type="checkbox" checked={underlyingExit} onChange={(e) => setUnderlyingExit(e.target.checked)} />
                      Close when the underlying crosses a short strike or a breakeven ({underlyingExit ? "on" : "off"})
                    </label>
                    {canHold && !swing && (
                      <label className="flex items-start gap-2 text-sm" data-field="hold-for-days">
                        <input type="checkbox" className="mt-1" checked={holdForDays} onChange={(e) => setHoldForDays(e.target.checked)} />
                        <span>Hold for days
                          <span className="block text-xs text-fg-muted">{HOLD_FOR_DAYS_LINE}</span>
                        </span>
                      </label>
                    )}
                  </>
                )}
              </>
            )}
            {(kind === "OPTION" || kind === "FUTURE") && (
              <div>
                <label className="block text-xs text-fg-muted mb-1">Max lots<FromDefaults on={marked("maxLots", maxLots)} /></label>
                <input type="number" min={1} placeholder="risk-based" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={maxLots} onChange={(e) => setMaxLots(e.target.value)} />
              </div>
            )}
          </div>
          {kind === "OPTION" && structure === "CUSTOM" && (
            <div className="mt-2 rounded border border-border bg-surface-2/40 p-2 text-xs" data-deploy="legs">
              <div className="flex flex-wrap items-center justify-between gap-1 mb-1">
                <span className="text-fg-muted">Legs (2-{MAX_CUSTOM_LEGS}, one expiry; strikes relative to the spot at signal time){ownMark("customLegs", true) && <FromDefaults on />}</span>
                <button id="deploy-legs" disabled={customLegs.length >= MAX_CUSTOM_LEGS} onClick={addLeg} className="rounded border border-border hover:bg-surface-2 px-2 py-0.5 disabled:opacity-50">+ leg</button>
              </div>
              {!customLegs.length && <p className="text-fg-muted mb-1">No legs yet: add each leg of your structure.</p>}
              <div className="space-y-1">
                {customLegs.map((leg, i) => (
                  <div key={i} className="grid grid-cols-3 gap-1 items-center sm:grid-cols-6">
                    <select aria-label="Buy or sell" className="rounded bg-surface-2 border border-border px-1 py-1" value={leg.role} onChange={(e) => updateLeg(i, { role: e.target.value as LegDraft["role"] })}>
                      <option value="">Buy / sell?</option>
                      <option value="SHORT">Sell</option>
                      <option value="LONG">Buy</option>
                    </select>
                    <input type="number" min={1} className="rounded bg-surface-2 border border-border px-1 py-1" value={leg.ratio} title="ratio (lots of this leg per lot of the structure)" onChange={(e) => updateLeg(i, { ratio: Math.max(1, Math.round(Number(e.target.value)) || 1) })} />
                    <select aria-label="Strike" className="rounded bg-surface-2 border border-border px-1 py-1" value={leg.strike_rule} onChange={(e) => updateLeg(i, { strike_rule: e.target.value as LegDraft["strike_rule"], strike_offset: e.target.value === "ATM" ? 0 : leg.strike_offset })}>
                      <option value="">Strike?</option>
                      <option value="ATM">ATM</option>
                      <option value="ITM">ITM</option>
                      <option value="OTM">OTM</option>
                    </select>
                    <input type="number" min={0} disabled={leg.strike_rule === "ATM" || !leg.strike_rule} placeholder="Not set" className="rounded bg-surface-2 border border-border px-1 py-1 disabled:opacity-40" value={leg.strike_offset || ""} title="strike steps from ATM" onChange={(e) => updateLeg(i, { strike_offset: Number(e.target.value) || 0 })} />
                    <select aria-label="CE or PE" className="rounded bg-surface-2 border border-border px-1 py-1" value={leg.right} onChange={(e) => updateLeg(i, { right: e.target.value as LegDraft["right"] })}>
                      <option value="">CE / PE?</option>
                      <option value="CE">CE</option>
                      <option value="PE">PE</option>
                    </select>
                    <button onClick={() => removeLeg(i)} className="text-down hover:underline disabled:opacity-40">remove</button>
                  </div>
                ))}
              </div>
              {savedLegsOf(customLegs) && <div className="mt-1"><SaveAsDefault fieldKey="options.custom_legs" value={savedLegsOf(customLegs)} /></div>}
              <div className="mt-1 text-xs text-fg-muted">
                Max loss, max profit and breakevens come from the expiry payoff of these legs. A side with no protection (more sold than bought) is undefined risk:
                lots are sized off the loss the stop accepts and the group closes beyond that side's breakeven.
              </div>
            </div>
          )}
          {kind === "OPTION" && structure !== "CUSTOM" && (
            <div className="mt-2 text-xs">
              <button onClick={() => setShowFilters(!showFilters)} className="text-brand hover:underline">
                {showFilters ? "Hide" : "Show"} strike filters {strikeFilters() ? "(active)" : ""}
              </button>
              {showFilters && (
                <div className="mt-2 grid sm:grid-cols-4 gap-2">
                  {([["min_oi", "Min OI"], ["min_volume", "Min volume"], ["max_spread_pct", "Max bid/ask spread %"], ["target_delta", "Target |delta| (0-1)"],
                     ["min_iv_pct", "Min IV %"], ["max_iv_pct", "Max IV %"], ["min_premium", "Min premium"], ["max_premium", "Max premium"],
                     ["delta_tolerance", "Delta tolerance (with a target delta)"], ["search_steps", "Strikes searched either side"]] as const).map(([k, label]) => (
                    <div key={k}>
                      <label className="block text-xs text-fg-muted mb-0.5">{label}</label>
                      <input type="number" step="any" className="w-full rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={filters[k] ?? ""}
                        onChange={(e) => { setFilters({ ...filters, [k]: e.target.value }); setPreview(null); }} />
                    </div>
                  ))}
                  <div className="sm:col-span-4 text-xs text-fg-muted">
                    Applied at signal time to the live option chain around the rule strike: strikes failing liquidity, IV, delta or premium bounds are skipped;
                    with a target delta the passing strike nearest that delta wins. If the chain cannot be checked the trade is refused, never guessed.
                  </div>
                </div>
              )}
            </div>
          )}
          {(kind === "OPTION" || kind === "FUTURE") && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-fg-muted">
                {kind === "OPTION"
                  ? structure === "SINGLE"
                    ? "The strategy signals on the underlying; at signal time the contract is picked from the instrument master and the spot. Exits follow the strategy's underlying levels, with the premium " + (position === "BUY" ? "stop" : "ceiling") + " as a safety net."
                    : PAYOFF_STRUCTURES.includes(structure)
                      ? "Priced off the expiry payoff of its legs: max loss, max profit and breakevens are computed, not assumed. Legs carry ratios (1:2 for a ratio spread, 1:2:1 for a butterfly). Sized off the max loss when defined, else off the loss the stop accepts; closed as one position on the P&L target/stop, beyond an unprotected side's breakeven, or at square-off."
                    : DEBIT_STRUCTURES.includes(structure)
                      ? "A debit structure bought at signal time; the debit is the max loss and what the lots are sized off. Closed as one position when it is worth the target % more, has lost the stop % of the debit, or at square-off."
                      : WINGED_STRUCTURES.includes(structure)
                        ? "A defined-risk credit structure sold at signal time: short leg(s) at the rule strike, protective wing(s) the chosen width away. Sized in lots off max loss; closed as one position on the credit target/stop, a short-strike breach, or square-off."
                        : "An undefined-risk credit structure: short CE and PE with no wings. There is no max loss - lots are sized off the loss the stop accepts, and the broker's full margin applies. Closed on the credit target/stop, a short-strike breach, or square-off."
                  : "The strategy signals on the underlying; the future of the chosen expiry is traded in the signal's direction."}
              </span>
              {kind === "OPTION" && (
                <input type="number" placeholder="spot (optional)" className="w-36 rounded bg-surface-2 border border-border px-2 py-1 text-xs" value={spot} onChange={(e) => setSpot(e.target.value)} />
              )}
              <button disabled={busy || !symbol} onClick={runPreview} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1 disabled:opacity-50">Preview contract</button>
            </div>
          )}
          {preview && preview.contracts && (
            <div className="mt-2 grid sm:grid-cols-2 gap-2 text-xs">
              {(["LONG", "SHORT"] as const).map((dir) => {
                const c = preview.contracts![dir];
                return (
                  <div key={dir} className="rounded border border-border bg-surface-2/60 p-2">
                    <div className="text-xs uppercase tracking-wide text-fg-muted">{dir} signal → {"error" in c ? "unresolved" : `${c.entry_side} ${c.tradingsymbol}`}</div>
                    {"error" in c ? (
                      <div className="text-warn mt-1">{c.error}</div>
                    ) : (
                      <div className="text-fg-muted mt-1">{c.exchange} · expiry {c.expiry}{c.strike ? ` · strike ${c.strike}` : ""}{c.right ? ` ${c.right}` : ""} · lot {c.lot_size}</div>
                    )}
                  </div>
                );
              })}
              <div className="sm:col-span-2 text-xs text-fg-muted">{preview.rules}{preview.spot ? ` · spot ${preview.spot} (${preview.spot_source})` : ""}</div>
            </div>
          )}
          {preview && preview.structures && (
            <div className="mt-2 grid sm:grid-cols-2 gap-2 text-xs">
              {(["LONG", "SHORT"] as const).map((dir) => {
                const st = preview.structures![dir];
                return (
                  <div key={dir} className="rounded border border-border bg-surface-2/60 p-2">
                    <div className="text-xs uppercase tracking-wide text-fg-muted">{dir} signal → {"error" in st ? "no structure" : st.strategy.replace(/_/g, " ").toLowerCase()}</div>
                    {"error" in st ? (
                      <div className="text-warn mt-1">{st.error}</div>
                    ) : (
                      <div className="mt-1 space-y-0.5 text-fg-muted">
                        {st.legs.map((l) => <div key={l.tradingsymbol}>{l.side} {(l.ratio ?? 1) > 1 ? `${l.ratio}x ` : ""}{l.tradingsymbol} <span className="text-fg-muted">({l.role.toLowerCase()} leg)</span></div>)}
                        <div className="text-fg-muted">expiry {st.expiry} · lot {st.lot_size} · {st.width_points} pts wide</div>
                        {st.metrics ? (
                          <div className="text-xs mt-1">
                            {st.metrics.net_credit >= 0 ? "credit" : "debit"} <span className="text-up">{Math.abs(st.metrics.net_credit)}</span>/unit · max loss{" "}
                            {st.metrics.max_loss === null || st.metrics.max_loss === undefined
                              ? <span className="text-down">UNDEFINED (sized off the stop, {st.metrics.risk_per_unit}/unit)</span>
                              : <><span className="text-down">{st.metrics.max_loss}</span>/unit ({formatNumber(st.metrics.max_loss * st.lot_size, 3, { trim: true })}/lot)</>}
                            {st.metrics.max_profit !== null && st.metrics.max_profit !== undefined ? <> · max profit {st.metrics.max_profit}/unit</> : null}
                            · breakeven {st.metrics.breakevens.join(" / ")}
                            {st.metrics.pnl_stop !== undefined
                              ? <> · exit at P&L ≥ {st.metrics.pnl_target} or ≤ {st.metrics.pnl_stop} per unit</>
                              : <> · exit at value ≤ {st.metrics.target_value} or ≥ {st.metrics.stop_value}</>}
                          </div>
                        ) : st.metrics_error ? <div className="text-warn text-xs mt-1">{st.metrics_error}</div> : null}
                      </div>
                    )}
                  </div>
                );
              })}
              <div className="sm:col-span-2 text-xs text-fg-muted">{preview.rules}{preview.spot ? ` · spot ${preview.spot} (${preview.spot_source})` : ""}</div>
            </div>
          )}
        </div>

        <div className="mt-3 rounded-lg border border-border bg-surface-2/40 p-3">
          <div className="text-xs font-bold uppercase tracking-wider text-fg-muted mb-2">Dynamic exits <span className="normal-case font-normal">(optional; the strategy's stop and targets still apply)</span></div>
          <div className="grid sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-fg-muted mb-1">Trailing stop %<FromDefaults on={marked("exitTrail", exitTrail)} /></label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={exitTrail} onChange={(e) => setExitTrail(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Break-even at R<FromDefaults on={marked("exitBreakEven", exitBreakEven)} /></label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={exitBreakEven} onChange={(e) => setExitBreakEven(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Time exit (minutes)<FromDefaults on={marked("exitMinutes", exitMinutes)} /></label>
              <input type="number" min={1} placeholder="off" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={exitMinutes} onChange={(e) => setExitMinutes(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Flat at (HH:MM IST)<FromDefaults on={marked("exitAt", exitAt)} /></label>
              <input placeholder="off" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={exitAt} onChange={(e) => setExitAt(e.target.value)} />
            </div>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border bg-surface-2/40 p-3">
          <div className="text-xs font-bold uppercase tracking-wider text-fg-muted mb-2">Regime filter</div>
          <div className="flex flex-wrap gap-2">
            {REGIME_OPTIONS.map((r) => (
              <button key={r} type="button" onClick={() => toggleRegime(r)} className={`rounded-md border px-2.5 py-1 text-xs font-bold ${regimes.includes(r) ? "border-brand/60 text-brand bg-brand/10" : "border-border text-fg-muted hover:text-fg"}`}>
                {r.replace("_", " ").toLowerCase()}
              </button>
            ))}
          </div>
          <Details id="deployments.regime-filter" className="mt-space-2"><p>(optional; a diagnostic only - recorded for review, it never blocks an entry)</p></Details>
        </div>

        <div className="mt-3 rounded-lg border border-border bg-surface-2/40 p-3">
          <div className="text-xs font-bold uppercase tracking-wider text-fg-muted mb-2">OI gates</div>
          <div className="flex flex-wrap gap-2">
            {OI_GATE_OPTIONS.map(([g, label]) => (
              <button key={g} type="button" onClick={() => toggleOiGate(g)} title={g}
                      className={`rounded-md border px-2.5 py-1 text-xs font-bold ${oiGates.includes(g) ? "border-brand/60 text-brand bg-brand/10" : "border-border text-fg-muted hover:text-fg"}`}>
                {label}
              </button>
            ))}
          </div>
          <Details id="deployments.oi-gates" className="mt-space-2"><p>(optional; checked before each new entry from the option chain's OI - missing or stale OI data blocks the entry; exits are never gated. Limits come from the Option Chain page's OI settings)</p></Details>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4">
          {!paperOnly && <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-fg-muted">Mode</span>
            {(["PAPER", "LIVE"] as ExecutionMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                aria-pressed={mode === m}
                className={`rounded-md border px-3 py-1 text-xs font-bold tracking-wide ${
                  mode === m
                    ? m === "LIVE" ? "border-down text-down bg-down/15" : "border-brand/50 text-brand bg-brand/10"
                    : "border-border text-fg-muted hover:text-fg"
                }`}
              >
                {m}
              </button>
            ))}
          </div>}
          <button
            onClick={handleCreate}
            disabled={busy || !strategyId || !symbol}
            className={`flex items-center gap-1.5 rounded font-semibold px-4 py-1.5 text-sm disabled:opacity-50 ${
              mode === "LIVE" ? "bg-down text-surface hover:brightness-110" : "bg-brand text-on-brand hover:bg-brand-strong"
            }`}
          >
            <Plus size={14} /> {mode === "LIVE" ? "Deploy LIVE" : paperOnly ? "Start on paper" : "Deploy paper"}
          </button>
          {brokers.length === 0 && (
            <span className="text-xs text-warn">Connect a broker on the Broker page first - even paper trading needs a broker session for live candles.</span>
          )}
        </div>

        {confirmLive && (
          <div className="mt-4 rounded-lg border border-down/50 bg-down/10 p-4 space-y-3">
            <div className="flex items-center gap-2 text-down font-bold text-sm">
              <AlertTriangle size={16} /> This will place real orders with real money.
            </div>
            <ul className="text-xs text-fg-muted list-disc pl-5 space-y-1">
              <li><b>{strategyId}</b> on <b>{symbol}</b> ({exchange || "its own exchange"}) via <b className="capitalize">{brokerName || "your stored broker"}</b>, every minute during market hours{kind === "OPTION" || kind === "FUTURE" ? <> - trading <b>{kind === "OPTION" ? `${position === "BUY" ? "bought" : "written"} options` : "futures"}</b> on it</> : ""}.</li>
              {kind === "OPTION" && position === "WRITE" && <li className="text-down">Written options have open-ended risk until the underlying stop or the premium ceiling exits; margin is blocked at the broker.</li>}
              <li>Every entry passes your Risk Management limits and kill switches; a broker-side stop-loss is placed with each fill.</li>
              <li>You can pause or stop it here at any time; Emergency Exit (Risk Management) flattens everything.</li>
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="rounded bg-surface-2 border border-down/40 px-2 py-1.5 text-sm w-40"
                placeholder='type LIVE to confirm'
                value={liveTyped}
                onChange={(e) => setLiveTyped(e.target.value)}
              />
              <button
                onClick={() => void submit()}
                disabled={busy || liveTyped !== "LIVE"}
                className="rounded bg-down text-surface hover:brightness-110 font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
              >
                {busy ? "Deploying…" : "Confirm LIVE deployment"}
              </button>
              <button onClick={() => { setConfirmLive(false); setLiveTyped(""); }} className="text-xs text-fg-muted hover:text-fg">Cancel</button>
            </div>
          </div>
        )}

        {error && <div className="mt-3 text-sm text-down">{error}</div>}
        {message && <div className="mt-3 text-sm text-up">{message}</div>}
      </Card>

      {!paperOnly && <Card title={`Deployments (${deployments.length})`}>
        <div className="flex justify-end mb-2">
          <label className="flex items-center gap-2 text-xs text-fg-muted cursor-pointer">
            <input type="checkbox" checked={includeStopped} onChange={(e) => setIncludeStopped(e.target.checked)} />
            show stopped
          </label>
        </div>
        {deployments.length === 0 ? (
          <div className="text-sm text-fg-muted py-4 text-center">Nothing deployed yet. Deploy a strategy above and the worker picks it up on its next cycle.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-fg-muted uppercase text-xs tracking-wide">
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
                    <td className="py-1.5 pr-3 text-fg-muted">{d.id}</td>
                    <td className="py-1.5 pr-3 font-medium text-fg">{d.strategy_id}<div className="text-xs text-fg-muted">{d.timeframe} base{d.holding === "SWING" ? " · swing (overnight)" : ""}</div></td>
                    <td className="py-1.5 pr-3 text-fg">{d.symbol}<div className="text-xs text-fg-muted">{d.exchange}{d.instrument_kind !== "UNDERLYING" ? ` · ${d.contract_rules}` : ""}</div></td>
                    <td className="py-1.5 pr-3"><ModeBadge mode={d.mode} /></td>
                    <td className="py-1.5 pr-3 capitalize text-fg-muted">{d.broker_name ?? "-"}
                      {d.mode === "LIVE" && d.last_route && <div className="text-xs text-fg-muted normal-case max-w-[14rem] truncate" title={d.last_route}>{d.last_route}</div>}
                    </td>
                    <td className="py-1.5 pr-3"><StatusBadge status={d.status} /></td>
                    <td className="py-1.5 pr-3 font-tabular text-fg">{d.open_positions}</td>
                    <td className="py-1.5 pr-3 text-fg-muted whitespace-nowrap">{ago(d.last_evaluated_at)}</td>
                    <td className="py-1.5 pr-3 text-fg-muted whitespace-nowrap">{ago(d.last_signal_at)}</td>
                    <td className="py-1.5 pr-3 max-w-[16rem]">
                      {d.pause_reason && d.status !== "ACTIVE" && <div className="text-warn">{d.pause_reason}</div>}
                      {d.last_error && <div className="text-fg-muted">{d.last_error}</div>}
                    </td>
                    <td className="py-1.5 pl-3 text-right whitespace-nowrap">
                      {d.status === "ACTIVE" && (
                        <button title="Pause" disabled={busy} onClick={() => act(`Deployment #${d.id} paused.`, () => api.pauseDeployment(d.id, "paused from Autopilot tab"))} className="p-1 text-warn hover:bg-surface-2 rounded disabled:opacity-50"><Pause size={14} /></button>
                      )}
                      {d.status === "PAUSED" && (
                        <button title="Resume" disabled={busy} onClick={() => act(`Deployment #${d.id} resumed.`, () => api.resumeDeployment(d.id))} className="p-1 text-up hover:bg-surface-2 rounded disabled:opacity-50"><Play size={14} /></button>
                      )}
                      {d.status !== "STOPPED" && (
                        <button title="Stop (no new trades; open positions stay monitored)" disabled={busy} onClick={() => act(`Deployment #${d.id} stopped.`, () => api.stopDeployment(d.id, "stopped from Autopilot tab"))} className="p-1 text-down hover:bg-surface-2 rounded disabled:opacity-50"><Square size={14} /></button>
                      )}
                      {d.status === "STOPPED" && (
                        <button title="Delete" disabled={busy} onClick={() => act(`Deployment #${d.id} deleted.`, () => api.deleteDeployment(d.id))} className="p-1 text-fg-muted hover:text-down hover:bg-surface-2 rounded disabled:opacity-50"><Trash2 size={14} /></button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>}
    </div>
  );
}
