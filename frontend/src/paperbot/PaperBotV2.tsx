import { ChevronRight, Pause, Play, Square, Trash2 } from "lucide-react";
import { LiveBadge, PaperBadge } from "../ui/PaperBadge";
import { showsPaper } from "../ui/paperWords";
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import { ApiError, errorSentence } from "../api/errors";
import { useAuth, useOptionalAuth } from "../auth/AuthContext";
import { SymbolPicker, recentKeyFor, type PickedSymbol } from "../ui/SymbolPicker";
import { BROKER_PATH } from "../broker/paths";
import StepUpDialog, { isStepUpError } from "../components/StepUpDialog";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2, PageTitleV2, StateChipV2 } from "../trading/PartsV2";
import type { CustomStrategyResponse, Deployment, PortfolioExposure, StrategyInfo, TradeRecord } from "../types";
import { BotWhyV2 } from "./BotWhyV2";
import ReportCardV2 from "./ReportCardV2";
import { BotPositionsV2, botPositions } from "./BotPositionsV2";
import ContractPreviewV2 from "./ContractPreviewV2";
import {
  DEFAULTS_PATH, SWING_CONSTRUCTION_NOTE, appliedLine, constructionChoices, constructionProblem, needsConstruction, needsWidth, swingNotTaken,
  useTradingDefaults, widthOf, withConstruction, type BotConstruction, type TradingDefaults,
} from "../settings/tradingDefaults";
import { WINGED_STRUCTURES, type OptionStrategy } from "../types";
import { Chip } from "../ui/Chip";
import { Combobox, type ComboOption } from "../ui/Combobox";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Details } from "../ui/numbersFirst";
import { PageIntro } from "../ui/PageIntro";
import { useFeatures } from "../ui/useFeatures";
import { APPROVAL_MODE_KEY, ENTRY_MODE_CHOICES, ORDER_TYPE_KEY, PROTECTION_KEY, approvalModeOf, orderDefaultsOf, useTradeSettings,
  type EntryModeChoice } from "../settings/tradeSettings";
import { SaveAsDefault } from "../settings/SaveAsDefault";
import { draftFromState, formSymbol } from "../copilot/strategyDraft";
import CopilotDraftV2 from "./CopilotDraftV2";
import {
  BOTS_NOT_CHECKED, IDEA_SYMBOL, READY, SYMBOLS, TEMPLATE_ID_PREFIX, botName, botState, botStatusText, botWaitText, botWhat, cleanSymbol, lastChecked, lastSignal, paperBotRequestWithDefaults, whyNoTrade, sortBots,
  isIndex, isSwing, startSummaryParts, strategyLine, symbolProblem, withIdeaLots, type Preselect,
} from "./model";
import { templatesApi, type StrategyTemplate, type TemplateAdoption } from "../strategyTemplates/api";
import { handoffFromState, withTestedContract } from "../strategyFlow/model";
import { settingRows, templateLinkFromSearch, testPathFor, typedSettings, type TemplateLink } from "../strategyTemplates/link";
import {
  EMPTY_PICK, effectivePick, exitClass, exitNotes, exitProblem, stopOptions, targetOptions, templateExitsBody, type ExitPick, type StopSource,
  type TargetRule,
} from "../strategyTemplates/exits";

/** M68 PR-6: which New entries mode this bot uses now, and whether it follows his Trade settings; null when unknown. */
export function entryModeText(bot: Pick<Deployment, "entry_mode_in_effect" | "entry_mode_follows_settings">): string | null {
  if (bot.entry_mode_follows_settings === undefined) return null;
  const mode = bot.entry_mode_in_effect === "APPROVAL" ? "Asks you first" : bot.entry_mode_in_effect === "AUTO" ? "Places entries by itself"
    : "New entries not set - places nothing";
  return `${mode}${bot.entry_mode_follows_settings ? " (from your Trade settings)" : " (this bot's own choice)"}`;
}

/** M68 PR-1: the entry styles a bot can send (Trade settings > Order defaults > Order type), none pre-selected. */
const ENTRY_STYLES: ComboOption[] = [
  { value: "MARKET", label: "Market" }, { value: "PROTECTED_LIMIT", label: "Limit with price protection" },
];
const ENTRY_STYLE_MISSING = "Choose how this bot sends its entries.";
/** M68 PR-6 (TS1-060): "New entries" - none pre-selected (Trade settings > Automation). */
const MODE_OPTIONS: ComboOption[] = ENTRY_MODE_CHOICES.map((c) => ({ value: c.value, label: c.label }));
const MODE_MISSING = "Choose whether this bot places entries by itself or asks you first.";
const BAND_MISSING = "Enter the price protection % (0.05 to 5).";

/** The band typed for a limit with price protection, or null when it is not 0.05 to 5. */
function bandOf(text: string): number | null {
  const v = Number(text.replace(/,/g, "").trim());
  return text.trim() !== "" && Number.isFinite(v) && v >= 0.05 && v <= 5 ? v : null;
}

// The full form (options structures, exits, filters) loads only when "More ways to trade" is opened.
const MoreWaysToTrade = lazy(() => import("../pages/DeploymentsPage"));

const OTHER = "__other__";
/** CT-1 C6: the usual symbols, suggested while the picker is empty. */
const SYMBOL_ROWS: readonly PickedSymbol[] = SYMBOLS.map((s) => ({ symbol: s, exchange: "NSE" }));
/** A strategy template in the strategy list (its strategy id comes from the server once the template is picked). */
const PICK = "template-pick:";
/** The chart timeframe a template starts on when it offers it (a template's settings stay its own defaults here). */
const TEMPLATE_TIMEFRAME = "15m";

/** Strategy templates step 2: `?template=D2` opens the form with that template picked; the idea library's "Adopt as a
 * paper strategy" (IDEA-1) adds `timeframe` and `set.<key>` settings as the pre-fill (strategyTemplates/link.ts). */
function templateFromUrl(): TemplateLink | null {
  try {
    return templateLinkFromSearch(window.location.search);
  } catch {
    return null;
  }
}

/**
 * UX2 fix 4 ("Trading bot in three taps", docs/design/UX_AUDIT_FULL.md P18): the v2 page at /deployments, shown only while
 * `ui_v2` is on (the v2 shell picks it; with the flag off today's Autopilot page renders as before).
 *
 * Top to bottom: the page's one primary action "Start a trading bot" (jumps to the form), "Your bots" as cards (name,
 * symbol, Running / Paused / Stopped, Pause / Resume / Stop), then "Start a trading bot": 1 a strategy with one sentence,
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
  // REV-H2: the stopped bot whose "Remove" asks for a confirm.
  const [confirmRemove, setConfirmRemove] = useState<Deployment | null>(null);
  const [botNote, setBotNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [stepUp, setStepUp] = useState<{ reason: string; retry: () => Promise<unknown> } | null>(null);
  // Strategy templates step 2 (ui_v2 + strategy_templates): the templates the organisation can adopt as paper strategies.
  const templatesOn = features.on("ui_v2") && features.on("strategy_templates");
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  // Job 6: the Copilot's draft arrives in the navigation state (with the Copilot switch on); the trader saves it himself.
  const location = useLocation();
  const navigate = useNavigate();
  const draft = features.on("ai_copilot") ? draftFromState(location.state) : null;
  // SB-UX SU6: the strategy builder's "Start on paper" - the saved strategy, its symbol and the contract it was tested with.
  const fromFlow = handoffFromState(location.state);
  const [preselect, setPreselect] = useState<Preselect | null>(null);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const formRef = useRef<HTMLElement | null>(null);
  const firstField = useRef<HTMLButtonElement>(null);

  // UI-28 U2.4: the bots' own open positions and their live P&L (one read each per refresh, only while a bot holds one).
  const [positions, setPositions] = useState<TradeRecord[] | null>(null);
  const [exposure, setExposure] = useState<PortfolioExposure | null>(null);
  // M55 S7: false while the engine that checks the bots is silent (GET /api/system/worker-status), read with the bots.
  const [checked, setChecked] = useState<boolean | null>(null);
  const loadBots = useCallback(async () => {
    Promise.resolve().then(() => api.workerStatus()).then((w) => setChecked(w?.bots_checked !== false)).catch(() => setChecked(null));
    try {
      const list = await api.listDeployments(true);
      setBots(list);
      setBotsFailed(false);
      if (list.some((b) => b.open_positions > 0)) {
        api.listPositions().then(setPositions).catch(() => setPositions(null));
        api.portfolioExposure(true).then(setExposure).catch(() => setExposure(null));
      } else setPositions(null);
    } catch {
      setBotsFailed(true);
    }
  }, []);

  async function exitBot(list: TradeRecord[]): Promise<number> {
    let closed = 0;
    for (const p of list) {
      try { await api.exitNow(p.id); closed += 1; } catch { /* intentional: the next one still exits; the note says how many closed */ }
    }
    await loadBots();
    return closed;
  }

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
      if (kind === "pause") await api.pauseDeployment(bot.id, "paused from the Trading bot page");
      else if (kind === "resume") await api.resumeDeployment(bot.id);
      else await api.stopDeployment(bot.id, "stopped from the Trading bot page");
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

  /** REV-H2: remove a stopped bot from the list (the existing DELETE; the server refuses a bot that is not stopped). */
  async function remove(bot: Deployment) {
    setConfirmRemove(null); setBusyBot(bot.id); setBotNote(null);
    const name = botName(bot, names);
    try {
      await api.deleteDeployment(bot.id);
      setBotNote({ kind: "done", text: `${name} is removed from your bots. Its closed trades stay in your trade history.` });
      await loadBots();
    } catch (e) {
      if (isStepUpError(e)) setStepUp({ reason: String(e), retry: () => remove(bot) });
      else setBotNote({ kind: "warn", text: `${name} was not removed. ${errorSentence(e)}` });
    } finally {
      setBusyBot(null);
    }
  }

  useEffect(() => {
    if (!fromFlow) return;
    setPreselect({ strategyId: fromFlow.strategyId, symbol: fromFlow.symbol, lots: null, contract: fromFlow.contract });
    setDraftNote(`"${fromFlow.name || "Your strategy"}" from the strategy builder is picked below, with the contract you tested. Check it and press Start on paper when you are ready.`);
    navigate(location.pathname + location.search, { replace: true, state: null });
    goToForm();
  }, [fromFlow?.strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  function goToForm() {
    formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    window.setTimeout(() => firstField.current?.focus({ preventScroll: true }), 300);
  }

  if (authLoading) return null;
  if (!user) {
    return (
      <div className="space-y-space-4">
        <PageTitleV2 title="Trading bot" subtitle="Your strategy trades with paper money here, so you can watch how it does." />
        <NoteV2>Sign in to start a trading bot.</NoteV2>
      </div>
    );
  }

  const shown = bots ? sortBots(bots, showStopped) : [];
  const stoppedCount = bots ? bots.filter((b) => botState(b) === "stopped").length : 0;
  const canChange = user.role !== "VIEWER";

  return (
    <div className="space-y-space-4">
      <PageTitleV2 title="Trading bot" subtitle="Your strategy trades with paper money here, so you can watch how it does. No real money is used."
        action={canChange ? <button type="button" className={BUTTON_PRIMARY} onClick={goToForm}>Start a trading bot</button> : undefined} />
      <PageIntro id="paper-bot" lines={["Pick a strategy and a symbol, then start it. The bot keeps checking the market even while the app is closed; pause or stop it any time."]} />

      {stepUp && (
        <StepUpDialog reason={stepUp.reason} onCancel={() => setStepUp(null)} onVerified={() => { const r = stepUp.retry; setStepUp(null); void r(); }} />
      )}

      <CardV2 title="Your bots" count={bots ? sortBots(bots).length : undefined} testId="bots"
        badge={bots && showsPaper(bots.map((b) => b.mode)) ? <PaperBadge /> : undefined}>
        {botsFailed && !bots ? <LoadFailedV2 what="your bots" />
          : !bots ? <LoadingRowsV2 rows={2} />
          : shown.length === 0 ? (
            <p className="text-t14 text-fg-muted">Start a trading bot below: it trades with paper money.</p>
          ) : (
            <>
            {checked === false && <p role="status" className="mb-space-2 text-t13 font-semibold text-warn" data-bots-not-checked>{BOTS_NOT_CHECKED}</p>}
            <ul className="divide-y divide-border-subtle">
              {shown.map((bot) => (
                <BotRow key={bot.id} bot={bot} name={botName(bot, names)} busy={busyBot === bot.id} disabled={busyBot !== null || !canChange}
                  confirming={confirmStop === bot.id} onAsk={() => setConfirmStop(bot.id)} onCancel={() => setConfirmStop(null)}
                  onAct={(kind) => void act(bot, kind)} onRemove={canChange ? () => setConfirmRemove(bot) : undefined}
                  held={botPositions(positions, bot.id).length ? (
                    <BotPositionsV2 positions={botPositions(positions, bot.id)} exposure={exposure} canTrade={canChange} onExit={exitBot} />
                  ) : undefined}
                  why={features.on("why_no_trade") ? <BotWhyV2 botId={bot.id} /> : undefined} />
              ))}
            </ul>
            </>
          )}
        {botNote && <div className="mt-space-3"><NoteV2 kind={botNote.kind}>{botNote.text}</NoteV2></div>}
        <ConfirmDialog open={confirmRemove !== null} onOpenChange={(o) => { if (!o) setConfirmRemove(null); }}
          title={`Remove ${confirmRemove ? botName(confirmRemove, names) : "this bot"}?`}
          consequence={removeConsequence} confirmLabel="Remove bot" busy={busyBot !== null}
          onConfirm={() => { if (confirmRemove) void remove(confirmRemove); }} note={null} />
        {stoppedCount > 0 && (
          <button type="button" className="mt-space-3 inline-flex min-h-11 items-center text-t14 font-medium text-brand hover:underline md:min-h-0"
            onClick={() => setShowStopped((v) => !v)}>
            {showStopped ? "Hide stopped bots" : `Show stopped bots (${stoppedCount})`}
          </button>
        )}
      </CardV2>

      {/* AUD-T4: the paper report card - his own closed paper trades per bot and per strategy (flag paper_report_card). */}
      {features.on("paper_report_card") && <ReportCardV2 names={names} />}

      {canChange && draft && (
        <CopilotDraftV2 key={draft.idea} draft={draft}
          onDiscard={() => navigate(location.pathname, { replace: true, state: null })}
          onSaved={(saved) => {
            setCustom((all) => [...all.filter((c) => c.id !== saved.id), saved]);
            setPreselect({ strategyId: saved.strategy_id, symbol: formSymbol(draft.symbol), lots: draft.lots });
            setDraftNote(`Saved as your strategy "${saved.config.name}". It is picked below: check the symbol and press Start on paper when you are ready.`);
            navigate(location.pathname, { replace: true, state: null });
            goToForm();
          }} />
      )}

      {canChange && (
        <section ref={formRef} className="scroll-mt-20 space-y-space-3">
          {draftNote && <NoteV2 kind="done" testId="draft-saved">{draftNote}</NoteV2>}
          <StartBotV2 library={library} custom={custom} hasBroker={hasBroker} firstField={firstField} onStarted={() => void loadBots()}
            defaults={defaults} approvalAllowed templates={templates} initialTemplate={templateFromUrl()}
            preselect={preselect} />
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

/** REV-H2: what "Remove" on a stopped bot does, said before it happens. */
export const removeConsequence = "It leaves your list of bots. Its closed trades stay in your trade history, and you can start the same strategy on this symbol again.";

export function BotRow({ bot, name, busy, disabled, confirming, onAsk, onCancel, onAct, onRemove, held, why }: {
  bot: Deployment; name: string; busy: boolean; disabled: boolean; confirming: boolean;
  onAsk: () => void; onCancel: () => void; onAct: (kind: "pause" | "resume" | "stop") => void;
  /** REV-H2: "Remove" on a stopped bot (the page asks first); absent: no Remove. */
  onRemove?: () => void;
  /** UI-28 U2.4: the bot's own open positions (P&L, legs, exit), when it has any. */
  held?: ReactNode;
  /** AUD-T3: "Why no trade?" - the last closed bars with their reasons (flag `why_no_trade`). */
  why?: ReactNode;
}) {
  const state = botState(bot);
  const status = botStatusText(bot);
  return (
    <li className="space-y-space-2 py-space-3 first:pt-0 last:pb-0" data-bot={bot.id}>
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-space-2 text-t15 font-semibold text-fg">
            <span className="min-w-0 break-words">{name}</span>
            {bot.mode === "LIVE" && <LiveBadge />}
          </p>
          {/* UX-11b C5: what it trades and how long it holds, as two facts (chips), not a sentence. */}
          <p className="flex flex-wrap gap-space-1" data-bot-what>
            {botWhat(bot).split(" · ").map((part) => <Chip key={part} tone="neutral" variant="meaning">{part[0].toUpperCase() + part.slice(1)}</Chip>)}
          </p>
          <p className="mt-space-1 flex flex-wrap items-center gap-space-2 text-t13 text-fg-muted">
            <StateChipV2 tone={state === "paused" ? "warn" : "neutral"} check={state === "running"}>{status}</StateChipV2>
            {state !== "stopped" && <span>{lastChecked(bot.last_evaluated_at)}</span>}
            {state !== "stopped" && <span data-bot-signal>{lastSignal(bot.last_signal_at)}</span>}
            {whyNoTrade(bot) && <Chip tone="warn" variant="meaning" icon="!" name="why-no-trade">{whyNoTrade(bot)}</Chip>}
            {botWaitText(bot) && <span data-bot-wait>{botWaitText(bot)}</span>}
            {bot.open_positions > 0 && !held && <span>{bot.open_positions} open {bot.open_positions === 1 ? "trade" : "trades"}</span>}
            {state !== "stopped" && entryModeText(bot) && <span data-bot-entry-mode>{entryModeText(bot)}</span>}
          </p>
          {held}
          {state !== "stopped" && why}
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
        {/* REV-H2: a stopped bot can be removed from the list (it asks first). Only once no trade of it is open, so its
            open trade and its exit stay right here until it closes. */}
        {state === "stopped" && onRemove && bot.open_positions === 0 && (
          <div className="flex shrink-0 gap-space-2">
            <button type="button" className={BUTTON_SECONDARY} disabled={disabled} aria-busy={busy || undefined} onClick={onRemove} data-remove>
              <Trash2 size={16} aria-hidden /> Remove
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

/** The "Pick a strategy" rows, in their groups (Ready-made, My strategies, More from the Library, Strategy templates). */
export function strategyOptions(ready: readonly StrategyInfo[], custom: readonly CustomStrategyResponse[], others: readonly StrategyInfo[],
  templates: readonly StrategyTemplate[]): ComboOption[] {
  return [
    ...ready.map((s) => ({ value: s.id, label: s.name, group: "Ready-made" })),
    ...custom.map((c) => ({ value: c.strategy_id, label: c.config.name, group: "My strategies" })),
    ...others.map((s) => ({ value: s.id, label: s.name, group: "More from the Library" })),
    ...templates.map((t) => ({ value: PICK + t.id, label: t.name, group: "Strategy templates" })),
  ];
}

export function StartBotV2({ library, custom, hasBroker, firstField, onStarted, defaults = null, approvalAllowed = false, templates = [],
  initialTemplate = null, preselect = null }: {
  library: StrategyInfo[] | null; custom: CustomStrategyResponse[]; hasBroker: boolean | null;
  firstField: RefObject<HTMLButtonElement>; onStarted: () => void;
  /** Settings part C: the user's own defaults (a pre-fill, shown and switchable here); null = none. */
  defaults?: TradingDefaults | null; approvalAllowed?: boolean;
  /** Strategy templates step 2: templates the user can adopt here (empty while the flag is off). */
  templates?: StrategyTemplate[]; initialTemplate?: TemplateLink | null;
  /** Job 6: the strategy the trader just saved from the Copilot's draft, its symbol and the size his idea named. */
  preselect?: Preselect | null;
}) {
  const ids = { strategy: useId(), symbol: useId(), other: useId(), line: useId(), defaults: useId(), timeframe: useId(), stop: useId(),
    target: useId(), times: useId(), wave: useId(), share: useId(), exitNotes: useId(), construction: useId(), width: useId(), startWhy: useId(),
    entry: useId(), band: useId(), mode: useId() };
  const [useDefaults, setUseDefaults] = useState(true);
  const ready = useMemo(() => (library ?? []).filter((s) => READY.some((r) => r.id === s.id))
    .sort((a, b) => READY.findIndex((r) => r.id === a.id) - READY.findIndex((r) => r.id === b.id)), [library]);
  const others = useMemo(() => (library ?? []).filter((s) => !READY.some((r) => r.id === s.id)), [library]);
  const [strategyId, setStrategyId] = useState("");
  const [symbolChoice, setSymbolChoice] = useState("NIFTY 50");
  const [otherSymbol, setOtherSymbol] = useState("");
  const auth = useOptionalAuth();
  // CT-1 C6: a picked symbol of the usual list keeps its choice (a strategy then sets its own); any other is "another".
  const chooseSymbol = (s: string) => { if (SYMBOLS.includes(s)) setSymbolChoice(s); else { setSymbolChoice(OTHER); setOtherSymbol(s); } };
  const [busy, setBusy] = useState(false);
  // REV-H3: how the bot trades the option when nothing says it yet (a seller pack with no default): empty until chosen.
  const [construction, setConstruction] = useState<BotConstruction | "">("");
  const [widthText, setWidthText] = useState("");
  // M68 PR-1 (TS1-010 / 012): how this bot sends its entries - his Trade settings, else asked here (nothing pre-selected).
  const ownSettings = useTradeSettings(true);
  const mine = orderDefaultsOf(ownSettings);
  const [entryStyle, setEntryStyle] = useState<"" | "MARKET" | "PROTECTED_LIMIT">("");
  const [bandText, setBandText] = useState("");
  // M68 PR-6 (TS1-060): whether this bot places entries by itself or asks first - his Trade settings, else asked here.
  const ownMode = approvalModeOf(ownSettings);
  const [entryModePick, setEntryModePick] = useState<"" | EntryModeChoice>("");
  const [result, setResult] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [touched, setTouched] = useState(false);
  // Strategy templates step 2: the picked template, its chart timeframe and the server's pre-fill for them (nothing is
  // created until "Start on paper"; the template keeps its usual settings here).
  const picked = strategyId.startsWith(PICK) ? templates.find((t) => t.id === strategyId.slice(PICK.length)) ?? null : null;
  const [templateTimeframe, setTemplateTimeframe] = useState("");
  const [adoption, setAdoption] = useState<TemplateAdoption | null>(null);
  const [adoptFailed, setAdoptFailed] = useState(false);
  // Section 5: the template's exits as the user's own choice (empty = the template's defaults)
  const [exitPick, setExitPick] = useState<ExitPick>(EMPTY_PICK);
  const urlTemplateApplied = useRef(false);

  useEffect(() => {
    const t = initialTemplate ? templates.find((x) => x.id === initialTemplate.template) : undefined;
    if (urlTemplateApplied.current || !initialTemplate || !t) return;
    urlTemplateApplied.current = true;
    setStrategyId(PICK + t.id);
    // Day-one fix 6: an idea's link (it carries the idea's settings) starts on a stock, never a quiet index option.
    if (Object.keys(initialTemplate.params).length > 0) setSymbolChoice((cur) => (cur === "NIFTY 50" ? IDEA_SYMBOL : cur));
    if (initialTemplate.timeframe && t.timeframes.some((x) => x.value === initialTemplate.timeframe)) setTemplateTimeframe(initialTemplate.timeframe);
  }, [templates, initialTemplate]);

  useEffect(() => {
    if (!picked) return;
    if (!picked.timeframes.some((t) => t.value === templateTimeframe)) {
      setTemplateTimeframe(picked.timeframes.some((t) => t.value === TEMPLATE_TIMEFRAME) ? TEMPLATE_TIMEFRAME : picked.default_timeframe ?? "");
    }
  }, [picked, templateTimeframe]);

  // The link's settings (the idea library's pre-fill) apply to the template the link named, typed as it declares them.
  const linkSettings = useMemo(() => (picked && initialTemplate && picked.id === initialTemplate.template
    ? typedSettings(picked, initialTemplate.params) : {}), [picked, initialTemplate]);
  const settingsKey = JSON.stringify(linkSettings);
  const fromLink = settingsKey !== "{}";

  const pickedId = picked?.id ?? null;
  useEffect(() => { setExitPick(EMPTY_PICK); }, [pickedId]);

  useEffect(() => {
    setAdoption(null); setAdoptFailed(false);
    if (!pickedId || !templateTimeframe) return;
    let live = true;
    const params = JSON.parse(settingsKey) as Record<string, number | string | boolean>;
    templatesApi.adopt(pickedId, fromLink ? { timeframe: templateTimeframe, params } : { timeframe: templateTimeframe })
      .then((a) => { if (live) setAdoption(a); })
      .catch(() => { if (live) setAdoptFailed(true); });
    return () => { live = false; };
  }, [pickedId, templateTimeframe, settingsKey, fromLink]);

  // Job 6: the strategy the trader saved from the Copilot's draft, with the symbol his idea named (a pre-fill he can change).
  useEffect(() => {
    if (!preselect) return;
    setStrategyId(preselect.strategyId);
    if (preselect.symbol) {
      if (SYMBOLS.includes(preselect.symbol)) setSymbolChoice(preselect.symbol);
      else { setSymbolChoice(OTHER); setOtherSymbol(preselect.symbol); }
    }
    setResult(null);
  }, [preselect]);

  // The first ready-made strategy, with the symbol it is shown with.
  useEffect(() => {
    // (a template from the link, picked in this same commit, wins: the effect above already set it)
    if (strategyId || !library?.length || urlTemplateApplied.current) return;
    const first = ready[0] ?? library[0];
    setStrategyId(first.id);
    const r = READY.find((x) => x.id === first.id);
    if (r) setSymbolChoice(r.symbol);
  }, [library, ready, strategyId]);

  if (library === null) return <CardV2 title="Start a trading bot"><LoadingRowsV2 rows={3} /></CardV2>;
  if (library.length === 0 && custom.length === 0) {
    return <CardV2 title="Start a trading bot"><LoadFailedV2 what="the strategy library" /></CardV2>;
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
  // SB-UX SU6: the contract the strategy builder tested replaces the form's own while that strategy stays picked.
  const testedContract = preselect && preselect.strategyId === strategyId ? preselect.contract ?? null : null;
  // REV-H3: a request that names no side and no structure asks for them here (nothing is pre-chosen); a structure with
  // no width of the user's own asks for the width.
  const swing = isSwing(info);
  const choices = constructionChoices(swing);
  const pickedConstruction = choices.some((c) => c.value === construction) ? construction : "";
  const askConstruction = !testedContract && needsConstruction(built);
  const askWidth = !testedContract && needsWidth(withConstruction(built, pickedConstruction, null));
  const constructed = testedContract ? built : withConstruction(built, pickedConstruction, widthOf(widthText));
  const notTaken = useDefaults ? swingNotTaken(defaults, { index: isIndex(symbol), swing, approvalAllowed }, built.instrument_kind) : null;
  // Job 6: the size the trader's own idea named (his saved strategy only).
  const ideaLots = preselect && preselect.strategyId === strategyId ? preselect.lots : null;
  const ideaSized = withIdeaLots(constructed, ideaLots);
  const withLots = withTestedContract(ideaSized, testedContract);
  // A template reads its own candles (the adopted timeframe) and holds as the server said (Daily: for days).
  const adopted = picked && adoption ? { ...withLots, timeframe: adoption.deployment.timeframe, holding: adoption.deployment.holding } : withLots;
  // Section 5: the exit choices this template offers for the instrument the bot will trade; a default choice sends nothing
  const exitChoices = picked && adoption?.exits ? adoption.exits : null;
  const cls = exitClass(adopted);
  const exitsNow = exitChoices ? effectivePick(exitPick, exitChoices, cls) : null;
  const exitIssue = exitChoices && exitsNow ? exitProblem(exitsNow, exitChoices) : null;
  const exitsBody = exitChoices && exitsNow ? templateExitsBody(exitsNow, exitChoices, cls) : null;
  const request = exitsBody ? { ...adopted, template_exits: exitsBody } : adopted;
  const askEntry = !mine.botOrderStyle;                     // his Trade settings has none: the form asks
  const style = mine.botOrderStyle ?? (entryStyle || null);
  const askBand = style === "PROTECTED_LIMIT" && mine.protectionPct === undefined;
  const band = askBand ? bandOf(bandText) : null;
  const sent = askEntry && style
    ? { ...request, order_style: style, ...(band !== null ? { market_protection_pct: band } : {}) }
    : askBand && band !== null ? { ...request, market_protection_pct: band } : request;
  // his "Ask me first" saved as a default (My trading defaults) already sets entry_mode on the request; else his choice here
  const askMode = !ownMode && !sent.entry_mode;
  const sentWithMode = askMode && entryModePick ? { ...sent, entry_mode: entryModePick } : sent;
  const summary = startSummaryParts(symbol, info, sentWithMode);
  const entryWhy = askEntry && !style ? ENTRY_STYLE_MISSING : askBand && band === null ? BAND_MISSING
    : askMode && !entryModePick ? MODE_MISSING : null;
  const startWhy = (effectiveId ? constructionProblem(sent) : null) ?? (effectiveId ? entryWhy : null);
  const widthLabel = (s: OptionStrategy | undefined) => (s && (WINGED_STRUCTURES.includes(s) || s === "LONG_BUTTERFLY") ? "Wing width" : "Short strike distance");
  const setExit = (patch: Partial<ExitPick>) => { setExitPick((p) => ({ ...(exitsNow ?? p), ...patch })); setResult(null); };

  async function start() {
    setTouched(true);
    if (problem || !effectiveId || exitIssue || startWhy) return;
    setBusy(true); setResult(null);
    try {
      await api.createDeployment(sentWithMode);
      setResult({ kind: "done", text: `Started on paper: ${cleanSymbol(symbol)}. It shows under Your bots and starts checking the market on its next round.` });
      onStarted();
    } catch (e) {
      // REV-H1: a 409 names its own reason (no broker, contracts not loaded, a bot already running, ...): the shared
      // sentence for that reason, never one guess for all of them.
      setResult({ kind: "warn", text: e instanceof ApiError && e.status === 409 ? errorSentence(e)
        : e instanceof ApiError && e.status === 403 ? "Your role cannot start a bot. Ask your organisation's owner."
        : "The bot could not be started just now. Try again in a moment." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Start a trading bot" testId="start">
      <div className="space-y-space-4">
        {hasBroker === false && (
          <NoteV2 kind="warn">
            A trading bot reads live prices from your broker. <Link to={BROKER_PATH} className="font-medium text-brand hover:underline">Connect your broker</Link> first.
          </NoteV2>
        )}
        <div>
          <label htmlFor={ids.strategy} className={LABEL}><StepNo n={1} />Pick a strategy</label>
          <Combobox id={ids.strategy} label="Pick a strategy" buttonRef={firstField} value={strategyId} describedBy={ids.line} className="w-full"
            onChange={(v) => {
              setStrategyId(v); setResult(null);
              const r = READY.find((x) => x.id === v);
              if (r && symbolChoice !== OTHER) setSymbolChoice(r.symbol);
            }}
            options={strategyOptions(ready, custom, others, templates)} />
        </div>
        {picked && (
          <div data-template={picked.id}>
            <label htmlFor={ids.timeframe} className={LABEL}>Chart timeframe</label>
            <Combobox id={ids.timeframe} label="Chart timeframe" value={templateTimeframe} className="w-full"
              onChange={(v) => { setTemplateTimeframe(v); setResult(null); }} options={picked.timeframes} />
            {/* UX-11 U1: where the settings come from and the template's own notes on demand, word for word; a failure or the wait stays visible. */}
            <div data-template-notes>
              {adoptFailed ? <p role="alert" className="mt-space-1 text-t13 text-fg-muted">This template cannot be set up just now. Try again in a moment.</p>
                : !adoption ? <p role="status" className="mt-space-1 text-t13 text-fg-muted">Getting the template ready…</p>
                : (
                  <Details id="paperbot.template" label="About this template" className="mt-space-1">
                    <p>{fromLink ? "Starts with the settings from the idea." : "Starts with the template's usual settings."}{adoption.notes.length > 0 && ` ${adoption.notes.join(" ")}`}</p>
                  </Details>
                )}
            </div>
            {/* Day-one fix 6: the settings this bot starts with, in words (from the idea, or the template's usual ones). */}
            {adoption && settingRows(picked, adoption.settings.params).length > 0 && (
              <div className="mt-space-2 rounded-control border border-subtle p-space-3" data-template-settings>
                <p className="text-t13 font-medium text-fg">{fromLink ? "The idea's settings" : "Its settings"}</p>
                <dl className="mt-space-1 grid grid-cols-1 gap-x-space-4 gap-y-space-1 text-t13 sm:grid-cols-2">
                  {settingRows(picked, adoption.settings.params).map((r) => (
                    <div key={r.key} className="flex justify-between gap-space-2 sm:block">
                      <dt className="text-fg-muted">{r.label}</dt>
                      <dd className="text-fg">{r.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
            {/* Day-one fix 1: test this template with these settings on past prices first (nothing starts). */}
            <Link to={testPathFor({ template: picked.id, timeframe: templateTimeframe || null, params: linkSettings })}
              className="mt-space-1 inline-flex min-h-11 items-center text-t14 font-medium text-brand hover:underline md:min-h-0" data-template-test>
              Test it first on past prices
            </Link>
          </div>
        )}
        <div>
          <label htmlFor={ids.symbol} className={LABEL}><StepNo n={2} />Pick a symbol</label>
          {/* CT-1 C6: the one symbol picker - the usual symbols first, any other by typing (kept when the field is left). */}
          <SymbolPicker id={ids.symbol} label="Pick a symbol" value={symbol} exchange="NSE" placeholder="For example RELIANCE" storageKey={recentKeyFor(auth?.user?.id)}
            suggestions={SYMBOL_ROWS} onPick={(p) => { chooseSymbol(cleanSymbol(p.symbol)); setResult(null); }} commitTyped
            onClear={() => { setSymbolChoice(OTHER); setOtherSymbol(""); setResult(null); }} onLeave={() => setTouched(true)}
            invalid={touched && !!problem} describedBy={touched && problem ? ids.other : undefined} className="w-full" />
          {touched && problem && <p id={ids.other} role="alert" className="mt-space-1 text-t13 text-warn">{problem}</p>}
        </div>
        {exitChoices && exitsNow && (
          <fieldset className="space-y-space-3 rounded-control border border-subtle p-space-3" data-template-exits aria-describedby={ids.exitNotes}>
            <legend className="px-space-1 text-t14 font-medium text-fg">Exits for this bot</legend>
            {stopOptions(exitChoices, cls).length > 1 ? (
              <div>
                <label htmlFor={ids.stop} className={LABEL}>Stop</label>
                <Combobox id={ids.stop} label="Stop" value={exitsNow.stop} className="w-full" onChange={(v) => setExit({ stop: v as StopSource })}
                  options={stopOptions(exitChoices, cls)} />
              </div>
            ) : (
              <div data-exit-stop>
                <p className={LABEL}>Stop</p>
                <p className="text-t14 text-fg">{stopOptions(exitChoices, cls)[0]?.label}</p>
              </div>
            )}
            <div>
              <label htmlFor={ids.target} className={LABEL}>Take profit</label>
              <Combobox id={ids.target} label="Take profit" value={exitsNow.target} className="w-full" onChange={(v) => setExit({ target: v as TargetRule })}
                options={targetOptions(exitChoices, cls)} />
            </div>
            {exitsNow.target === "R_MULTIPLE" && (
              <div>
                <label htmlFor={ids.times} className={LABEL}>Times my risk</label>
                <input id={ids.times} className={FIELD} inputMode="decimal" autoComplete="off" value={exitsNow.targetR}
                  aria-invalid={touched && !!exitIssue} onChange={(e) => setExit({ targetR: e.target.value })} />
              </div>
            )}
            {exitsNow.target === "WAVE_PROJECTION" && (
              <div className="grid gap-space-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={ids.wave} className={LABEL}>Measured on</label>
                  <Combobox id={ids.wave} label="Measured on" value={exitsNow.baseWave} className="w-full" onChange={(v) => setExit({ baseWave: v })}
                    placeholder="Pick a wave" options={exitChoices.base_waves} />
                </div>
                <div>
                  <label htmlFor={ids.share} className={LABEL}>Share of that wave</label>
                  <input id={ids.share} className={FIELD} inputMode="decimal" autoComplete="off" value={exitsNow.ratio}
                    aria-invalid={touched && !!exitIssue} onChange={(e) => setExit({ ratio: e.target.value })} />
                </div>
              </div>
            )}
            <ul id={ids.exitNotes} className="list-disc space-y-space-1 pl-space-4 text-t13 text-fg-muted" data-exit-notes>
              {exitNotes(exitsNow, cls).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {touched && exitIssue && <p role="alert" className="text-t13 text-warn">{exitIssue}</p>}
          </fieldset>
        )}
        {(askConstruction || askWidth) && (
          <div className="space-y-space-3 rounded-control border border-subtle p-space-3" data-construction>
            {askConstruction && (
              <div>
                <label htmlFor={ids.construction} className={LABEL}>How this bot trades the option</label>
                <Combobox id={ids.construction} label="How this bot trades the option" value={pickedConstruction} className="w-full"
                  placeholder="Choose one" options={choices}
                  onChange={(v) => { setConstruction(v as BotConstruction); setResult(null); }} />
                <p className="mt-space-1 text-t13 text-fg-muted">
                  {swing ? SWING_CONSTRUCTION_NOTE : "The strategy only gives the side. Nothing is chosen for you."}
                </p>
              </div>
            )}
            {askWidth && (
              <div>
                <label htmlFor={ids.width} className={LABEL}>{widthLabel(withConstruction(built, pickedConstruction, null).option_strategy)} (strike steps)</label>
                <input id={ids.width} className={FIELD} inputMode="numeric" autoComplete="off" value={widthText} placeholder="1 to 20"
                  aria-invalid={widthText.trim() !== "" && widthOf(widthText) === null ? true : undefined}
                  onChange={(e) => { setWidthText(e.target.value); setResult(null); }} />
              </div>
            )}
          </div>
        )}
        {askMode && (
          <div className="space-y-space-3 rounded-control border border-subtle p-space-3" data-entry-mode>
            <div>
              <label htmlFor={ids.mode} className={LABEL}>New entries</label>
              <Combobox id={ids.mode} label="New entries" value={entryModePick} className="w-full" placeholder="Choose one"
                options={MODE_OPTIONS} dataAttrs={{ "data-entry-input": "mode" }}
                onChange={(v) => { setEntryModePick(v as EntryModeChoice); setResult(null); }} />
              {entryModePick && <SaveAsDefault key={entryModePick} fieldKey={APPROVAL_MODE_KEY} value={entryModePick} />}
            </div>
          </div>
        )}
        {(askEntry || askBand) && (
          <div className="space-y-space-3 rounded-control border border-subtle p-space-3" data-entry-style>
            {askEntry && (
              <div>
                <label htmlFor={ids.entry} className={LABEL}>How this bot sends its entries</label>
                <Combobox id={ids.entry} label="How this bot sends its entries" value={entryStyle} className="w-full" placeholder="Choose one"
                  options={ENTRY_STYLES} dataAttrs={{ "data-entry-input": "style" }}
                  onChange={(v) => { setEntryStyle(v as "MARKET" | "PROTECTED_LIMIT"); setResult(null); }} />
                {entryStyle && <SaveAsDefault key={entryStyle} fieldKey={ORDER_TYPE_KEY} value={entryStyle} />}
              </div>
            )}
            {askBand && (
              <div>
                <label htmlFor={ids.band} className={LABEL}>Price protection (%)</label>
                <input id={ids.band} className={FIELD} inputMode="decimal" autoComplete="off" value={bandText} placeholder="0.05 to 5"
                  aria-invalid={bandText.trim() !== "" && band === null ? true : undefined} data-entry-input="band"
                  onChange={(e) => { setBandText(e.target.value); setResult(null); }} />
                {band !== null && <SaveAsDefault fieldKey={PROTECTION_KEY} value={band} />}
              </div>
            )}
          </div>
        )}
        <div className="space-y-space-2">
          <p className="text-t14 text-fg-muted" data-start-summary>{summary.line}</p>
          {/* UX-11 U1: the one visible line above; the strategy's own sentence and the rest of the summary on demand. */}
          <Details id="paperbot.start" className="mt-0">
            {line && <p id={ids.line}>{line}</p>}
            <p>{summary.more}</p>
          </Details>
          {ideaSized !== constructed && <p className="text-t13 text-fg-muted" data-idea-lots>The size comes from your idea.</p>}
          {testedContract && <p className="text-t13 text-fg-muted" data-tested-contract>The option structure, strikes and expiry are the ones you tested.</p>}
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
          {/* UI-28 U7.4: the exact contract each signal side would trade now, before anything starts. */}
          {notTaken && <p className="text-t13 text-fg-muted" data-defaults-not-taken>{notTaken}</p>}
          {/* REV-H3: no preview of a contract nobody chose yet (the preview would fill in a side). */}
          {effectiveId && !startWhy && <ContractPreviewV2 request={request} />}
          <button type="button" className={`${BUTTON_PRIMARY} w-full sm:w-auto`} disabled={busy || !effectiveId || !!startWhy} aria-busy={busy || undefined}
            aria-describedby={startWhy ? ids.startWhy : undefined} onClick={() => void start()}>
            {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
            Start on paper
          </button>
          {startWhy && <p id={ids.startWhy} role="status" className="text-t13 text-fg-muted" data-start-why>{startWhy}</p>}
        </div>
        {result && <NoteV2 kind={result.kind}>{result.text}</NoteV2>}
      </div>
    </CardV2>
  );
}
