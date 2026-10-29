import { History } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { reasonWords } from "../signals/signalText";
import { Details } from "../ui/Details";
import { formatDateIST } from "../ui/format";
import {
  contextWords, journalLink, recallApi, recallSummary, rWords, type Recall, type RecallMatch, type Reflection,
} from "./recall";

/**
 * AUD-T6 (BR-M3 / BR-R1; flags `ui_v2` + `setup_recall`, checked by the caller): one line that stays open - how many of
 * his own past closed trades had the same stored setup - and one closed Details with the matches (date, outcome in R and
 * words, a link to each journal row) and, on a closed journal row, what the context said at entry next to what
 * happened. Nothing shows while the server says the feature is off (404) or the read fails.
 */

type State = { status: "loading" } | { status: "hidden" } | { status: "ok"; recall: Recall; reflection: Reflection | null };

function MatchRow({ m, onOpenTrade }: { m: RecallMatch; onOpenTrade?: (week: string | null, tradeId: number) => void }) {
  const tone = m.result === "gain" ? "text-up" : m.result === "loss" ? "text-down" : "text-fg";
  const label = `Open trade of ${m.entered_at ? formatDateIST(m.entered_at) : "-"} in the journal`;
  return (
    <li className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 py-space-1" data-recall-match={m.trade_id}>
      <span className="font-tabular text-fg-muted">{m.entered_at ? formatDateIST(m.entered_at) : "-"}</span>
      <span className={`font-tabular ${tone}`}>{m.outcome}</span>
      <span className="text-fg-muted">{m.how_closed}</span>
      {onOpenTrade
        ? <button type="button" className="ml-auto text-brand underline-offset-2 hover:underline" aria-label={label} onClick={() => onOpenTrade(m.journal_week, m.trade_id)}>Open</button>
        : <Link to={journalLink(m)} className="ml-auto text-brand underline-offset-2 hover:underline" aria-label={label}>Open</Link>}
    </li>
  );
}

export function RecallBody({ recall, reflection, onOpenTrade }: {
  recall: Recall; reflection: Reflection | null; onOpenTrade?: (week: string | null, tradeId: number) => void;
}) {
  const reasons = reflection ? reasonWords(reflection.said.reasons) : [];
  return (
    <>
      {reflection?.closed && reflection.happened && (
        <div data-recall-reflection>
          <p className="font-semibold text-fg">What the context said vs what happened</p>
          <p>{reflection.line}</p>
          <p>At entry: {contextWords(reflection.said)}{reflection.said.planned_rr !== null ? ` · planned reward to risk ${reflection.said.planned_rr}` : ""}</p>
          {reasons.length > 0 && <ul className="list-disc pl-space-5">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
          <p>Outcome: {reflection.happened.how_closed} · {reflection.happened.outcome}
            {reflection.happened.mfe_r !== null && reflection.happened.mae_r !== null
              ? ` · most in your favour ${reflection.happened.mfe_r} R, most against you ${reflection.happened.mae_r} R` : ""}</p>
        </div>
      )}
      <p data-recall-line>{recall.line}</p>
      {recall.matches.length > 0 && (
        <ul className="divide-y divide-border/60" aria-label="Similar past trades">
          {recall.matches.map((m) => <MatchRow key={m.trade_id} m={m} onOpenTrade={onOpenTrade} />)}
        </ul>
      )}
      <p>
        Similar means the same setup and the same {contextWords(recall.context)}, as recorded when each trade was taken; only
        trades that closed before this entry count. The totals show from {recall.min_sample} similar trades. Your own history
        only - not a forecast and not advice.
      </p>
    </>
  );
}

export default function SimilarSetupsV2({ kind, id, onOpenTrade }: {
  kind: "trade" | "proposal"; id: number; onOpenTrade?: (week: string | null, tradeId: number) => void;
}) {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    const read = kind === "trade"
      ? recallApi.trade(id).then((d) => ({ recall: d.recall, reflection: d.reflection }))
      : recallApi.proposal(id).then((d) => ({ recall: d.recall, reflection: null }));
    read.then((d) => { if (live) setState({ status: "ok", ...d }); }).catch(() => { if (live) setState({ status: "hidden" }); });
    return () => { live = false; };
  }, [kind, id]);
  if (state.status !== "ok") return null;
  return (
    <section className="text-t13" aria-label="Similar setups" data-setup-recall={`${kind}-${id}`}>
      <p className="flex items-center gap-space-2 text-fg" data-recall-summary>
        <History size={14} aria-hidden className="shrink-0 text-fg-muted" />
        <span>{recallSummary(state.recall)}</span>
      </p>
      <Details id={`setup-recall.${kind}`} label="Similar setups" className="mt-space-1">
        <RecallBody recall={state.recall} reflection={state.reflection} onOpenTrade={onOpenTrade} />
      </Details>
    </section>
  );
}
