import { ALERTS_PATH } from "./getStarted";
import { Details } from "../ui/numbersFirst";
import { BellRing, Mail, Send, Smartphone } from "lucide-react";
import { useEffect, useId, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { currentPushEnv, pushReadiness, vapidKeyBytes } from "../alerts/push";
import { ChipV2, LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import type { TelegramLinkCode } from "../alerts/rules";
import TelegramConnectV2 from "../alerts/TelegramConnectV2";
import type { AlertChannel, AlertChannelType, OHLCVBar, PaperRiskStatus, StrategyInfo } from "../types";
import { STORED_HISTORY_NOTE } from "../backtest/pricesLine";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { useFeatures } from "../ui/useFeatures";
import { formatNumber, formatTimeIST } from "../ui/format";
import {
  LIMIT_FIELDS, LIMIT_KEYS, RISK_DEFAULTS, RISK_HELP, RISK_LABELS, SUGGESTIONS, limitsBody, paperDeployment, perTradeText, riskErrors, riskNumbers,
  riskSentence, testSummary,
  type RiskField, type RiskForm, type StrategySuggestion,
} from "./model";
import { Skeleton } from "../components/primitives";

/**
 * UI2-7 (UI v2 proposal §9, §11): the setup wizard's four steps. Each uses the APIs that already exist and says what
 * happened, then what to do next. Nothing here can trade LIVE: the strategy step only ever creates a PAPER deployment.
 */

export interface StepProps {
  /** The server says this step is done (worked out from the trader's own rows). */
  done: boolean;
  /** Something changed that may finish the step: read the progress again. */
  onChanged: () => void;
}

/** What went wrong, in plain words, with what to do next. Never a stack trace or a status code. */
export function problemText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return e.message;
    if (e.status === 403) return "Your role cannot change this. Ask your organisation's owner.";
  }
  return fallback;
}

export function ActionV2({ primary = false, busy = false, children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean; busy?: boolean }) {
  return (
    <button type="button" {...rest} disabled={busy || rest.disabled} aria-busy={busy || undefined}
      className={`${primary ? LINK_PRIMARY : LINK_SECONDARY} disabled:cursor-not-allowed disabled:opacity-60 ${className}`}>
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  );
}

/** A result line under an action: success in the text colour with a tick tone, a problem in the warning tone. */
export function Note({ tone, children }: { tone: "ok" | "problem"; children: ReactNode }) {
  return (
    <p role={tone === "problem" ? "alert" : "status"} className={`text-t13 ${tone === "problem" ? "text-warn" : "text-fg"}`}>{children}</p>
  );
}

const P = "text-t14 text-fg-muted";

// --- Step 1 -----------------------------------------------------------------------------------------------------------

/**
 * Step 1, "Connect your broker". The v2 Connect flow (§10, UI2-6) is not built yet - it waits for PR #303 - so this step
 * links to today's broker settings, where a broker is connected, and offers paper trading first (the step's skip).
 */
export function BrokerStepV2({ done, onSkip, intro }: StepProps & { onSkip: () => void; intro?: ReactNode }) {
  if (done) {
    return (
      <div className="space-y-space-3">
        <p className="text-t14 text-fg">Your broker account is connected. Prices and your positions come from it.</p>
        {intro && <Details id="setup.broker">{intro}</Details>}
        <Link to={CONNECT_BROKER_PATH} className={LINK_SECONDARY}>Open broker settings</Link>
      </div>
    );
  }
  return (
    <div className="space-y-space-3">
      {/* UX-11 U1 / PROSE-1: the step's line, the wizard's intro and the paper-first note on demand, word for word. */}
      <Details id="setup.broker" className="mt-0">
        <p className="text-t14 text-fg">Connect the broker account you trade with. Prices and your positions then come from it.</p>
        {intro}
        <p className={P}>Want to practise first? Trade on paper: no order reaches your broker. You can connect a broker at any time.</p>
      </Details>
      <div className="flex flex-col gap-space-2 sm:flex-row">
        <Link to={CONNECT_BROKER_PATH} className={LINK_PRIMARY}>Connect your broker</Link>
        <ActionV2 onClick={onSkip}>I&apos;ll trade on paper first</ActionV2>
      </div>
    </div>
  );
}

// --- Step 2 -----------------------------------------------------------------------------------------------------------

function NumberField({ field, value, error, prefix, suffix, help, placeholder, onChange, onBlur }: {
  field: RiskField; value: string; error?: string; prefix?: string; suffix?: string; help?: string; placeholder?: string;
  onChange: (v: string) => void; onBlur: () => void;
}) {
  const id = useId();
  const described = [error ? `${id}-error` : "", help ? `${id}-help` : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className="space-y-space-1">
      <label htmlFor={id} className="block text-t13 font-medium text-fg">{RISK_LABELS[field]}</label>
      <div className={`flex h-11 items-center rounded-control border bg-surface-1 px-space-3 focus-within:border-brand md:h-9 ${error ? "border-warn" : "border-border"}`}>
        {prefix && <span className="pr-space-1 text-t14 text-fg-muted" aria-hidden>{prefix}</span>}
        <input id={id} inputMode="decimal" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} onBlur={onBlur}
          aria-invalid={error ? true : undefined} aria-describedby={described} data-field={field}
          className="h-full min-w-0 flex-1 bg-transparent text-t15 tabular-nums text-fg outline-none placeholder:text-fg-muted" />
        {suffix && <span className="pl-space-1 text-t14 text-fg-muted" aria-hidden>{suffix}</span>}
      </div>
      {error && <p id={`${id}-error`} className="text-t13 text-warn">{error}</p>}
      {help && !error && <p id={`${id}-help`} className="text-t12 text-fg-muted">{help}</p>}
    </div>
  );
}

const SUFFIX: Partial<Record<RiskField, string>> = { riskPerTradePct: "%", dailyLossPct: "%", minRr: ": 1" };

/** The form's starting values (TT-12): each limit the trader set himself, every other one empty ("Not set" - no number
 * is filled in for him); the starting capital from the paper account (`GET /paper/account`), or ₹1,00,000 when it is
 * not known. */
export function riskStart(status: PaperRiskStatus | null, startingCapital: number | null): RiskForm {
  const capital = startingCapital !== null && startingCapital > 0 ? startingCapital : RISK_DEFAULTS.capital;
  const own = (f: (typeof LIMIT_FIELDS)[number]): string => {
    const key = LIMIT_KEYS[f];
    const value = status?.settings?.[key];
    return status?.set?.includes(key) && typeof value === "number" && Number.isFinite(value) ? String(value) : "";
  };
  return {
    capital: formatNumber(Math.round(capital)),             // "1,00,000"; the commas are read back (riskNumbers)
    riskPerTradePct: own("riskPerTradePct"), dailyLossPct: own("dailyLossPct"), maxTradesPerDay: own("maxTradesPerDay"), minRr: own("minRr"),
  };
}

const notFound = (e: unknown) => e instanceof ApiError && e.status === 404;

/** Step 2 (TT-12): the trader fills in his own limits - risk per trade, daily loss limit, trades a day, lowest reward to
 * risk - in empty boxes; an empty box is "Not set" (no limit). The "most you can lose today" sentence follows what he
 * types. The limits are saved through the existing `PUT /paper/risk` (an emptied one is sent as null), then a changed
 * starting capital through `PUT /paper/account`. With the paper account off (404) the capital field is not shown and
 * nothing is sent for it. */
export function RiskStepV2({ done, onChanged }: StepProps) {
  const [status, setStatus] = useState<PaperRiskStatus | null | "off">(null);
  /** The paper account's starting capital, or "off" (no paper account) / null (not read): then no field. */
  const [account, setAccount] = useState<number | "off" | null>(null);
  const [form, setForm] = useState<RiskForm | null>(null);
  const [touched, setTouched] = useState<Partial<Record<RiskField, boolean>>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "problem"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const risk = api.paperRisk().then((r) => r as PaperRiskStatus | "off").catch((e: unknown) => (notFound(e) ? "off" as const : null));
    const capital = api.paperAccount().then((a) => a.starting_capital).catch((e: unknown) => (notFound(e) ? "off" as const : null));
    void Promise.all([risk, capital]).then(([r, c]) => {
      if (!alive) return;
      setStatus(r);
      setAccount(c);
      setForm(riskStart(r && r !== "off" ? r : null, typeof c === "number" ? c : null));
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (status === "off") {
    return (
      <div className="space-y-space-3">
        <p className="text-t14 text-fg">Your organisation sets its risk limits on the Risk page.</p>
        <Link to="/risk-management" className={LINK_PRIMARY}>Open the Risk page</Link>
      </div>
    );
  }
  if (!form) return <Skeleton className="h-40" label="Loading" />;

  const withCapital = typeof account === "number";            // unknown or off: no field, nothing sent for it
  const errors = riskErrors(form, { capital: withCapital });
  const shown = (f: RiskField) => (touched[f] ? errors[f] : undefined);
  const n = riskNumbers(form);
  // Without the field, the limits are a share of the capital the server measures them from.
  const basis = withCapital ? n.capital : status && typeof status === "object" ? status.capital_basis : RISK_DEFAULTS.capital;
  const sentence = errors.capital || errors.dailyLossPct ? null : riskSentence(basis, n.dailyLossPct);
  const perTrade = errors.capital || errors.riskPerTradePct ? null : perTradeText(basis, n.riskPerTradePct);
  const set = (f: RiskField) => (v: string) => { setForm({ ...form, [f]: v }); setNote(null); };
  const blur = (f: RiskField) => () => setTouched((t) => ({ ...t, [f]: true }));

  async function save() {
    setTouched({ capital: true, riskPerTradePct: true, dailyLossPct: true, maxTradesPerDay: true, minRr: true });
    if (Object.keys(errors).length) return;
    setBusy(true); setNote(null);
    let text: string;
    try {
      // The answer also says what waits: a loosening applies from the next day (backend paper/ticket.py save_settings).
      const saved = await api.setPaperRisk(limitsBody(n)) as
        PaperRiskStatus & { change?: { deferred?: Record<string, number | null>; from?: string | null } };
      setStatus(saved);
      const from = saved.change?.from;
      const none = LIMIT_FIELDS.every((f) => n[f] === null);
      text = from && Object.keys(saved.change?.deferred ?? {}).length > 0
        ? `Limits saved. Stricter limits apply now; a looser or cleared limit starts on ${from}.`
        : none ? "Saved. You have no limits: nothing stops a new paper trade. Add one here at any time."
          : "Limits saved. They apply to your new paper trades from now on.";
      onChanged();
    } catch (e) {
      setNote({ tone: "problem", text: e instanceof ApiError && e.status === 422 && e.detail ? `Not saved: ${e.detail}` : problemText(e, "Your limits could not be saved. Try again in a moment.") });
      setBusy(false);
      return;
    }
    if (typeof account === "number" && n.capital !== null && Math.round(account) !== n.capital) {
      try {
        const a = await api.setPaperCapital(n.capital);
        setAccount(a.starting_capital);
      } catch (e) {
        setNote({ tone: "problem", text: `${text} ${e instanceof ApiError && e.status === 409
          ? "Your starting capital stays as it was: it is fixed once your paper account has a trade."
          : notFound(e) ? "Your starting capital stays as it was." : problemText(e, "Your starting capital was not changed. Try again in a moment.")}` });
        setBusy(false);
        return;
      }
    }
    setNote({ tone: "ok", text });
    setBusy(false);
  }

  return (
    <form className="space-y-space-4" noValidate onSubmit={(e) => { e.preventDefault(); void save(); }} data-own-limits>
      <Details id="setup.limits" className="mt-0">
        <p className="text-t14 text-fg">Fill in your own limits. No number is filled in: leave a box empty for no limit.</p>
        <p className={P}>Your limits only stop new paper trades. Closing a trade always works.</p>
      </Details>
      {withCapital && (
        <div className="max-w-xs">
          <NumberField field="capital" prefix="₹" value={form.capital} error={shown("capital")} onChange={set("capital")} onBlur={blur("capital")} />
        </div>
      )}
      <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
        {LIMIT_FIELDS.map((f) => (
          <NumberField key={f} field={f} suffix={SUFFIX[f]} value={form[f]} error={shown(f)} help={RISK_HELP[f]} placeholder="Off"
            onChange={set(f)} onBlur={blur(f)} />
        ))}
      </div>
      {(sentence || perTrade) && (
        <div className="rounded-control bg-surface-2 px-space-3 py-space-2" data-risk-sentence>
          {sentence && <p className="text-t14 font-medium text-fg">{sentence}</p>}
          {perTrade && <p className="text-t13 text-fg-muted">{perTrade}</p>}
        </div>
      )}
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-center">
        <ActionV2 primary type="submit" busy={busy}>{done ? "Save changes" : "Save limits"}</ActionV2>
        {note && <Note tone={note.tone}>{note.text}</Note>}
      </div>
    </form>
  );
}

// --- Step 3 -----------------------------------------------------------------------------------------------------------

/** `stored` (owner order 23 Oct, flag backtest_stored_history): no usable broker - Daily tests run on ATP's stored history. */
interface Source { broker: string; intradayDays: number; dailyDays: number; stored?: boolean }

function SuggestionCard({ s, info, source, onChanged }: { s: StrategySuggestion; info: StrategyInfo; source: Source | null; onChanged: () => void }) {
  const [busy, setBusy] = useState<"test" | "start" | null>(null);
  const [test, setTest] = useState<{ tone: "ok" | "problem"; text: string } | null>(null);
  const [start, setStart] = useState<{ tone: "ok" | "problem"; text: string } | null>(null);

  async function runTest() {
    if (!source) return;
    setBusy("test"); setTest(null);
    const timeframe = info.timeframes[0] ?? "5m";
    const daily = timeframe === "day";
    const days = daily ? Math.min(365, source.dailyDays) : Math.min(30, source.intradayDays);
    try {
      let bars: OHLCVBar[];
      let label: string;
      let line = "";
      if (source.stored) {
        const res = await api.backtestBars(s.symbol, timeframe, days);
        bars = res.candles; label = res.source; line = ` ${res.line}`;
      } else {
        const res = await api.marketDataCandles([s.symbol], timeframe, days, "NSE", source.broker);
        bars = res.symbols[s.symbol.toUpperCase()]?.bars ?? Object.values(res.symbols)[0]?.bars ?? [];
        label = `broker:${res.source.broker}`;
      }
      if (!bars.length) throw new Error("no candles");
      const result = await api.backtest(info.id, s.symbol, timeframe, bars, null, label);
      setTest({ tone: "ok", text: testSummary(result, days, s.symbol) + line + (s.option ? " Tested on the index itself, not the option." : "") });
      onChanged();
    } catch (e) {
      // Owner order 23 Oct: on stored history the server's own sentence says what is missing ("Intraday backtests need a
      // connected broker...", "Daily history for X is not stored yet.").
      const plain = source.stored && e instanceof ApiError && e.status === 409 ? e.message : null;
      setTest({ tone: "problem", text: plain ?? problemText(e, "The test could not run on your broker's prices just now. Try again later, or open Test a strategy.") });
    } finally {
      setBusy(null);
    }
  }

  async function startPaper() {
    setBusy("start"); setStart(null);
    try {
      await api.createDeployment(paperDeployment(s));
      setStart({ tone: "ok", text: "Started as a paper bot. It trades with paper money only; see it on the Paper bot page." });
      onChanged();
    } catch (e) {
      setStart({ tone: "problem", text: e instanceof ApiError && e.status === 409
        // 409: this strategy already runs on paper for this symbol in the organisation (perhaps a teammate's), so nothing
        // new was started and this step is not done by it. Say so, and point to "Test it".
        ? `Nothing new was started: this strategy already runs on paper for ${s.symbol} in your organisation. Use Test it to try it yourself, or pick another one.`
        : problemText(e, "It could not be started just now. Try again, or set it up on the Paper bot page.") });
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="space-y-space-2 rounded-control border border-subtle p-space-3" data-suggestion={s.strategyId}>
      <div className="flex flex-wrap items-center gap-space-2">
        <ChipV2 kind="brand">{s.style}</ChipV2>
        <ChipV2>Paper</ChipV2>
      </div>
      <p className="text-t15 font-semibold text-fg">{info.name}</p>
      <p className={P}>{s.line}</p>
      <div className="flex flex-col gap-space-2 sm:flex-row">
        <ActionV2 busy={busy === "test"} disabled={!source || busy !== null} onClick={() => void runTest()}>Test it</ActionV2>
        <ActionV2 busy={busy === "start"} disabled={busy !== null} onClick={() => void startPaper()}>Start a paper bot</ActionV2>
      </div>
      {test && <Note tone={test.tone}>{test.text}</Note>}
      {start && <Note tone={start.tone}>{start.text}</Note>}
      {start?.tone === "ok" && <Link to="/deployments" className="inline-flex min-h-11 items-center text-t14 font-medium text-brand hover:underline md:min-h-0">Open Paper bot</Link>}
    </li>
  );
}

/** Step 3: three Library strategies (intraday, swing, options). "Test it" runs a quick backtest on the broker's prices;
 * "Start on Paper Autopilot" creates a PAPER deployment. LIVE is never offered here. */
export function StrategyStepV2({ onChanged }: StepProps) {
  const [library, setLibrary] = useState<StrategyInfo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const storedOn = useFeatures().on("backtest_stored_history");

  useEffect(() => {
    let alive = true;
    api.listStrategies().then((l) => { if (alive) setLibrary(l); }).catch(() => { if (alive) setFailed(true); });
    api.marketDataSources().then((r) => {
      const usable = r.sources.find((x) => x.usable && x.tier_a !== false);
      if (alive && usable) setSource({ broker: usable.broker, intradayDays: r.intraday_max_lookback_days || 30, dailyDays: r.daily_max_lookback_days || 365 });
      else if (alive && storedOn) setSource({ broker: "", intradayDays: 30, dailyDays: 365, stored: true });
    }).catch(() => { if (alive && storedOn) setSource({ broker: "", intradayDays: 30, dailyDays: 365, stored: true }); });
    return () => { alive = false; };
  }, [storedOn]);

  if (failed) return <p className={P}>We could not load the strategy library. Try again in a moment, or open the Library.</p>;
  if (!library) return <Skeleton className="h-40" label="Loading" />;
  const shown = SUGGESTIONS.flatMap((s) => {
    const info = library.find((x) => x.id === s.strategyId);
    return info ? [{ s, info }] : [];
  });
  return (
    <div className="space-y-space-3">
      <Details id="setup.strategy" className="mt-0"><p className="text-t14 text-fg">Pick one to try. A paper bot then trades it with paper money, so you can watch how it does.</p></Details>
      {!source && <p className={P}>Testing uses your broker&apos;s prices. Connect your broker to test a strategy first.</p>}
      {source?.stored && <p className={P}>{STORED_HISTORY_NOTE}</p>}
      <ul className="space-y-space-3">
        {shown.map(({ s, info }) => <SuggestionCard key={s.strategyId} s={s} info={info} source={source} onChanged={onChanged} />)}
      </ul>
      <Link to="/strategies" className="inline-flex min-h-11 items-center text-t14 font-medium text-brand hover:underline md:min-h-0">See every strategy in the Library</Link>
    </div>
  );
}

// --- Step 4 -----------------------------------------------------------------------------------------------------------

const CHANNELS: { type: AlertChannelType; name: string; icon: typeof Smartphone; missing: string }[] = [
  { type: "PUSH", name: "App notifications", icon: Smartphone, missing: "Off. Turn them on to get alerts on this device." },
  { type: "TELEGRAM", name: "Telegram", icon: Send, missing: "Connect it in one tap." },
  { type: "EMAIL", name: "Email", icon: Mail, missing: "Not set up. Email alerts are added in Settings, under Alert delivery." },
];

function channelStatus(c: AlertChannel | undefined, missing: string): string {
  if (!c) return missing;
  if (!c.enabled) return "Switched off in Settings.";
  if (c.last_error) return "The last alert did not go through. Send a test to check it.";
  if (c.last_delivered_at) return "Working. A test or an alert went through.";
  return "Set up. Send a test to check it works.";
}

/** Step 4: the alert channels (app push, Telegram, email) and a test send, through the existing alert APIs. */
export function AlertsStepV2({ onChanged }: StepProps) {
  const [channels, setChannels] = useState<AlertChannel[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Partial<Record<AlertChannelType, { tone: "ok" | "problem"; text: string }>>>({});
  const [code, setCode] = useState<TelegramLinkCode | null>(null);
  const readiness = pushReadiness(currentPushEnv());

  const refresh = () => api.listAlertChannels().then(setChannels).catch(() => setChannels((c) => c ?? []));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void refresh(); }, []);
  const note = (type: AlertChannelType, tone: "ok" | "problem", text: string) => setNotes((n) => ({ ...n, [type]: { tone, text } }));

  async function sendTest(type: AlertChannelType) {
    setBusy(`test-${type}`);
    try {
      const r = await api.testAlertChannel(type.toLowerCase());
      if (r.ok) note(type, "ok", "Test sent. Check that it arrived.");
      else note(type, "problem", "The test did not go through. Check this channel's details in Settings, under Alert delivery, then try again.");
      await refresh();
      onChanged();
    } catch (e) {
      note(type, "problem", problemText(e, "The test could not be sent just now. Try again in a moment."));
    } finally {
      setBusy(null);
    }
  }

  async function pushHere(floor: AlertChannel["min_severity"]) {
    setBusy("push-on");
    try {
      const { public_key } = await api.pushPublicKey();
      const registration = await navigator.serviceWorker.register("/sw.js");
      if (await Notification.requestPermission() !== "granted") {
        note("PUSH", "problem", "Notifications were not allowed, so nothing was turned on. Allow them in the browser's site settings and try again.");
        return;
      }
      const json = (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKeyBytes(public_key) })).toJSON();
      const label = `${navigator.platform || "device"} · ${/Chrome|Firefox|Safari|Edg/.exec(navigator.userAgent)?.[0] ?? "browser"}`;
      await api.upsertAlertChannel("push", { enabled: true, min_severity: floor,
        config: { subscription: { endpoint: json.endpoint, p256dh: json.keys?.p256dh, auth: json.keys?.auth, label } } });
      note("PUSH", "ok", "Notifications are on for this device. Send a test to check one arrives.");
      await refresh();
    } catch (e) {
      note("PUSH", "problem", problemText(e, "Notifications could not be turned on here. Try again, or use Telegram or email."));
    } finally {
      setBusy(null);
    }
  }

  async function linkTelegram() {
    setBusy("link");
    try {
      setCode(await api.telegramLinkCode());
      note("TELEGRAM", "ok", "Code ready. Send it to your organisation's bot as shown below.");
    } catch (e) {
      note("TELEGRAM", "problem", e instanceof ApiError && e.status === 409
        ? "Linking needs the Telegram bot to receive messages. Ask your organisation's owner to turn that on in Settings."
        : problemText(e, "A link code could not be made just now. Try again in a moment."));
    } finally {
      setBusy(null);
    }
  }

  if (!channels) return <Skeleton className="h-40" label="Loading" />;
  return (
    <div className="space-y-space-3">
      <Details id="setup.alerts" className="mt-0"><p className="text-t14 text-fg">Pick where alerts reach you when the app is closed: a lost broker login, a daily loss limit, a trade waiting for your approval.</p></Details>
      <ul className="space-y-space-3">
        {CHANNELS.map(({ type, name, icon: Icon, missing }) => {
          const c = channels.find((x) => x.channel_type === type);
          const n = notes[type];
          const canPush = type === "PUSH" && (readiness === "ask" || readiness === "granted");
          if (type === "TELEGRAM") {
            // UX2 fix 5: Telegram in one tap (the platform's bot). An organisation's own bot keeps "Link my Telegram"
            // (each member's own chat for the screen alerts of their rules).
            const ownBot = !!c && !c.config.platform_bot;
            return (
              <li key={type} className="space-y-space-2 rounded-control border border-subtle p-space-3" data-channel={type}>
                <TelegramConnectV2 onChanged={() => { void refresh(); onChanged(); }} />
                {ownBot && c.enabled && (
                  <div className="flex flex-col gap-space-2 sm:flex-row">
                    <ActionV2 busy={busy === "link"} disabled={busy !== null} onClick={() => void linkTelegram()}>Link my Telegram</ActionV2>
                  </div>
                )}
                {n && <Note tone={n.tone}>{n.text}</Note>}
                {code && (
                  <p className="rounded-control bg-surface-2 px-space-3 py-space-2 text-t14 text-fg">
                    Send <span className="font-mono font-semibold">{code.command}</span> to your organisation&apos;s bot in a private chat.
                    The code works once, until {formatTimeIST(code.expires_at)}.
                  </p>
                )}
              </li>
            );
          }
          return (
            <li key={type} className="space-y-space-2 rounded-control border border-subtle p-space-3" data-channel={type}>
              <div className="flex items-start gap-space-3">
                <Icon size={18} aria-hidden className="mt-px shrink-0 text-fg-muted" />
                <div className="min-w-0">
                  <p className="text-t15 font-semibold text-fg">{name}</p>
                  <p className={P}>{type === "PUSH" && !c && readiness === "ios-install-first"
                    ? "On iPhone and iPad, add the app to your Home Screen first, then turn them on there."
                    : channelStatus(c, missing)}</p>
                </div>
              </div>
              <div className="flex flex-col gap-space-2 sm:flex-row">
                {canPush && <ActionV2 busy={busy === "push-on"} disabled={busy !== null} onClick={() => void pushHere(c?.min_severity ?? "WARNING")}>
                  <BellRing size={16} aria-hidden /> Turn on here</ActionV2>}
                {c?.enabled && <ActionV2 busy={busy === `test-${type}`} disabled={busy !== null} onClick={() => void sendTest(type)}>Send a test</ActionV2>}
                {!c && type !== "PUSH" && <Link to={ALERTS_PATH} className={LINK_SECONDARY}>Open alert settings</Link>}
              </div>
              {n && <Note tone={n.tone}>{n.text}</Note>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
