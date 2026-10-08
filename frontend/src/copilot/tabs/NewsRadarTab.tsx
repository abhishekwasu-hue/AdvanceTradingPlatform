import { ExternalLink, Radar, RefreshCw, Search, ThumbsDown, ThumbsUp, Waves } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { Badge, Button, EmptyState, fieldClass, Select, type Tone } from "../../components/primitives";
import { pathFor } from "../../routes";
import type { NewsFeedItem, NewsTrust, NewsVerdict } from "../../types";
import { friendlyError } from "../aiTask";
import { useCopilotData } from "../data";
import { useCopilotT } from "../i18n";
import { Panel, Stagger, StaggerItem } from "./shared";

/**
 * News Radar: the live feed from the publishers' own public feeds (RBI, SEBI, exchanges...) - headline, source link,
 * a severity chip and the classification (keyword or AI). Items are unverified by the platform; the trader's verdict
 * (Useful / Noise / Wrong direction) teaches the thesis how much to trust the feed. Filters: window, severity,
 * category, source, words.
 */
export function severityTone(severity: number): Tone {
  return severity >= 5 ? "down" : severity >= 4 ? "warn" : severity >= 3 ? "info" : "neutral";
}
const VERDICTS: NewsVerdict[] = ["useful", "noise", "wrong_direction"];
const VERDICT_ICON = { useful: ThumbsUp, noise: ThumbsDown, wrong_direction: Waves } as const;

export default function NewsRadarTab() {
  const t = useCopilotT();
  const { markNewsSeen } = useCopilotData();
  const [items, setItems] = useState<NewsFeedItem[] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hours, setHours] = useState("24");
  const [minSeverity, setMinSeverity] = useState("1");
  const [category, setCategory] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [query, setQuery] = useState("");
  const [verdicts, setVerdicts] = useState<Record<string, NewsVerdict>>({});
  const [trust, setTrust] = useState<NewsTrust | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true); setError(null);
    try {
      const st = await api.newsFeedStatus();
      if (!st.enabled) { setOff(true); setItems([]); return; }
      setOff(false);
      const list = await api.newsFeedItems(Number(hours), Number(minSeverity));
      setItems(list);
      markNewsSeen(list);
      api.newsFeedbackMine(list.map((i) => i.id)).then((r) => setVerdicts(r.verdicts)).catch(() => undefined);
      api.newsFeedbackSummary().then((r) => setTrust(r.trust)).catch(() => setTrust(null));
    } catch (e) { setError(friendlyError(e, t)); setItems([]); } finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, [hours, minSeverity]); // eslint-disable-line react-hooks/exhaustive-deps

  async function vote(id: number, verdict: NewsVerdict) {
    try { const r = await api.newsFeedback(id, verdict); setVerdicts((v) => ({ ...v, [String(id)]: r.verdict })); setTrust(r.trust); }
    catch (e) { setError(friendlyError(e, t)); }
  }

  const sources = useMemo(() => [...new Set((items ?? []).map((i) => i.source))].sort(), [items]);
  const categories = useMemo(() => [...new Set((items ?? []).map((i) => i.category))].sort(), [items]);
  const shown = [...(items ?? [])].sort((a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime()).filter((i) =>
    (!category || i.category === category) && (!sourceFilter || i.source === sourceFilter)
    && (!query.trim() || `${i.headline} ${i.symbols.join(" ")}`.toLowerCase().includes(query.trim().toLowerCase())));

  return (
    <div className="space-y-4" data-testid="tab-panel-news-radar">
      <Panel title={t("news.title")} icon={<Radar size={15} />}
             action={<Button size="sm" variant="ghost" icon={<RefreshCw size={13} className={busy ? "animate-spin" : ""} />} disabled={busy} onClick={() => void load()}>{t("states.refresh")}</Button>}>
        <p className="mb-3 text-xs text-fg-muted">{t("news.intro")}{trust && ` ${t("news.trust", { trust: trust.trust.toFixed(2), n: trust.ratings })}`}</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          <Select label={t("news.window")} value={hours} onChange={setHours} options={[{ value: "6", label: t("news.hours", { n: 6 }) }, { value: "24", label: t("news.hours", { n: 24 }) }, { value: "72", label: t("news.days", { n: 3 }) }]} />
          <Select label={t("news.severity")} value={minSeverity} onChange={setMinSeverity}
                  options={[{ value: "1", label: t("news.sevAll") }, { value: "3", label: t("news.sevMin", { n: 3 }) }, { value: "4", label: t("news.sevMin", { n: 4 }) }, { value: "5", label: t("news.sevOnly5") }]} />
          <Select label={t("news.category")} value={category || "all"} onChange={(v) => setCategory(v === "all" ? "" : v)}
                  options={[{ value: "all", label: t("news.all") }, ...categories.map((c) => ({ value: c, label: t(`news.cat.${c}`, { defaultValue: c }) }))]} />
          <Select label={t("news.source")} value={sourceFilter || "all"} onChange={(v) => setSourceFilter(v === "all" ? "" : v)}
                  options={[{ value: "all", label: t("news.all") }, ...sources.map((s) => ({ value: s, label: s }))]} />
          <div>
            <label htmlFor="news-q" className="mb-1 block text-xs font-medium text-fg-muted">{t("news.search")}</label>
            <div className="relative"><Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-fg-muted" aria-hidden />
              <input id="news-q" className={`${fieldClass} pl-7`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("news.searchPlaceholder")} /></div>
          </div>
        </div>
      </Panel>

      {error && <p className="text-sm text-down" role="alert">{error}</p>}
      {items == null ? <div className="copilot-skeleton copilot-shimmer h-40" aria-busy="true" /> : off ? (
        <EmptyState icon={<Radar size={22} />} title={t("news.offTitle")} body={t("news.offBody")}
                    action={<Link to={pathFor("news-events")} className="text-sm font-semibold text-brand hover:underline">{t("news.offCta")}</Link>} />
      ) : shown.length === 0 ? (
        <EmptyState icon={<Radar size={22} />} title={t("news.emptyTitle")} body={t("news.emptyBody")} />
      ) : (
        <Stagger className="space-y-2">
          {shown.map((i) => {
            const c: Partial<NewsFeedItem["classification"]> = i.ai ?? i.classification ?? {};
            return (
              <StaggerItem key={i.id}>
                <article className="copilot-glass rounded-xl p-3" data-testid="news-item">
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    {typeof c.severity === "number" && <Badge tone={severityTone(c.severity)}>{t("news.sev", { n: c.severity })}</Badge>}
                    <Badge tone="neutral">{t(`news.cat.${i.category}`, { defaultValue: i.category })}</Badge>
                    <Badge tone="warn">{t("news.unverified")}</Badge>
                    {c.method === "ai" && <Badge tone="brand">{t("news.aiClassified")}</Badge>}
                    <span className="ml-auto text-fg-muted">{new Date(i.published_at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                  </div>
                  <h3 className="mt-1.5 text-sm font-semibold text-fg">{i.headline}</h3>
                  {c.one_line_en && <p className="mt-0.5 text-xs text-fg-muted">{c.one_line_en}</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                    {i.source_url ? (
                      <a href={i.source_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-brand hover:underline">{i.source}<ExternalLink size={11} /></a>
                    ) : <span className="text-fg-muted">{i.source}</span>}
                    {i.symbols.slice(0, 6).map((s) => <span key={s} className="rounded border border-border px-1 text-[10px] text-fg-muted">{s}</span>)}
                    <div className="ml-auto flex flex-wrap gap-1" role="group" aria-label={t("news.feedback")}>
                      {VERDICTS.map((v) => {
                        const Icon = VERDICT_ICON[v];
                        const on = verdicts[String(i.id)] === v;
                        return (
                          <button key={v} onClick={() => void vote(i.id, v)} aria-pressed={on}
                                  className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] ${on ? "border-ai bg-ai/15 text-fg" : "border-border text-fg-muted hover:text-fg"}`}>
                            <Icon size={11} aria-hidden />{t(`news.verdict.${v}`)}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </article>
              </StaggerItem>
            );
          })}
        </Stagger>
      )}
    </div>
  );
}
