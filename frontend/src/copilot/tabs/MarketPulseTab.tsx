import { AlertTriangle, CalendarClock, CheckCircle2, CircleHelp, ListChecks, Minus, RefreshCw, Rocket, Wallet, XCircle } from "lucide-react";
import { useState } from "react";
import { Badge, Button, EmptyState } from "../../components/primitives";
import type { BriefDay, DailyBrief } from "../../types";
import { friendlyError } from "../aiTask";
import RegimeDial, { type DialKind } from "../components/RegimeDial";
import SentimentGauge3D from "../components/SentimentGauge3D";
import TiltCard from "../components/TiltCard";
import { useCopilotData } from "../data";
import { useCopilotT } from "../i18n";
import MemoryPanel from "./pulse/MemoryPanel";
import ThesisPanel from "./pulse/ThesisPanel";
import { ageText, money, Panel, Stagger, StaggerItem, TabSkeleton } from "./shared";

/**
 * Market Pulse: today's read of the market in one screen - the 3D sentiment gauge, the regime dial, the briefing's
 * headline, the thesis (a reading of the data, never a call), today's events and the pre-flight checklist; then the
 * trader's own day, deployments and the market memory. Everything here is data the worker already read - no AI call.
 */
function DataStamp({ data }: { data: NonNullable<DailyBrief["market_data"]> }) {
  const t = useCopilotT();
  if (data.state === "fresh") return null;
  const text = data.state === "stale" ? t("pulse.stamp.stale", { age: ageText(data.age_minutes ?? 0) }) : data.state === "suspect" ? t("pulse.stamp.placeholder") : t("pulse.stamp.none");
  return <Badge tone="warn" className="font-bold uppercase tracking-wider">{text}</Badge>;
}

function Meter({ label, used, max, isMoney = false }: { label: string; used: number; max: number; isMoney?: boolean }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
  const tone = pct >= 100 ? "bg-down" : pct >= 70 ? "bg-warn" : "bg-ai";
  return (
    <div>
      <div className="flex justify-between text-xs"><span className="text-fg-muted">{label}</span><span className="font-tabular text-fg">{isMoney ? money(used) : used} / {isMoney ? money(max) : max}</span></div>
      <div className="mt-1 h-1.5 rounded-full bg-surface-3" role="progressbar" aria-label={label} aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function YourDay({ day }: { day: BriefDay }) {
  const t = useCopilotT();
  const pnlTone = day.realised_pnl > 0 ? "text-up" : day.realised_pnl < 0 ? "text-down" : "text-fg";
  return (
    <div className="space-y-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-fg-muted">{t("pulse.day.pnl")}</span>
        <span className={`font-tabular text-2xl font-bold ${pnlTone}`}>{day.realised_pnl > 0 ? "+" : ""}{money(day.realised_pnl)}</span>
      </div>
      <Meter label={t("pulse.day.lossUsed")} used={day.loss_used} max={day.loss_limit} isMoney />
      <Meter label={t("pulse.day.trades")} used={day.trades_today} max={day.max_trades} />
      <Meter label={t("pulse.day.open")} used={day.open_positions} max={day.max_open} />
      <div className="text-xs text-fg-muted">{t("pulse.day.streak")}: <b className={day.consecutive_losses >= day.max_consecutive_losses ? "text-down" : "text-fg"}>{day.consecutive_losses}</b> / {day.max_consecutive_losses}</div>
    </div>
  );
}

export default function MarketPulseTab() {
  const t = useCopilotT();
  const { brief, briefError, briefLoading, reloadBrief } = useCopilotData();
  const [mode, setMode] = useState<"PAPER" | "LIVE" | null>(null);

  if (!brief && briefLoading) return <TabSkeleton />;
  if (!brief) {
    return <EmptyState icon={<AlertTriangle size={22} />} title={t("pulse.errorTitle")} body={friendlyError(briefError, t)}
                       action={<Button onClick={reloadBrief} icon={<RefreshCw size={14} />}>{t("states.retry")}</Button>} />;
  }
  const data = brief.market_data ?? { state: "fresh" as const, updated_at: brief.market_updated_at, age_minutes: null, figures_shown: true };
  const snaps = data.figures_shown ? brief.market.symbols : [];
  const vix = data.figures_shown ? brief.day_type.vix : null;
  const kind = brief.day_type.kind as DialKind;
  const shownMode = mode ?? (brief.you.LIVE.active ? "LIVE" : "PAPER");
  const sentiment = brief.sentiment;

  return (
    <div className="space-y-4" data-testid="tab-panel-market-pulse">
      <Stagger className="grid gap-4 lg:grid-cols-3">
        <StaggerItem className="lg:col-span-1">
          <TiltCard className="h-full" data-testid="pulse-read">
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
              {t("pulse.todayRead")}{brief.day_type.symbol ? ` · ${brief.day_type.symbol}` : ""}<DataStamp data={data} />
            </div>
            <p className="mt-2 text-lg font-semibold leading-snug text-fg">{brief.plan.headline}</p>
            <p className="mt-2 text-xs text-fg-muted">
              {brief.session.text}
              {data.age_minutes != null ? ` · ${t("pulse.readAgo", { age: ageText(data.age_minutes) })}` : ` · ${t("pulse.noRead")}`}
              {brief.session.holidays_next_7_days.length ? ` · ${t("pulse.holidays", { list: brief.session.holidays_next_7_days.join(", ") })}` : ""}
            </p>
            {!data.figures_shown && data.state !== "none" && (
              <p className="mt-2 text-xs text-warn">{data.state === "stale" ? t("pulse.hiddenStale") : t("pulse.hiddenPlaceholder")}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
              {snaps.map((s) => (
                <span key={s.symbol} className="rounded-lg border border-border bg-surface-2/70 px-2 py-1 text-fg">
                  {s.symbol} <b className={(s.change_pct ?? 0) > 0 ? "text-up" : (s.change_pct ?? 0) < 0 ? "text-down" : "text-fg"}>{(s.change_pct ?? 0) > 0 ? "+" : ""}{(s.change_pct ?? 0).toFixed(2)}%</b>
                </span>
              ))}
              {vix != null && <span className="rounded-lg border border-border bg-surface-2/70 px-2 py-1 text-fg">India VIX <b>{vix.toFixed(1)}</b></span>}
              {brief.global_mood && (
                <span className="rounded-lg border border-border bg-surface-2/70 px-2 py-1 text-fg">{t("pulse.global")}{" "}
                  <b className={brief.global_mood.label === "POSITIVE" ? "text-up" : brief.global_mood.label === "NEGATIVE" ? "text-down" : "text-fg"}>{t(`pulse.mood.${brief.global_mood.label}`)}</b>
                </span>
              )}
            </div>
            <div className="mt-3"><Button size="sm" variant="ghost" icon={<RefreshCw size={13} className={briefLoading ? "animate-spin" : ""} />} onClick={reloadBrief} disabled={briefLoading}>{t("states.refresh")}</Button></div>
          </TiltCard>
        </StaggerItem>
        <StaggerItem>
          <TiltCard className="h-full" glow={sentiment && sentiment.score > 10 ? "up" : sentiment && sentiment.score < -10 ? "down" : "ai"} data-testid="pulse-gauge">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("pulse.sentiment.title")}</div>
            <SentimentGauge3D score={sentiment?.score ?? null} label={sentiment?.label} coverage={sentiment?.coverage} />
            {(brief.sentiment_view ?? []).length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">{(brief.sentiment_view ?? []).slice(0, 2).map((l) => <li key={l}>{l}</li>)}</ul>}
          </TiltCard>
        </StaggerItem>
        <StaggerItem>
          <TiltCard className="h-full" data-testid="pulse-dial">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{t("pulse.regime.title")}</div>
            <RegimeDial kind={kind} detail={brief.day_type.higher_regime ? t("pulse.regime.higher", { regime: brief.day_type.higher_regime.replace(/_/g, " ").toLowerCase() }) : null} />
          </TiltCard>
        </StaggerItem>
      </Stagger>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title={t("pulse.filters")} icon={<Minus size={15} />}>
          <ul className="space-y-1.5 text-sm text-fg">
            {brief.plan.lines.map((l) => <li key={l} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-ai" /><span>{l}</span></li>)}
            {brief.plan.lines.length === 0 && <li className="text-fg-muted">{t("pulse.filtersEmpty")}</li>}
          </ul>
          {(brief.plan.fit_families.length > 0 || brief.plan.avoid_families.length > 0) && (
            <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
              {brief.plan.fit_families.map((f) => <Badge key={f} tone="neutral">{t("pulse.filterOpen", { family: t(`pulse.family.${f}`, { defaultValue: f }) })}</Badge>)}
              {brief.plan.avoid_families.map((f) => <Badge key={f} tone="neutral">{t("pulse.filterClosed", { family: t(`pulse.family.${f}`, { defaultValue: f }) })}</Badge>)}
            </div>
          )}
        </Panel>

        <Panel title={t("pulse.checklist")} icon={<ListChecks size={15} />} testId="pulse-checklist">
          <ul className="space-y-1.5 text-sm">
            {brief.checklist.map((c) => (
              <li key={c.id} className="flex gap-2">
                {c.ok === true ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-up" aria-label={t("pulse.ok")} />
                  : c.ok === false ? <XCircle size={16} className="mt-0.5 shrink-0 text-down" aria-label={t("pulse.notOk")} />
                  : <CircleHelp size={16} className="mt-0.5 shrink-0 text-fg-muted" aria-label={t("pulse.unknown")} />}
                <span className={c.ok === false ? "text-fg" : "text-fg-muted"}>{c.text}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title={t("pulse.day.title")} icon={<Wallet size={15} />}
               action={(
                 <div className="flex overflow-hidden rounded-md border border-border text-xs" role="group" aria-label={t("pulse.day.mode")}>
                   {(["PAPER", "LIVE"] as const).map((m) => (
                     <button key={m} onClick={() => setMode(m)} aria-pressed={shownMode === m}
                             className={`px-2 py-0.5 ${shownMode === m ? "bg-ai/15 text-fg" : "text-fg-muted hover:text-fg"}`}>{m}</button>
                   ))}
                 </div>
               )}>
          <YourDay day={brief.you[shownMode]} />
        </Panel>
      </div>

      <ThesisPanel />

      <Panel title={t("pulse.events")} icon={<CalendarClock size={15} />} testId="pulse-events">
        {brief.events.length === 0 ? <p className="text-sm text-fg-muted">{t("pulse.eventsEmpty")}</p> : (
          <ul className="space-y-1.5 text-sm text-fg">
            {brief.events.map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <Badge tone={e.action === "BLOCK" ? "warn" : "info"}>{e.kind}</Badge>
                <span>{e.description}</span>
                {e.start_time && <span className="text-xs text-fg-muted">{e.start_time}{e.end_time ? `-${e.end_time}` : ""}</span>}
                <span className="text-xs text-fg-muted">· {e.action === "BLOCK" ? t("pulse.eventBlock") : t("pulse.eventReduce")}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title={t("pulse.deployments", { count: brief.deployments.length })} icon={<Rocket size={15} />}>
        {brief.deployments.length === 0 ? <p className="text-sm text-fg-muted">{t("pulse.deploymentsEmpty")}</p> : (
          <div className="grid gap-2 md:grid-cols-2">
            {brief.deployments.map((d) => (
              <div key={d.id} className="rounded-xl border border-border bg-surface-2/50 p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <b className="text-fg">#{d.id} {d.strategy_id}</b>
                  <span className="text-xs text-fg-muted">{d.symbol} · {d.timeframe} · {d.mode}{d.holding === "SWING" ? ` · ${t("pulse.swing")}` : ""}</span>
                  <Badge className="ml-auto" tone={d.state === "error" || d.state === "stale" ? "down" : d.state === "regime" ? "warn" : d.state === "ok" ? "up" : "neutral"}>{t(`pulse.state.${d.state}`)}</Badge>
                </div>
                <ul className="mt-1.5 space-y-0.5 text-xs text-fg-muted">{d.why.map((w) => <li key={w}>· {w}</li>)}</ul>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <MemoryPanel />
    </div>
  );
}
