import { CandlestickChart, ChevronRight, Layers, ListChecks, RefreshCw, ShieldCheck, Sparkles, TriangleAlert, Wallet, Zap } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import {
  chipLabel, costText, fetchConclusion, glyph, segments, shortTime, summarizeConclusion, toneOf, type Conclusion, type ConclusionSection,
  type ConclusionSummary, type Reading,
} from "./conclusion";

/**
 * TERM-CON C2 (plan section 6A, "Glance design"; owner 20 Nov "eka najaret ... ishara kafi hota hai"): the Terminal's
 * Conclusion panel for its one symbol + expiry. On top the glance strip - the lean cue (▲ / ▼ / ◆ with "Lean" and its
 * count, chip-sized, TC-Q5), the confluence meter (one segment per reading, glyph + colour), one chip per source and the
 * one structure line; what needs him; then each section folded (tap to open). Read only: no order button, no buy / sell
 * words (CLAUDE.md 3). Text sizes and colours from the tokens only.
 */
export function ConclusionPanelV2({ symbol, expiry, tf, also = [], refresh = 0, enabled = true, load = fetchConclusion, summarize = summarizeConclusion }: {
  symbol: string; expiry: string | null; tf: string; also?: readonly string[];
  /** Any change refetches (bar close, a new OI slot, his positions / orders changing - C3). */
  refresh?: string | number;
  enabled?: boolean;
  load?: typeof fetchConclusion;
  /** C4: the AI summary, on tap only (TC-Q3). */
  summarize?: typeof summarizeConclusion;
}) {
  const [data, setData] = useState<Conclusion | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [tick, setTick] = useState(0);
  const last = useRef<string>("");
  const alsoKey = also.join(",");
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    load(symbol, expiry, tf, alsoKey ? alsoKey.split(",") : [], String(refresh))
      .then((body) => {
        if (!live) return;
        if (body.fingerprint !== last.current) { last.current = body.fingerprint; setData(body); }   // same facts: no repaint
        setState("ok");
      })
      .catch(() => { if (live) setState((s) => (s === "ok" ? "ok" : "error")); });
    return () => { live = false; };
  }, [enabled, load, symbol, expiry, tf, alsoKey, refresh, tick]);
  useEffect(() => { last.current = ""; setData(null); setState("loading"); }, [symbol, expiry, tf]);
  // C4: a summary belongs to the read it was made from; a new read (another fingerprint) clears it.
  const [sum, setSum] = useState<{ busy: boolean; result: ConclusionSummary | null; failed: boolean; of: string }>({ busy: false, result: null, failed: false, of: "" });
  const fingerprint = data?.fingerprint ?? "";
  const shownSum = sum.of === fingerprint ? sum : { busy: false, result: null, failed: false, of: fingerprint };
  const ask = () => {
    setSum({ busy: true, result: null, failed: false, of: fingerprint });
    summarize(symbol, expiry, tf, alsoKey ? alsoKey.split(",") : [], String(refresh))
      .then((r) => setSum({ busy: false, result: r, failed: false, of: fingerprint }))
      .catch(() => setSum({ busy: false, result: null, failed: true, of: fingerprint }));
  };
  return <ConclusionViewV2 data={data} state={state} onRefresh={() => setTick((t) => t + 1)}
    summary={data ? <SummaryV2 busy={shownSum.busy} result={shownSum.result} failed={shownSum.failed} onAsk={ask} /> : null} />;
}

/** C4: "Summarise with AI" - his own key, on tap; the words are checked on the server (no advice, only the read's facts). */
export function SummaryV2({ busy, result, failed, onAsk }: { busy: boolean; result: ConclusionSummary | null; failed: boolean; onAsk: () => void }) {
  return (
    <section aria-label="AI summary" data-summary className="space-y-space-1">
      {result?.text ? (
        <>
          <p data-summary-text className="rounded-control border border-subtle bg-surface-2 p-space-2 text-fg">{result.text}</p>
          <p className="text-t12 text-fg-muted">AI summary of the readings above. Information only, not advice. {costText(result.usage)}</p>
        </>
      ) : (
        <>
          <button type="button" onClick={onAsk} disabled={busy} data-summary-ask
            className="inline-flex min-h-11 items-center gap-space-1 rounded-control border border-subtle px-space-3 text-t13 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60 md:min-h-9">
            <Sparkles size={14} aria-hidden className="text-ai" />{busy ? "Summarising..." : "Summarise with AI"}
          </button>
          {(result?.reason || failed) && (
            <p data-summary-reason className="text-t12 text-fg-muted">{result?.reason ?? "The summary could not be made right now. The readings above are unchanged."}</p>
          )}
        </>
      )}
    </section>
  );
}

const SOURCE_ICON: Record<string, ReactNode> = {
  chart: <CandlestickChart size={14} aria-hidden />, oi: <Layers size={14} aria-hidden />, signals: <Zap size={14} aria-hidden />,
  positions: <Wallet size={14} aria-hidden />, orders: <ListChecks size={14} aria-hidden />,
};

/** The panel's content (no state: the tests render it). */
export function ConclusionViewV2({ data, state, onRefresh, summary }: {
  data: Conclusion | null; state: "loading" | "ok" | "error"; onRefresh?: () => void; summary?: ReactNode;
}) {
  if (!data) {
    return (
      <div data-panel-body="conclusion" className="text-t13 text-fg-muted" data-conclusion-state={state}>
        {state === "error" ? "We could not read the conclusion right now. We will try again at the next update." : "Reading the chart, OI and your positions..."}
      </div>
    );
  }
  const g = data.glance;
  const lean = g.lean.side;
  const t = toneOf(lean);
  return (
    <div data-panel-body="conclusion" className="space-y-space-3 text-t13" data-conclusion-state={state} data-fingerprint={data.fingerprint}>
      <div className="flex items-center justify-between gap-space-2 text-t12 text-fg-muted">
        <span className="truncate font-semibold text-fg">{data.symbol}{data.expiry ? ` · ${data.expiry}` : ""}</span>
        <span className="flex shrink-0 items-center gap-space-1">
          <span data-conclusion-time>{shortTime(data.as_of)}</span>
          {onRefresh && (
            <button type="button" onClick={onRefresh} aria-label="Read again"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-control hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8 md:min-w-8">
              <RefreshCw size={14} aria-hidden />
            </button>
          )}
        </span>
      </div>

      <section aria-label="At a glance" data-glance className="space-y-space-2">
        <div className="flex flex-wrap items-center gap-space-2">
          <span data-lean={lean} className={cx("inline-flex items-center gap-space-1 rounded-pill border px-space-2 py-space-1 font-semibold", t.text, t.tint, t.border)}>
            <span aria-hidden>{glyph(lean)}</span>{g.lean.words}
          </span>
          <Meter meter={g.meter} />
        </div>
        <ul className="flex flex-wrap gap-space-1" aria-label="Readings by source" data-chips>
          {data.readings.map((r) => <SourceChip key={`${r.source}-${r.tf ?? ""}`} r={r} />)}
          <OrdersChip data={data} />
        </ul>
        {g.line && !g.conflicts.some((c) => c.text === g.line) && <p data-glance-line className="text-fg">{g.line}</p>}
        {data.needs_you.length > 0 && (
          <ul aria-label="Needs you" data-needs-you className="space-y-space-1">
            {data.needs_you.map((n) => (
              <li key={n.key} data-need={n.key} className="flex items-center gap-space-2 rounded-control border border-warn bg-warn-tint px-space-2 py-space-1 text-warn">
                <TriangleAlert size={14} aria-hidden className="shrink-0" /><span className="min-w-0 text-fg">{n.text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(g.agrees.length > 0 || g.conflicts.length > 0) && (
        <section aria-label="Agrees and conflicts" className="space-y-space-1" data-agree-conflict>
          {g.agrees.map((a) => (
            <p key={a.direction} data-agrees={a.direction} className="flex gap-space-2">
              <span className={cx("shrink-0 font-semibold", toneOf(a.direction).text)}>{glyph(a.direction)} Agrees</span><span className="min-w-0 text-fg">{a.text}</span>
            </p>
          ))}
          {g.conflicts.map((c) => (
            <p key={c.text} data-conflicts className="flex gap-space-2">
              <span className="shrink-0 font-semibold text-warn">◆ Conflicts</span><span className="min-w-0 text-fg">{c.text}</span>
            </p>
          ))}
        </section>
      )}

      {summary}

      <div className="divide-y divide-subtle border-t border-subtle" data-sections>
        {data.sections.map((s) => <SectionV2 key={s.key} s={s} />)}
      </div>
      <p className="text-t12 text-fg-muted" data-conclusion-note>{data.note}</p>
    </div>
  );
}

function Meter({ meter }: { meter: Conclusion["glance"]["meter"] }) {
  const parts = segments(meter);
  if (!parts.length) return null;
  const words = `${meter.up} up · ${meter.down} down · ${meter.range} range`;
  return (
    <span className="inline-flex items-center gap-space-2" data-meter aria-label={`Readings: ${words}`}>
      <span className="inline-flex overflow-hidden rounded-control border border-subtle" aria-hidden>
        {parts.map((d, i) => (
          <span key={i} data-segment={d} className={cx("inline-flex h-6 w-6 items-center justify-center text-t12", toneOf(d).text, toneOf(d).tint,
            i > 0 && "border-l border-subtle")}>{glyph(d)}</span>
        ))}
      </span>
      <span className="text-t12 text-fg-muted">{words}</span>
    </span>
  );
}

function SourceChip({ r }: { r: Reading }) {
  const t = toneOf(r.direction);
  return (
    <li data-chip-source={r.source} data-chip-direction={r.direction} title={`${r.label} · ${shortTime(r.as_of)}`}
      className={cx("inline-flex items-center gap-space-1 rounded-pill border px-space-2 py-space-1 text-t12", t.text, t.tint,
        r.stale ? "border-dashed border-subtle opacity-75" : t.border)}>
      {SOURCE_ICON[r.source]}<span aria-hidden>{glyph(r.direction)}</span><span className="text-fg">{chipLabel(r)}</span>
    </li>
  );
}

function OrdersChip({ data }: { data: Conclusion }) {
  const trouble = data.needs_you.some((n) => n.key === "orders" || n.key === "broker");
  const t = toneOf(trouble ? "warn" : "neutral");
  return (
    <li data-chip-source="orders" data-chip-direction={trouble ? "warn" : "ok"}
      className={cx("inline-flex items-center gap-space-1 rounded-pill border px-space-2 py-space-1 text-t12", t.text, t.tint, t.border)}>
      {trouble ? <TriangleAlert size={14} aria-hidden /> : <ShieldCheck size={14} aria-hidden />}
      <span aria-hidden>{trouble ? "!" : "✓"}</span><span className="text-fg">Orders</span>
    </li>
  );
}

function SectionV2({ s }: { s: ConclusionSection }) {
  return (
    <details data-section={s.key} data-section-status={s.status} className="group py-space-1">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-space-2 rounded-control font-medium text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8 [&::-webkit-details-marker]:hidden">
        <span className="inline-flex items-center gap-space-1">
          <ChevronRight size={14} aria-hidden className="transition-transform group-open:rotate-90" />{s.title}
          {s.status === "stale" && <span className="text-t12 text-warn">· stale</span>}
          {s.status === "na" && <span className="text-t12 text-fg-muted">· not available</span>}
        </span>
        <span className="text-t12 font-normal text-fg-muted">{shortTime(s.as_of)}</span>
      </summary>
      <ul className="mt-space-1 space-y-space-1 pl-space-4">
        {s.lines.map((l, i) => (
          <li key={i} data-line-kind={l.kind} className={cx("flex gap-space-2", toneOf(l.tone).text)}>
            <span aria-hidden className="shrink-0">{l.tone === "up" ? "▲" : l.tone === "down" ? "▼" : l.tone === "warn" ? "!" : "·"}</span>
            <span className="min-w-0 text-fg">{l.text}<span className="ml-space-1 text-t12 text-fg-muted">{l.source}{l.as_of && shortTime(l.as_of) ? ` · ${shortTime(l.as_of)}` : ""}</span></span>
          </li>
        ))}
        {s.note && <li className="text-t12 text-fg-muted">{s.note}</li>}
      </ul>
    </details>
  );
}
