import { ChevronRight, Pause, Play, Square } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { BROKER_PATH } from "../broker/paths";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { CardV2, ChipV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2, PageTitleV2, StateChipV2 } from "../trading/PartsV2";
import type { CustomStrategyResponse, Deployment, StrategyInfo } from "../types";
import { DEFAULTS_PATH, appliedLine, useTradingDefaults, type TradingDefaults } from "../settings/tradingDefaults";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import {
  READY, SYMBOLS, TEMPLATE_ID_PREFIX, botName, botState, botStatusText, botWhat, cleanSymbol, lastChecked, paperBotRequestWithDefaults, sortBots,
  startSummary, strategyLine, symbolProblem,
} from "./model";
import { templatesApi, type StrategyTemplate, type TemplateAdoption } from "../strategyTemplates/api";

// The full form (options structures, exits, filters) loads only when "More ways to trade" is opened.
const MoreWaysToTrade = lazy(() => import("../pages/DeploymentsPage"));

const OTHER = "__other__";
/** A strategy template in the strategy list (its strategy id comes from the server once the template is picked). */
const PICK = "template-pick:";
/** The chart timeframe a template starts on when it offers it (a template's settings stay its own defaults here). */
const TEMPLATE_TIMEFRAME = "15m";

/** Strategy templates step 2: `?template=D2` opens the form with that template picked (the idea library's "Adopt"). */
function templateFromUrl(): string | null {
  try {
    return new URLSearchParams(window.location.search).get("template");
  } catch {
    return null;
  }
}

/**
 * UX2 fix 4 ("Paper bot in three taps", docs/design/UX_AUDIT_FULL.md P18): the v2 page at /deployments, shown only while
 * `ui_v2` is on (the v2 shell picks it; with the flag off today's Autopilot page renders as before).
 *
 * Top to bottom: the page's one primary action "Start a paper bot" (jumps to the form), "Your bots" as cards (name,
 * symbol, Running / Paused / Stopped, Pause / Resume / Stop), then "Start a paper bot": 1 a strategy with one sentence,
 * 2 a symbol, 3 Start on paper. One lot, PAPER only, the trader's own risk limits apply on the server. No LIVE switch on
 * this page (LIVE stays behind the go-live checklist). Options structures, exits and filters are under "More ways to
 * trade", the same form as before, paper only. Nothing here is advice: the trader picks the strategy and the symbol.
 */
export default function PaperBotV2(_props: PageProps) {
  const { user, loading: authLoading } = useAuth();
  const features = useFeatures();
  // Settings part C: the user's own defaults pre-fill "Start on paper" (v2 only; null = none, start as today).
  const defaults = useTradingDefaults(!!user && features.on("ui_v2"));
  const [bots, setBots] = useState<Deployment[] | null>(null);
  const [botsFailed, setBotsFailed] = useState(false);
  const [library, setLibrary] = useState<StrategyInfo[] | null>(null);
  const [custom, setCustom] = useState<CustomStrategyResponse[]>([]);
  const [hasBroker, setHasBroker] = useState<boolean | null>(null);
  const [showStopped, setShowStopped] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [busyBot, setBusyBot] = useState<number | null>(null);
  const [confirmStop, setConfirmStop] = useState<number | null>(null);
  const [botNote, setBotNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [stepUp, setStepUp] = useState<{ reason: string; retry: () => Promise<unknown> } | null>(null);
  // Strategy templates step 2 (ui_v2 + strategy_templates): the templates the organisation can adopt as paper strategies.
  const templatesOn = features.on("ui_v2") && features.on("strategy_templates");
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const formRef = useRef<HTMLElement | null>(null);
  const firstField = useRef<HTMLSelectElement>(null);

  const loadBots = useCallback(async () => {
    try {
      setBots(await api.listDeployments(true));
      setBotsFailed(false);
    } catch {
      setBotsFailed(true);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void loadBots();
    api.listStrategies().then(setLibrary).catch(() => setLibrary([]));
    api.listCustomStrategies().then(setCustom).catch(() => setCustom([]));
    api.listStoredBrokerCredentials().then((b) => setHasBroker(b.length > 0)).catch(() => setHasBroker(null));
    const timer = window.setInterval(() => void loadBots(), 15000);
    return () => window.clearInterval(timer);
  }, [user, loadBots]);

  useEffect(() => {
    if (!user || !templatesOn) { setTemplates([]); return; }
    templatesApi.list().then((r) => setTemplates(r.templates.filter((t) => t.available))).catch(() => setTemplates([]));
  }, [user, templatesOn]);

  const names = useMemo(() => new Map<string, string>([
    ...(library ?? []).map((s) => [s.id, s.name] as [string, string]),
    ...custom.map((c) => [c.strategy_id, c.config.name] as [string, string]),
    ...templates.map((t) => [TEMPLATE_ID_PREFIX + t.id, t.name] as [string, string]),
  ]), [library, custom, templates]);

  async function act(bot: Deployment, kind: "pause" | "resume" | "stop") {
    setBusyBot(bot.id); setBotNote(null);
    const name = botName(bot, names);
    const run = async () => {
      if (kind === "pause") await api.pauseDeployment(bot.id, "paused from the Paper bot page");
      else if (kind === "resume") await api.resumeDeployment(bot.id);
      else await api.stopDeployment(bot.id, "stopped from the Paper bot page");
    };
    try {
      await run();
      setConfirmStop(null);
      setBotNote({ kind: "done", text: kind === "pause" ? `${name} is paused. It takes no new trades until you resume it.`
        : kind === "resume" ? `${name} is running again.`
        : `${name} is stopped. It takes no new trades; an open paper trade is still watched until it closes.` });
      await loadBots();
    } catch (e) {
      if (isStepUpError(e)) setStepUp({ reason: String(e), retry: () => act(bot, kind) });
      else setBotNote({ kind: "warn", text: "That did not go through just now. Try again in a moment." });
    } finally {
      setBusyBot(null);
    }
  }

  function goToForm() {
    formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    window.setTimeout(() => firstField.current?.focus({ preventScroll: true }), 300);
  }

  if (authLoading) return null;
  if (!user) {
    return (
      <div className="space-y-space-4">
        <PageTitleV2 title="Paper bot" subtitle="Your strategy trades with paper money here, so you can watch how it does." />
        <NoteV2>Sign in to start a paper bot.</NoteV2>
      </div>
    );
  }

  const shown = bots ? sortBots(bots, showStopped) : [];
  const stoppedCount = bots ? bots.filter((b) => botState(b) === "stopped").length : 0;
  const canChange = user.role !== "VIEWER";

  return (
    <div className="space-y-space-4">
      <PageTitleV2 title="Paper bot" subtitle="Your strategy trades with paper money here, so you can watch how it does. No real money is used."
        action={canChange ? <button type="button" className={BUTTON_PRIMARY} onClick={goToForm}>Start a paper bot</button> : undefined} />
      <PageIntro id="paper-bot" lines={[
        "Pick a strategy and a symbol; the bot then checks the market on its own and trades on paper, even while the app is closed.",
        "Pause or stop a bot any time. Your risk limits apply to every paper trade.",
      ]} />

      {stepUp && (
        <StepUpDialog reason={stepUp.reason} onCancel={() => setStepUp(null)} onVerified={() => { const r = stepUp.retry; setStepUp(null); void r(); }} />
      )}

      <CardV2 title="Your bots" count={bots ? sortBots(bots).length : undefined} testId="bots">
        {botsFailed && !bots ? <LoadFailedV2 what="your bots" />
          : !bots ? <LoadingRowsV2 rows={2} />
          : shown.length === 0 ? (
            <p className="text-t14 text-fg-muted">No paper bot yet. Start one below: it trades with paper money only.</p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {shown.map((bot) => (
                <BotRow key={bot.id} bot={bot} name={botName(bot, names)} busy={busyBot === bot.id} disabled={busyBot !== null || !canChange}
                  confirming={confirmStop === bot.id} onAsk={() => setConfirmStop(bot.id)} onCancel={() => setConfirmStop(null)}
                  onAct={(kind) => void act(bot, kind)} />
              ))}
            </ul>
          )}
        {botNote && <div className="mt-space-3"><NoteV2 kind={botNote.kind}>{botNote.text}</NoteV2></div>}
        {stoppedCount > 0 && (
          <button type="button" className="mt-space-3 inline-flex min-h-11 items-center text-t14 font-medium text-brand hover:underline md:min-h-0"
            onClick={() => setShowStopped((v) => !v)}>
            {showStopped ? "Hide stopped bots" : `Show stopped bots (${stoppedCount})`}
          </button>
        )}
      </CardV2>

      {canChange && (
        <section ref={formRef} className="scroll-mt-20">
          <StartBotV2 library={library} custom={custom} hasBroker={hasBroker} firstField={firstField} onStarted={() => void loadBots()}
            defaults={defaults} approvalAllowed={features.on("approval_only_entries")} templates={templates} initialTemplate={templateFromUrl()} />
        </section>
      )}

      {canChange && (
        <details open={moreOpen} onToggle={(e) => setMoreOpen(e.currentTarget.open)} className="group/more" data-more-ways>
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-2 rounded-control text-t14 font-semibold text-fg [&::-webkit-details-marker]:hidden">
            <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open/more:rotate-90" />
            More ways to trade (options, futures, exits, filters)
          </summary>
          <div className="pt-space-3">
            {moreOpen && (
              <Suspense fallback={<LoadingRowsV2 rows={3} />}>
                <MoreWaysToTrade paperOnly onCreated={() => void loadBots()} />
              </Suspense>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

export function BotRow({ bot, name, busy, disabled, confirming, onAsk, onCancel, onAct }: {
  bot: Deployment; name: string; busy: boolean; disabled: boolean; confirming: boolean;
  onAsk: () => void; onCancel: () => void; onAct: (kind: "pause" | "resume" | "stop") => void;
}) {
  const state = botState(bot);
  const status = botStatusText(bot);
  return (
    <li className="space-y-space-2 py-space-3 first:pt-0 last:pb-0" data-bot={bot.id}>
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-space-2 text-t15 font-semibold text-fg">
            <span className="min-w-0 break-words">{name}</span>
            {bot.mode === "LIVE" ? <ChipV2 kind="warn">Live</ChipV2> : <ChipV2>Paper</ChipV2>}
          </p>
          <p className="text-t14 text-fg-muted">{botWhat(bot)}</p>
          <p className="mt-space-1 flex flex-wrap items-center gap-space-2 text-t13 text-fg-muted">
            <StateChipV2 tone={state === "paused" ? "warn" : "neutral"} check={state === "running"}>{status}</StateChipV2>
            {state !== "stopped" && <span>{lastChecked(bot.last_evaluated_at)}</span>}
            {bot.open_positions > 0 && <span>{bot.open_positions} open {bot.open_positions === 1 ? "trade" : "trades"}</span>}
          </p>
        </div>
        {state !== "stopped" && !confirming && (
          <div className="flex shrink-0 gap-space-2">
            {state === "running" && (
              <button type="button" className={BUTTON_SECONDARY} disabled={disabled} aria-busy={busy || undefined} onClick={() => onAct("pause")}>
                <Pause size={16} aria-hidden /> Pause
              </button>
            )}
            {state === "paused" && (
              <button type="button" className={BUTTON_SECONDARY} disabled={disabled} aria-busy={busy || undefined} onClick={() => onAct("resume")}>
                <Play size={16} aria-hidden /> Resume
              </button>
            )}
            <button type="button" className={BUTTON_SECONDARY} disabled={disabled} onClick={onAsk}>
              <Square size={16} aria-hidden /> Stop
            </button>
          </div>
        )}
      </div>
      {confirming && (
        <div className="space-y-space-2 rounded-control border border-subtle p-space-3" role="group" aria-label={`Stop ${name}`}>
          <p className="text-t14 text-fg">Stop {name}? It takes no new trades. An open paper trade is still watched until it closes.</p>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <button type="button" className={BUTTON_SECONDARY} disabled={disabled} aria-busy={busy || undefined} onClick={() => onAct("stop")}>Yes, stop it</button>
            <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Keep it running</button>
          </div>
        </div>
      )}
    </li>
  );
}

/** The step number in front of a field's label (decoration: the label itself says what to do). */
function StepNo({ n }: { n: number }) {
  return <span aria-hidden className="mr-space-1 inline-flex h-5 w-5 items-center justify-center rounded-pill bg-surface-3 text-t12 font-semibold text-fg">{n}</span>;
}

export function StartBotV2({ library, custom, hasBroker, firstField, onStarted, defaults = null, approvalAllowed = false, templates = [],
  initialTemplate = null }: {
  library: StrategyInfo[] | null; custom: CustomStrategyResponse[]; hasBroker: boolean | null;
  firstField: RefObject<HTMLSelectElement>; onStarted: () => void;
  /** Settings part C: the user's own defaults (a pre-fill, shown and switchable here); null = none. */
  defaults?: TradingDefaults | null; approvalAllowed?: boolean;
  /** Strategy templates step 2: templates the user can adopt here (empty while the flag is off). */
  templates?: StrategyTemplate[]; initialTemplate?: string | null;
}) {
  const ids = { strategy: useId(), symbol: useId(), other: useId(), line: useId(), defaults: useId(), timeframe: useId() };
  const [useDefaults, setUseDefaults] = useState(true);
  const ready = useMemo(() => (library ?? []).filter((s) => READY.some((r) => r.id === s.id))
    .sort((a, b) => READY.findIndex((r) => r.id === a.id) - READY.findIndex((r) => r.id === b.id)), [library]);
  const others = useMemo(() => (library ?? []).filter((s) => !READY.some((r) => r.id === s.id)), [library]);
  const [strategyId, setStrategyId] = useState("");
  const [symbolChoice, setSymbolChoice] = useState("NIFTY 50");
  const [otherSymbol, setOtherSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [touched, setTouched] = useState(false);
  // Strategy templates step 2: the picked template, its chart timeframe and the server's pre-fill for them (nothing is
  // created until "Start on paper"; the template keeps its usual settings here).
  const picked = strategyId.startsWith(PICK) ? templates.find((t) => t.id === strategyId.slice(PICK.length)) ?? null : null;
  const [templateTimeframe, setTemplateTimeframe] = useState("");
  const [adoption, setAdoption] = useState<TemplateAdoption | null>(null);
  const [adoptFailed, setAdoptFailed] = useState(false);
  const urlTemplateApplied = useRef(false);

  useEffect(() => {
    if (urlTemplateApplied.current || !initialTemplate || !templates.some((t) => t.id === initialTemplate)) return;
    urlTemplateApplied.current = true;
    setStrategyId(PICK + initialTemplate);
  }, [templates, initialTemplate]);

  useEffect(() => {
    if (!picked) return;
    if (!picked.timeframes.some((t) => t.value === templateTimeframe)) {
      setTemplateTimeframe(picked.timeframes.some((t) => t.value === TEMPLATE_TIMEFRAME) ? TEMPLATE_TIMEFRAME : picked.default_timeframe ?? "");
    }
  }, [picked, templateTimeframe]);

  const pickedId = picked?.id ?? null;
  useEffect(() => {
    setAdoption(null); setAdoptFailed(false);
    if (!pickedId || !templateTimeframe) return;
    let live = true;
    templatesApi.adopt(pickedId, { timeframe: templateTimeframe })
      .then((a) => { if (live) setAdoption(a); })
      .catch(() => { if (live) setAdoptFailed(true); });
    return () => { live = false; };
  }, [pickedId, templateTimeframe]);

  // The first ready-made strategy, with the symbol it is shown with.
  useEffect(() => {
    // (a template from the link, picked in this same commit, wins: the effect above already set it)
    if (strategyId || !library?.length || urlTemplateApplied.current) return;
    const first = ready[0] ?? library[0];
    setStrategyId(first.id);
    const r = READY.find((x) => x.id === first.id);
    if (r) setSymbolChoice(r.symbol);
  }, [library, ready, strategyId]);

  if (library === null) return <CardV2 title="Start a paper bot"><LoadingRowsV2 rows={3} /></CardV2>;
  if (library.length === 0 && custom.length === 0) {
    return <CardV2 title="Start a paper bot"><LoadFailedV2 what="the strategy library" /></CardV2>;
  }

  const templateInfo = adoption && picked ? { timeframes: [adoption.deployment.timeframe], description: picked.idea } : null;
  const info = picked ? templateInfo : library.find((s) => s.id === strategyId) ?? null;
  // A template's id is the server's (with its settings); a pick whose template is gone starts nothing.
  const effectiveId = strategyId.startsWith(PICK) ? (picked && adoption?.strategy_id) || "" : strategyId;
  const symbol = symbolChoice === OTHER ? otherSymbol : symbolChoice;
  const problem = symbolProblem(symbol);
  const line = picked ? picked.idea
    : strategyLine(strategyId, info) || (custom.some((c) => c.strategy_id === strategyId) ? "Your own strategy from Build a strategy." : "");
  const offered = paperBotRequestWithDefaults(effectiveId, symbol, info, defaults, approvalAllowed).applied;
  const built = paperBotRequestWithDefaults(effectiveId, symbol, info, defaults, approvalAllowed, useDefaults).request;
  // A template reads its own candles (the adopted timeframe) and holds as the server said (Daily: for days).
  const request = picked && adoption ? { ...built, timeframe: adoption.deployment.timeframe, holding: adoption.deployment.holding } : built;

  async function start() {
    setTouched(true);
    if (problem || !effectiveId) return;
    setBusy(true); setResult(null);
    try {
      await api.createDeployment(request);
      setResult({ kind: "done", text: `Started on paper: ${cleanSymbol(symbol)}. It shows under Your bots and starts checking the market on its next round.` });
      onStarted();
    } catch (e) {
      setResult({ kind: "warn", text: e instanceof ApiError && e.status === 409
        ? `Nothing new was started: this strategy already runs on paper for ${cleanSymbol(symbol)} in your organisation. Pick another strategy or symbol.`
        : e instanceof ApiError && e.status === 403 ? "Your role cannot start a bot. Ask your organisation's owner."
        : "The bot could not be started just now. Try again in a moment." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Start a paper bot" testId="start">
      <div className="space-y-space-4">
        {hasBroker === false && (
          <NoteV2 kind="warn">
            A paper bot reads live prices from your broker. <Link to={BROKER_PATH} className="font-medium text-brand hover:underline">Connect your broker</Link> first.
          </NoteV2>
        )}
        <div>
          <label htmlFor={ids.strategy} className={LABEL}><StepNo n={1} />Pick a strategy</label>
          <select id={ids.strategy} ref={firstField} className={FIELD} value={strategyId} aria-describedby={ids.line}
            onChange={(e) => {
              setStrategyId(e.target.value); setResult(null);
              const r = READY.find((x) => x.id === e.target.value);
              if (r && symbolChoice !== OTHER) setSymbolChoice(r.symbol);
            }}>
            {ready.length > 0 && <optgroup label="Ready-made">{ready.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>}
            {custom.length > 0 && <optgroup label="My strategies">{custom.map((c) => <option key={c.strategy_id} value={c.strategy_id}>{c.config.name}</option>)}</optgroup>}
            {others.length > 0 && <optgroup label="More from the Library">{others.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>}
            {templates.length > 0 && (
              <optgroup label="Strategy templates">{templates.map((t) => <option key={t.id} value={PICK + t.id}>{t.name}</option>)}</optgroup>
            )}
          </select>
          {line && <p id={ids.line} className="mt-space-1 text-t14 text-fg-muted">{line}</p>}
        </div>
        {picked && (
          <div data-template={picked.id}>
            <label htmlFor={ids.timeframe} className={LABEL}>Chart timeframe</label>
            <select id={ids.timeframe} className={FIELD} value={templateTimeframe}
              onChange={(e) => { setTemplateTimeframe(e.target.value); setResult(null); }}>
              {picked.timeframes.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <p className="mt-space-1 text-t13 text-fg-muted" data-template-notes>
              {adoptFailed ? "This template cannot be set up just now. Try again in a moment."
                : adoption ? `Starts with the template's usual settings. ${adoption.notes.join(" ")}` : "Getting the template ready…"}
            </p>
          </div>
        )}
        <div>
          <label htmlFor={ids.symbol} className={LABEL}><StepNo n={2} />Pick a symbol</label>
          <select id={ids.symbol} className={FIELD} value={symbolChoice} onChange={(e) => { setSymbolChoice(e.target.value); setResult(null); }}>
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            <option value={OTHER}>Another symbol…</option>
          </select>
          {symbolChoice === OTHER && (
            <div className="mt-space-2">
              <label htmlFor={ids.other} className="sr-only">Type the symbol</label>
              <input id={ids.other} className={FIELD} value={otherSymbol} autoCapitalize="characters" autoComplete="off" placeholder="For example RELIANCE"
                aria-invalid={touched && !!problem} onChange={(e) => setOtherSymbol(e.target.value)} onBlur={() => setTouched(true)} />
            </div>
          )}
          {touched && problem && <p role="alert" className="mt-space-1 text-t13 text-warn">{problem}</p>}
        </div>
        <div className="space-y-space-2">
          <p className="text-t14 text-fg-muted">{startSummary(symbol, info, request)}</p>
          {offered.length > 0 && (
            <div className="space-y-space-1 rounded-control border border-subtle p-space-3" data-defaults>
              <label className="flex min-h-11 cursor-pointer items-center gap-space-2 text-t14 font-medium text-fg md:min-h-0">
                <input type="checkbox" className="h-4 w-4 shrink-0 accent-brand" checked={useDefaults} aria-describedby={ids.defaults}
                  onChange={(e) => { setUseDefaults(e.target.checked); setResult(null); }} />
                Use my defaults for this bot
              </label>
              <p id={ids.defaults} className="text-t13 text-fg-muted" data-defaults-line>
                {useDefaults ? appliedLine(offered) : "Starts with the usual settings instead."}{" "}
                <Link to={DEFAULTS_PATH} className="font-medium text-brand hover:underline">Change my defaults</Link>
              </p>
            </div>
          )}
          <button type="button" className={`${BUTTON_PRIMARY} w-full sm:w-auto`} disabled={busy || !effectiveId} aria-busy={busy || undefined} onClick={() => void start()}>
            {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
            Start on paper
          </button>
        </div>
        {result && <NoteV2 kind={result.kind}>{result.text}</NoteV2>}
      </div>
    </CardV2>
  );
}
