import { AlertTriangle, CheckCircle2, CircleHelp, Minus, RefreshCw, TrendingDown, TrendingUp, Waves, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BriefDay, DailyBrief, DayKind } from "../types";
import { Card } from "./ui";

/**
 * Phase AV: today's briefing - what an experienced trader tells you before the open. The day type
 * and its game plan, your risk budget, why each deployment is (not) trading, and the checklist.
 */
const KIND: Record<DayKind, { mr: string; en: string; cls: string; icon: typeof TrendingUp }> = {
  TREND_UP: { mr: "वरच्या trend चा दिवस", en: "Trending up", cls: "border-emerald-500/40 bg-emerald-500/[0.08] text-emerald-200", icon: TrendingUp },
  TREND_DOWN: { mr: "खालच्या trend चा दिवस", en: "Trending down", cls: "border-rose-500/40 bg-rose-500/[0.08] text-rose-200", icon: TrendingDown },
  RANGE: { mr: "Sideways दिवस", en: "Sideways / range", cls: "border-amber-500/40 bg-amber-500/[0.08] text-amber-100", icon: Waves },
  VOLATILE: { mr: "अस्थिर दिवस", en: "Volatile", cls: "border-fuchsia-500/40 bg-fuchsia-500/[0.08] text-fuchsia-100", icon: Zap },
  UNKNOWN: { mr: "Market अजून वाचलेला नाही", en: "No market read yet", cls: "border-border bg-panel2/60 text-slate-200", icon: CircleHelp },
};
const FAMILY_MR: Record<string, string> = { trend: "Trend", momentum: "Momentum", breakout: "Breakout", reversion: "Reversion" };
const STATE: Record<string, { mr: string; cls: string }> = {
  ok: { mr: "लक्ष ठेवून", cls: "border-emerald-500/40 text-emerald-300" },
  regime: { mr: "आज जुळत नाही", cls: "border-amber-500/40 text-amber-300" },
  closed: { mr: "बाजार बंद", cls: "border-border text-muted" },
  paused: { mr: "थांबवलेली", cls: "border-border text-muted" },
  stale: { mr: "worker तपासा", cls: "border-rose-500/40 text-rose-300" },
  error: { mr: "त्रुटी", cls: "border-rose-500/40 text-rose-300" },
};
const money = (v: number) => `${v < 0 ? "-" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

function Meter({ label, used, max, money: isMoney = false, invert = false }: { label: string; used: number; max: number; money?: boolean; invert?: boolean }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
  const tone = pct >= 100 ? "bg-rose-500" : pct >= 70 ? "bg-amber-400" : invert ? "bg-sky-400" : "bg-emerald-500";
  return (
    <div>
      <div className="flex justify-between text-xs"><span className="text-muted">{label}</span><span className="font-tabular text-slate-200">{isMoney ? money(used) : used} / {isMoney ? money(max) : max}</span></div>
      <div className="mt-1 h-1.5 rounded-full bg-panel2"><div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function YourDay({ day, mode }: { day: BriefDay; mode: string }) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">आजचा P&L ({mode})</span>
        <span className={`font-tabular text-2xl font-extrabold ${day.realised_pnl > 0 ? "text-emerald-300" : day.realised_pnl < 0 ? "text-rose-300" : "text-slate-100"}`}>{money(day.realised_pnl)}</span>
      </div>
      <Meter label="तोटा-बजेट वापरले" used={day.loss_used} max={day.loss_limit} money />
      <Meter label="आजचे trades" used={day.trades_today} max={day.max_trades} invert />
      <Meter label="उघड्या positions" used={day.open_positions} max={day.max_open} invert />
      <div className="text-xs text-muted">सलग तोटे: <b className={day.consecutive_losses >= day.max_consecutive_losses ? "text-rose-300" : "text-slate-200"}>{day.consecutive_losses}</b> / {day.max_consecutive_losses}</div>
    </div>
  );
}

export default function DailyBriefing({ lang }: { lang: "en" | "mr" }) {
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"PAPER" | "LIVE" | null>(null);

  const load = () => {
    setBusy(true);
    api.aiBrief(lang).then((b) => { setBrief(b); setError(null); }).catch((e) => setError(String(e).replace(/^Error:\s*/, ""))).finally(() => setBusy(false));
  };
  useEffect(load, [lang]);

  if (error) return <Card><div className="text-sm text-danger">{error}</div></Card>;
  if (!brief) return <Card><div className="text-sm text-muted">आजचा briefing तयार होत आहे…</div></Card>;
  const kind = KIND[brief.day_type.kind];
  const Icon = kind.icon;
  const shownMode = mode ?? (brief.you.LIVE.active ? "LIVE" : "PAPER");
  const snaps = brief.market.symbols;
  const vix = brief.day_type.vix;

  return (
    <div className="space-y-4">
      <div className={`rounded-2xl border p-4 ${kind.cls}`}>
        <div className="flex flex-wrap items-start gap-3">
          <div className="rounded-xl bg-black/20 p-2"><Icon size={26} /></div>
          <div className="min-w-[240px] flex-1">
            <div className="text-[11px] font-bold uppercase tracking-wider opacity-80">आजचा दिवस · {lang === "mr" ? kind.mr : kind.en}{brief.day_type.symbol ? ` · ${brief.day_type.symbol}` : ""}</div>
            <div className="mt-0.5 text-lg font-extrabold leading-snug text-slate-50">{brief.plan.headline}</div>
            <div className="mt-1 text-xs opacity-80">{brief.session.text}{brief.session.holidays_next_7_days.length ? ` · सुट्टी: ${brief.session.holidays_next_7_days.join(", ")}` : ""}</div>
          </div>
          <button onClick={load} disabled={busy} className="rounded-lg border border-white/15 px-2.5 py-1 text-xs text-slate-100 hover:bg-white/5 disabled:opacity-50">
            <RefreshCw size={12} className={`mr-1 inline ${busy ? "animate-spin" : ""}`} />ताजे करा
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {snaps.map((s) => (
            <span key={s.symbol} className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-slate-100">
              {s.symbol} <b className={(s.change_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{(s.change_pct ?? 0) >= 0 ? "+" : ""}{(s.change_pct ?? 0).toFixed(2)}%</b>
              <span className="text-slate-300"> · {s.bias === "BULLISH" ? "तेजी" : s.bias === "BEARISH" ? "मंदी" : "तटस्थ"}</span>
            </span>
          ))}
          {vix != null && <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-slate-100">India VIX <b>{vix.toFixed(1)}</b></span>}
          {brief.global_mood && (
            <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-slate-100">जागतिक संकेत{" "}
              <b className={brief.global_mood.label === "POSITIVE" ? "text-emerald-300" : brief.global_mood.label === "NEGATIVE" ? "text-rose-300" : "text-amber-200"}>
                {brief.global_mood.label === "POSITIVE" ? "सकारात्मक" : brief.global_mood.label === "NEGATIVE" ? "नकारात्मक" : "संमिश्र"}
              </b>
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="आजचा game plan">
          <ul className="space-y-1.5 text-sm text-slate-200">
            {brief.plan.lines.map((l) => <li key={l} className="flex gap-2"><Minus size={14} className="mt-0.5 shrink-0 text-purple-300" /><span>{l}</span></li>)}
            {brief.plan.lines.length === 0 && <li className="text-muted">Market memory भरली की इथे आजचा plan दिसेल.</li>}
          </ul>
          {(brief.plan.fit_families.length > 0 || brief.plan.avoid_families.length > 0) && (
            <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
              {brief.plan.fit_families.map((f) => <span key={f} className="rounded-full border border-emerald-500/40 px-2 py-0.5 text-emerald-200">✓ {FAMILY_MR[f] ?? f}</span>)}
              {brief.plan.avoid_families.map((f) => <span key={f} className="rounded-full border border-rose-500/30 px-2 py-0.5 text-rose-200">✗ {FAMILY_MR[f] ?? f}</span>)}
            </div>
          )}
        </Card>

        <Card title="तुमचा आजचा दिवस">
          <div className="mb-2 flex overflow-hidden rounded border border-border text-xs">
            {(["PAPER", "LIVE"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={`flex-1 px-2 py-0.5 ${shownMode === m ? "bg-panel2 text-slate-100" : "text-muted"}`}>{m}</button>
            ))}
          </div>
          <YourDay day={brief.you[shownMode]} mode={shownMode} />
        </Card>

        <Card title="Trade आधीची checklist">
          <ul className="space-y-1.5 text-sm">
            {brief.checklist.map((c) => (
              <li key={c.id} className="flex gap-2">
                {c.ok === true ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-400" /> : c.ok === false ? <XCircle size={16} className="mt-0.5 shrink-0 text-rose-400" /> : <CircleHelp size={16} className="mt-0.5 shrink-0 text-muted" />}
                <span className={c.ok === false ? "text-slate-100" : "text-slate-300"}>{c.text}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={`तुमचे deployments - trade होतो आहे का? (${brief.deployments.length})`}>
        {brief.deployments.length === 0 ? (
          <div className="text-sm text-muted">एकही deployment चालू नाही. "Strategy" tab मध्ये मुलाखतीतून strategy बनवा आणि आधी PAPER मध्ये deploy करा.</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {brief.deployments.map((d) => {
              const st = STATE[d.state] ?? STATE.ok;
              return (
                <div key={d.id} className="rounded-lg border border-border bg-panel2/40 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <b className="text-slate-100">#{d.id} {d.strategy_id}</b>
                    <span className="text-xs text-muted">{d.symbol} · {d.timeframe} · {d.mode}{d.holding === "SWING" ? " · swing" : ""}</span>
                    <span className={`ml-auto rounded border px-1.5 py-0.5 text-[11px] ${st.cls}`}>{st.mr}</span>
                  </div>
                  <ul className="mt-1.5 space-y-0.5 text-xs text-slate-300">
                    {d.why.map((w) => <li key={w} className="flex gap-1.5">{d.state === "error" || d.state === "stale" ? <AlertTriangle size={12} className="mt-0.5 shrink-0 text-rose-300" /> : <Minus size={12} className="mt-0.5 shrink-0 text-muted" />}<span>{w}</span></li>)}
                  </ul>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {brief.events.length > 0 && (
        <Card title="आजचे market events">
          <ul className="space-y-1 text-sm text-slate-200">
            {brief.events.map((e, i) => <li key={i}><b>{e.kind}</b> {e.description} {e.start_time ? `(${e.start_time}${e.end_time ? `-${e.end_time}` : ""})` : ""} · {e.action === "BLOCK" ? "नवीन entries बंद" : "size कमी"}</li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}
