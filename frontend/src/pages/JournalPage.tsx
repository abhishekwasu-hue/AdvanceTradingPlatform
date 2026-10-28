import { ChevronLeft, ChevronRight, NotebookPen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/errors";
import { Card, StatTile, signTone } from "../components/ui";
import { Badge, Button, EmptyState, Input, PageHeader, Signed, Skeleton, Tabs } from "../components/primitives";
import { useToast } from "../components/Toast";
import { journalApi } from "../journal/api";
import {
  COMPONENT_LABELS, EVENT_KINDS, SETTING_LABELS, TAG_HELP, chase, countsInScore, isoWeekOf, minutesText, money, parseSettingNumber, rText,
  settingsProblems, shiftWeek, tagLabel,
} from "../journal/format";
import type { DisciplineScore, DisciplineSettings, JournalEntry, JournalSettingsResponse, JournalTag, JournalWeek, TagStatus, WeeklyReview } from "../journal/types";
import type { PageProps } from "../routes";
import DisciplineCardV2 from "../growth/DisciplineCardV2";
import PageDishV2 from "../growth/PageDishV2";
import { Details } from "../ui/Details";
import { useFeatures } from "../ui/useFeatures";

const OFF_TEXT = "The discipline score is not turned on for your account yet.";

/** UI-28 U1.5: a tag's evidence as "name value" pairs - never raw JSON on screen. */
export function evidenceText(evidence: unknown): string {
  if (evidence === null || evidence === undefined) return "";
  if (typeof evidence !== "object") return String(evidence);
  return Object.entries(evidence as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "number" ? v.toLocaleString("en-IN") : String(v)}`)
    .join(" · ");
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 503) return OFF_TEXT;
  return e instanceof Error ? e.message : String(e);
}

function ScoreCard({ score }: { score: DisciplineScore }) {
  return (
    <Card title="Discipline score">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <div className="font-tabular text-4xl font-extrabold text-fg">{score.score ?? "-"}<span className="text-base text-fg-muted"> / 100</span></div>
          <div className="text-xs text-fg-muted">{score.score == null ? "No trades this week" : `${score.decisions} decisions · ${score.trades} trades`}</div>
        </div>
        <div className="min-w-[16rem] flex-1 space-y-2">
          {score.components.map((c) => (
            <div key={c.name}>
              <div className="flex justify-between text-xs">
                <span className="text-fg">{COMPONENT_LABELS[c.name] ?? c.name}</span>
                <span className="font-tabular text-fg-muted">{c.points.toFixed(1)} / {c.max_points.toFixed(1)}{c.flagged ? ` · ${c.flagged} flagged` : ""}</span>
              </div>
              <div className="mt-1 h-1.5 rounded bg-surface-2" aria-hidden>
                <div className="h-1.5 rounded bg-brand" style={{ width: `${c.max_points ? (100 * c.points) / c.max_points : 0}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
      <Details id="journal.how-scored" label="How scored">
        Each component is the share of this week's decisions with none of its tags; the score weights them by your settings.
        {score.count_unconfirmed ? " Suggested tags count until you dismiss them." : " Only confirmed tags count."}
      </Details>
    </Card>
  );
}

function TagChip({ tag, countUnconfirmed, onDecide }: { tag: JournalTag; countUnconfirmed: boolean; onDecide: (id: number, s: TagStatus) => void }) {
  const tone = tag.status === "DISMISSED" ? "neutral" : countsInScore(tag.status, countUnconfirmed) ? "warn" : "info";
  return (
    <span className="inline-flex items-center gap-1">
      <Badge tone={tone} className={tag.status === "DISMISSED" ? "line-through" : ""}>
        <span title={TAG_HELP[tag.tag] ?? ""}>{tagLabel(tag.tag)}</span>
        <span className="font-normal opacity-80">{tag.status === "SUGGESTED" ? "?" : tag.status === "CONFIRMED" ? "✓" : ""}</span>
      </Badge>
      {tag.status !== "CONFIRMED" && <button className="text-xs text-fg-muted underline" onClick={() => onDecide(tag.id, "CONFIRMED")}>confirm</button>}
      {tag.status !== "DISMISSED" && <button className="text-xs text-fg-muted underline" onClick={() => onDecide(tag.id, "DISMISSED")}>dismiss</button>}
    </span>
  );
}

function EntryRow({ e, countUnconfirmed, onDecide }: { e: JournalEntry; countUnconfirmed: boolean; onDecide: (id: number, s: TagStatus) => void }) {
  const [open, setOpen] = useState(false);
  const diff = chase(e.direction, e.plan.entry, e.execution.entry_price);
  return (
    <div className="border-t border-border/60 py-2">
      <button className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left text-xs" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="font-tabular text-fg-muted">{e.entry_time ? new Date(e.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "short", timeStyle: "short" }) : "-"}</span>
        <span className="font-semibold text-fg">{e.symbol}</span>
        <Badge tone={e.direction === "LONG" ? "up" : "down"}>{e.direction}</Badge>
        <span className="text-fg-muted">{e.setup}</span>
        <Badge>{e.source}</Badge>
        <span className="ml-auto flex items-center gap-3">
          <Signed value={e.pnl} format={(v) => money(v)} />
          <span className="font-tabular">{rText(e.r_multiple)}</span>
        </span>
      </button>
      {e.tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2">{e.tags.map((t) => <TagChip key={t.id} tag={t} countUnconfirmed={countUnconfirmed} onDecide={onDecide} />)}</div>
      )}
      {open && (
        <div className="mt-2 grid gap-3 rounded-md bg-surface-2 p-3 text-xs sm:grid-cols-2">
          <div>
            <div className="mb-1 font-semibold text-fg">Plan vs execution</div>
            <div>Planned entry {e.plan.entry ?? "-"} ({e.plan.entry_source ?? "-"}) · fill {e.execution.entry_price}{diff != null ? ` · chase ${diff}` : ""}</div>
            <div>Stop {e.plan.stop ?? "-"} (now {e.execution.stop_now}) · target {e.plan.target1 ?? "-"} · planned R:R {e.plan.rr ?? "-"}</div>
            <div>Exit {e.execution.exit_price ?? "open"}{e.execution.exit_reason ? ` (${e.execution.exit_reason})` : ""} · held {minutesText(e.holding_minutes)}</div>
            <div>MAE {e.excursions.mae ?? "-"} ({rText(e.excursions.mae_r)}) · MFE {e.excursions.mfe ?? "-"} ({rText(e.excursions.mfe_r)})</div>
            <div>Costs {money(e.costs.charges)} ({e.costs.source.toLowerCase()}) · risk {money(e.risk_amount)}</div>
          </div>
          <div>
            <div className="mb-1 font-semibold text-fg">Fills</div>
            {e.fills.map((f, i) => <div key={i} className="font-tabular">{f.leg} {f.side} {f.quantity} @ {f.price}{f.at ? ` · ${new Date(f.at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })}` : ""}</div>)}
            {e.stop_history.length > 0 && <div className="mt-2 font-semibold text-fg">Stop changes</div>}
            {e.stop_history.map((s, i) => <div key={i}>{s.from} → {s.to} ({s.source})</div>)}
            {e.greeks_entry && <div className="mt-2">Greeks at entry: {Object.entries(e.greeks_entry).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
            {e.chart_entry && <div className="mt-2 text-fg-muted">Chart at entry: {e.chart_entry.symbol} @ {new Date(e.chart_entry.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</div>}
            {e.tags.map((t) => <div key={t.id} className="mt-1 text-fg-muted">{tagLabel(t.tag)}{evidenceText(t.evidence) && `: ${evidenceText(t.evidence)}`}</div>)}
          </div>
        </div>
      )}
    </div>
  );
}

function WeekTab({ data, onDecide }: { data: JournalWeek; onDecide: (id: number, s: TagStatus) => void }) {
  return (
    <div className="space-y-4">
      <ScoreCard score={data.score} />
      <Card title={`Trades entered in ${data.week}`}>
        {data.entries.length === 0
          ? <EmptyState icon={<NotebookPen size={20} />} title="No trades this week" body="Every PAPER or LIVE fill is journaled here automatically." />
          : data.entries.map((e) => <EntryRow key={e.trade_id} e={e} countUnconfirmed={data.score.count_unconfirmed} onDecide={onDecide} />)}
      </Card>
    </div>
  );
}

/** `version` changes whenever a tag is confirmed or dismissed elsewhere on the page: the review is fetched again. */
function ReviewTab({ week, version }: { week: string | undefined; version: number }) {
  const toast = useToast();
  const [review, setReview] = useState<WeeklyReview | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { journalApi.review(week).then(setReview).catch((e) => toast.error(errorText(e))); }, [week, version, toast]);
  useEffect(() => { setReview(null); }, [week]);
  async function explain() {
    setBusy(true);
    try { setReview(await journalApi.review(week, true)); } catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }
  if (!review) return <Skeleton className="h-40 w-full" />;
  const t = review.totals;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Net P&L" value={money(t.net_pnl)} tone={signTone(t.net_pnl)} />
        <StatTile label="Win rate" value={t.win_rate == null ? "-" : `${t.win_rate}%`} />
        <StatTile label="Average R" value={rText(t.avg_r)} tone={signTone(t.avg_r)} />
        <StatTile label="Discipline" value={review.discipline.score ?? "-"} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Setups">
          {review.by_setup.length === 0 ? <p className="text-xs text-fg-muted">No closed trades.</p> : (
            <table className="w-full text-xs"><thead className="text-left text-xs uppercase text-fg-muted"><tr><th className="py-1">Setup</th><th>Closed</th><th>Win rate</th><th>Net</th></tr></thead>
              <tbody>{review.by_setup.map((g) => <tr key={g.setup} className="border-t border-border/60"><td className="py-1">{g.setup}</td><td>{g.closed}</td><td>{g.win_rate ?? "-"}{g.win_rate != null ? "%" : ""}</td><td><Signed value={g.net_pnl} format={(v) => money(v)} /></td></tr>)}</tbody></table>
          )}
        </Card>
        <Card title="Behaviours and their cost">
          {review.behaviours.length === 0 ? <p className="text-xs text-fg-muted">No counted behaviour tags this week.</p> : (
            <table className="w-full text-xs"><thead className="text-left text-xs uppercase text-fg-muted"><tr><th className="py-1">Tag</th><th>Trades</th><th>Net (closed)</th><th>Avg R</th></tr></thead>
              <tbody>{review.behaviours.map((b) => <tr key={b.tag} className="border-t border-border/60"><td className="py-1">{tagLabel(b.tag)}</td><td>{b.trades}</td><td><Signed value={b.net_pnl} format={(v) => money(v)} /></td><td>{rText(b.avg_r)}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>
      <Card title="Your previous weeks">
        <table className="w-full text-xs"><thead className="text-left text-xs uppercase text-fg-muted"><tr><th className="py-1">Week</th><th>Score</th><th>Trades</th><th>Net</th><th>Avg R</th></tr></thead>
          <tbody>
            <tr className="border-t border-border/60 font-semibold"><td className="py-1">{review.week}</td><td>{review.discipline.score ?? "-"}</td><td>{t.trades}</td><td><Signed value={t.net_pnl} format={(v) => money(v)} /></td><td>{rText(t.avg_r)}</td></tr>
            {review.history.map((h) => <tr key={h.week} className="border-t border-border/60"><td className="py-1">{h.week}</td><td>{h.score ?? "-"}</td><td>{h.trades}</td><td><Signed value={h.net_pnl} format={(v) => money(v)} /></td><td>{rText(h.avg_r)}</td></tr>)}
          </tbody></table>
      </Card>
      <Card title="What the numbers say">
        <div className="whitespace-pre-line text-sm text-fg">{review.explanation.text}</div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
          <Badge tone={review.explanation.source === "ai" ? "brand" : "neutral"}>{review.explanation.source === "ai" ? "Explained by the Copilot (numbers checked)" : "From the journal rules"}</Badge>
          {review.explanation.note && <span>{review.explanation.note}</span>}
          {review.totals.trades > 0 && <Button size="sm" onClick={explain} loading={busy}>Explain in plain language</Button>}
        </div>
        <p className="mt-3 text-xs text-fg-muted">{review.disclaimer}</p>
      </Card>
    </div>
  );
}

const NUMBER_KEYS = Object.keys(SETTING_LABELS) as (keyof DisciplineSettings)[];

/** The number fields as typed (so a cleared field stays blank instead of becoming 0): settings keys and `w:<component>`. */
function numberTexts(s: DisciplineSettings): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of NUMBER_KEYS) out[k as string] = String(s[k]);
  for (const [c, w] of Object.entries(s.weights)) out[`w:${c}`] = String(w);
  return out;
}

function SettingsTab() {
  const toast = useToast();
  const [data, setData] = useState<JournalSettingsResponse | null>(null);
  const [draft, setDraft] = useState<DisciplineSettings | null>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const reset = useCallback((s: DisciplineSettings) => { setDraft(s); setTexts(numberTexts(s)); }, []);
  useEffect(() => { journalApi.settings().then((d) => { setData(d); reset(d.settings); }).catch((e) => toast.error(errorText(e))); }, [toast, reset]);
  if (!data || !draft) return <Skeleton className="h-40 w-full" />;
  const settings = data;
  const setText = (key: string, value: string) => setTexts({ ...texts, [key]: value });
  async function save() {
    if (!draft) return;
    const labels: Record<string, string> = {};
    for (const k of NUMBER_KEYS) labels[k as string] = SETTING_LABELS[k as string].label;
    for (const c of Object.keys(settings.components)) labels[`w:${c}`] = `${COMPONENT_LABELS[c] ?? c} weight`;
    const problems = settingsProblems(texts, labels, settings.ranges);
    if (problems.length) { toast.error(`Not saved. ${problems.join("; ")}.`); return; }
    const values: DisciplineSettings = { ...draft, weights: { ...draft.weights } };
    for (const k of NUMBER_KEYS) (values as unknown as Record<string, number>)[k as string] = parseSettingNumber(texts[k as string]) as number;
    for (const c of Object.keys(settings.components)) if (texts[`w:${c}`] !== undefined) values.weights[c] = parseSettingNumber(texts[`w:${c}`]) as number;
    try {
      const d = await journalApi.saveSettings(values);
      setData(d); reset(d.settings); toast.success("Settings saved. The week's tags and score use them from now on.");
    } catch (e) { toast.error(errorText(e)); }
  }
  return (
    <div className="space-y-4">
      <Card title="Behaviour thresholds">
        <div className="grid gap-3 sm:grid-cols-2">
          {NUMBER_KEYS.map((k) => {
            const range = data.ranges[k as string];
            return (
              <Input key={k as string} type="number" step="any" label={SETTING_LABELS[k as string].label}
                hint={`${SETTING_LABELS[k as string].unit ?? ""} · default ${String(data.defaults[k])}${range ? ` · ${range[0]}-${range[1]}` : ""}`}
                value={texts[k as string] ?? ""} onChange={(ev) => setText(k as string, ev.target.value)} />
            );
          })}
        </div>
        <fieldset className="mt-4">
          <legend className="text-xs font-medium text-fg-muted">Event kinds that count for "held through event"</legend>
          <div className="mt-1 flex flex-wrap gap-3 text-xs">
            {EVENT_KINDS.map((kind) => (
              <label key={kind} className="flex items-center gap-1">
                <input type="checkbox" checked={draft.event_kinds.includes(kind)}
                  onChange={(ev) => setDraft({ ...draft, event_kinds: ev.target.checked ? [...draft.event_kinds, kind] : draft.event_kinds.filter((x) => x !== kind) })} />
                {kind.replace("_", " ")}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="mt-4 flex items-center gap-2 text-xs">
          <input type="checkbox" checked={draft.count_unconfirmed} onChange={(ev) => setDraft({ ...draft, count_unconfirmed: ev.target.checked })} />
          Suggested tags count in the score until dismissed
        </label>
      </Card>
      <Card title="Score weights">
        <div className="grid gap-3 sm:grid-cols-3">
          {Object.keys(data.components).map((c) => (
            <Input key={c} type="number" step="any" label={COMPONENT_LABELS[c] ?? c} hint={`default ${data.defaults.weights[c]}`}
              value={texts[`w:${c}`] ?? ""} onChange={(ev) => setText(`w:${c}`, ev.target.value)} />
          ))}
        </div>
        <p className="mt-2 text-xs text-fg-muted">Only the proportions matter. The defaults and the reasoning behind each one are in the discipline engine's design note.</p>
      </Card>
      <div className="flex gap-2">
        <Button variant="primary" onClick={save}>Save settings</Button>
        <Button onClick={() => reset(data.defaults)}>Back to defaults</Button>
      </div>
    </div>
  );
}

export default function JournalPage(_props: PageProps) {
  const toast = useToast();
  const features = useFeatures();
  // MO4 slice 3: "Share my week" - the discipline card (ui_v2 + growth_v2).
  const shareCard = features.on("ui_v2") && features.on("growth_v2");
  const [week, setWeek] = useState<string | undefined>(undefined);
  const [data, setData] = useState<JournalWeek | null>(null);
  const [off, setOff] = useState(false);
  const [tab, setTab] = useState("week");
  const [version, setVersion] = useState(0);
  const load = useCallback(() => {
    journalApi.week(week).then((d) => { setData(d); setOff(false); }).catch((e) => {
      if (e instanceof ApiError && e.status === 503) setOff(true); else toast.error(errorText(e));
    });
  }, [week, toast]);
  useEffect(() => { load(); }, [load]);
  async function decide(id: number, status: TagStatus) {
    try { await journalApi.decideTag(id, status); load(); setVersion((v) => v + 1); } catch (e) { toast.error(errorText(e)); }
  }
  const label = data?.week ?? week ?? isoWeekOf(new Date());
  const nav = (
    <div className="flex items-center gap-1">
      <Button size="sm" variant="ghost" aria-label="Previous week" icon={<ChevronLeft size={14} />} onClick={() => setWeek(shiftWeek(label, -1))} />
      <span className="font-tabular text-sm text-fg">{label}</span>
      <Button size="sm" variant="ghost" aria-label="Next week" icon={<ChevronRight size={14} />} onClick={() => setWeek(shiftWeek(label, 1))} />
    </div>
  );
  return (
    <div>
      <PageHeader title="Journal & discipline"
        description="Your trades, behaviour tags and weekly discipline score."
        actions={off ? undefined : nav} />
      {/* MO4 slice 4: the page's ready-made dish (ui_v2 + growth_v2; the server sends nothing while the journal is off). */}
      {shareCard && <PageDishV2 page="journal" className="mb-space-4" />}
      {shareCard && <DisciplineCardV2 />}
      {off ? <EmptyState icon={<NotebookPen size={20} />} title="Not enabled" body={OFF_TEXT} /> : !data ? <Skeleton className="h-40 w-full" /> : (
        <Tabs value={tab} onChange={setTab} items={[
          { value: "week", label: "Week", content: <WeekTab data={data} onDecide={decide} /> },
          { value: "review", label: "Weekly review", content: <ReviewTab week={data.week} version={version} /> },
          { value: "settings", label: "Settings", content: <SettingsTab /> },
        ]} />
      )}
    </div>
  );
}
