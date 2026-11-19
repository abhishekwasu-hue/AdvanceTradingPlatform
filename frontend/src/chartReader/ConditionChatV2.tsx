import { useCallback, useEffect, useId, useRef, useState } from "react";
import { friendlyError } from "../api/errors";
import { guardText } from "../chartAnalyze/signalLanguage";
import { Button } from "../components/primitives";
import { Chip } from "../ui/Chip";
import type { LayerBand } from "../charting/engine";
import { chartReaderApi } from "./api";
import { STATUS_MARK, STATUS_WORD, range, usd } from "./ReaderCardsV2";
import type { ChatAnswer, ChatTurn } from "./types";

/**
 * CA-CHAT (owner 18 / 19 Nov, OPEN_QUESTIONS CA-CHAT-Q1): on the SAME chart of an analysis, the trader writes his own
 * conditions and the AI answers each one as Met / Not met / Unclear with its evidence, on his own key (the estimate shows
 * before he sends, the real cost with each answer). A price area an answer points at is drawn on the chart (`onMarks`).
 * One thread per analysis, kept with it. Every sentence is guarded on render. Reference only - never an order.
 */

export const CHAT_PLACEHOLDER = "e.g. Price is in an uptrend; it came back to the last buyer area; a strong candle closed above it";
export const NO_ITEM = "Analyze a chart first, then check your conditions on it.";

/** The price areas the answers point at, as chart bands (met: up colour, not met: down, unclear: neutral). Pure. */
export function chatBands(turns: readonly ChatTurn[]): LayerBand[] {
  const out: LayerBand[] = [];
  turns.forEach((t, ti) => t.answers.forEach((a, ai) => {
    if (a.price_low == null || a.price_high == null) return;
    out.push({ low: a.price_low, high: a.price_high, role: a.status === "met" ? "demand" : a.status === "not_met" ? "supply" : "neutral",
      opacity: 0.12, style: "dashed", label: `Q${ti + 1}.${ai + 1}`, id: `chat-${ti}-${ai}` });
  }));
  return out.slice(-12);
}

function Answer({ a, tag }: { a: ChatAnswer; tag: string }) {
  const m = STATUS_MARK[a.status] ?? STATUS_MARK.unclear;
  return (
    <li className="space-y-space-1 border-t border-subtle pt-space-2 first:border-t-0 first:pt-0" data-chat-answer={a.status}>
      <div className="flex flex-wrap items-center gap-space-2">
        <Chip tone={m.tone} variant="meaning" icon={m.mark} name={`chat-${a.status}`}>{STATUS_WORD[a.status] ?? "Unclear"}</Chip>
        <span className="min-w-0 break-words text-t14 font-medium text-fg">{guardText(a.condition)}</span>
      </div>
      <p className="break-words text-t14 text-fg-muted">{guardText(a.evidence)}</p>
      {a.price_low != null && a.price_high != null ? (
        <p className="font-tabular text-t12 text-fg-muted" data-chat-range>{tag} · {range(a.price_low, a.price_high)}</p>
      ) : null}
    </li>
  );
}

export default function ConditionChatV2({ itemId, onMarks }: { itemId?: number | null; onMarks?: (bands: LayerBand[]) => void }) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [maxTurns, setMaxTurns] = useState(30);
  const [estimate, setEstimate] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  const fieldId = useId();
  const marks = useRef(onMarks);
  marks.current = onMarks;

  useEffect(() => () => { live.current = false; }, []);
  useEffect(() => {
    setTurns([]); setProblem(null);
    if (!itemId) return;
    let alive = true;
    chartReaderApi.chat(itemId).then(
      (t) => { if (alive) { setTurns(t.turns); setMaxTurns(t.max_turns); setEstimate(t.estimate_usd); } },
      (e) => { if (alive) setProblem(friendlyError(e)); },
    );
    return () => { alive = false; };
  }, [itemId]);
  useEffect(() => { marks.current?.(chatBands(turns)); }, [turns]);
  useEffect(() => () => { marks.current?.([]); }, []);

  const send = useCallback(async () => {
    const words = text.trim();
    if (!itemId || !words || busy) return;
    setBusy(true); setProblem(null);
    try {
      const got = await chartReaderApi.ask(itemId, words);
      if (!live.current) return;
      setTurns((t) => [...t, got.turn]);
      setEstimate(got.estimate_usd);
      setText("");
    } catch (e) {
      if (live.current) setProblem(friendlyError(e));
    } finally {
      if (live.current) setBusy(false);
    }
  }, [itemId, text, busy]);

  const clear = useCallback(async () => {
    if (!itemId) return;
    try { await chartReaderApi.clearChat(itemId); if (live.current) setTurns([]); } catch (e) { if (live.current) setProblem(friendlyError(e)); }
  }, [itemId]);

  if (!itemId) return <p className="text-t14 text-fg-muted" data-testid="chat-no-item">{NO_ITEM}</p>;
  const full = turns.length >= maxTurns;
  return (
    <div className="space-y-space-3" data-testid="condition-chat">
      {turns.length > 0 && (
        <ol className="space-y-space-3" data-testid="chat-turns">
          {turns.map((t, ti) => (
            <li key={`${t.at}-${ti}`} className="rounded-control border border-subtle p-space-3" data-chat-turn>
              <p className="break-words text-t14 font-semibold text-fg">Q{ti + 1}. {guardText(t.question)}</p>
              {t.unreadable || t.answers.length === 0 ? (
                <p className="mt-space-1 text-t14 text-fg-muted">The AI did not give a readable answer. Try writing the conditions one per line.</p>
              ) : (
                <ul className="mt-space-2 space-y-space-2">{t.answers.map((a, ai) => <Answer key={ai} a={a} tag={`Q${ti + 1}.${ai + 1}`} />)}</ul>
              )}
              <p className="mt-space-2 font-tabular text-t12 text-fg-muted" data-chat-cost>Cost {usd(t.cost_usd)}</p>
            </li>
          ))}
        </ol>
      )}
      <div>
        <label htmlFor={fieldId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Your conditions</label>
        <textarea id={fieldId} value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000} disabled={busy || full}
          placeholder={CHAT_PLACEHOLDER} data-testid="chat-input"
          className="w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand" />
      </div>
      <div className="flex flex-wrap items-center gap-space-2">
        <Button variant="primary" className="h-11 md:h-9" loading={busy} disabled={!text.trim() || full}
          reason={full ? `This chart already has ${maxTurns} questions.` : text.trim() ? undefined : "Write your conditions first."}
          onClick={() => void send()} data-testid="chat-send">Check on this chart</Button>
        <span className="font-tabular text-t12 text-fg-muted" data-chat-estimate>{estimate == null ? "" : `est. ${usd(estimate)} per check`}</span>
        {turns.length > 0 && (
          <button type="button" onClick={() => void clear()} data-testid="chat-clear"
            className="ml-auto inline-flex min-h-11 items-center text-t13 font-medium text-fg-muted hover:text-fg hover:underline md:min-h-0">Clear</button>
        )}
      </div>
      {problem ? <p role="alert" className="text-t14 text-fg" data-testid="chat-error">{problem}</p> : null}
    </div>
  );
}
