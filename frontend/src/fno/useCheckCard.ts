// M76 MP-2: the check card's state for one ticket - the server's checks in the background (never awaited by Place), one
// follow-up for checks that ran past the 300 ms budget, the lines he muted ("Don't show again"), the lines Place is asking
// him to accept ("ask me" on), and the record of accepted lines when Place is pressed (fire and forget).

import { useCallback, useRef, useState } from "react";
import { BUDGET_MS, checksApi, factKey, markSeen, type CheckDraft, type CheckLine, type ChecksAnswer, type ChecksApi, type WarningId } from "./checkCard";
import type { RiskLine } from "./insights";

/** What the ticket body needs to draw the card (absent: the ticket is exactly as before). */
export interface TicketCard {
  lines: CheckLine[]; others: (RiskLine | null)[]; statuses: string[]; pending: number; asking: CheckLine[];
  expanded: boolean; setExpanded: (open: boolean) => void;
  onEdit: (id: WarningId) => void; onDontShow: (id: WarningId) => void; onAccept: (line: CheckLine) => void;
}

export interface CheckCardState {
  answer: ChecksAnswer | null; pending: number; checksMs: number | null; hidden: ReadonlySet<string>;
  expanded: boolean; setExpanded: (open: boolean) => void; asking: CheckLine[]; setAsking: (lines: CheckLine[]) => void;
  acceptedHere: ReadonlySet<string>; acceptHere: (line: CheckLine) => void;
  start: (draft: CheckDraft) => void; reset: () => void; mute: (id: WarningId) => void;
  placed: (lines: readonly CheckLine[], draft: Pick<CheckDraft, "underlying" | "expiry" | "strike" | "right"> | null) => void;
}

export function useCheckCard(enabled: boolean, api: ChecksApi = checksApi, followUpMs = BUDGET_MS + 100): CheckCardState {
  const [answer, setAnswer] = useState<ChecksAnswer | null>(null);
  const [pending, setPending] = useState(0);
  const [checksMs, setChecksMs] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState(false);
  const [asking, setAsking] = useState<CheckLine[]>([]);
  const [acceptedHere, setAcceptedHere] = useState<Set<string>>(() => new Set());
  const seq = useRef(0);

  const reset = useCallback(() => {
    seq.current += 1;
    setAnswer(null); setPending(0); setChecksMs(null); setExpanded(false); setAsking([]); setAcceptedHere(new Set());
  }, []);

  const start = useCallback((draft: CheckDraft) => {
    if (!enabled) return;
    const n = ++seq.current;
    setAnswer(null); setPending(1); setAsking([]); setAcceptedHere(new Set());
    const began = typeof performance !== "undefined" ? performance.now() : Date.now();
    api.run(draft).then((a) => {
      if (n !== seq.current) return;
      setAnswer(a); setPending(a.pending.length);
      setChecksMs(Math.round((typeof performance !== "undefined" ? performance.now() : Date.now()) - began));
      if (!a.pending.length) return;
      window.setTimeout(() => {
        if (n !== seq.current) return;
        api.run(draft, a.pending).then((late) => {
          if (n !== seq.current) return;
          setAnswer((cur) => (cur ? { ...cur, findings: [...cur.findings, ...late.findings], pending: late.pending } : late));
          setPending(late.pending.length);
        }).catch(() => { if (n === seq.current) setPending(0); });
      }, followUpMs);
    }).catch(() => { if (n === seq.current) setPending(0); });   // no checks: the card shows the screen's own lines only
  }, [enabled, api, followUpMs]);

  const mute = useCallback((id: WarningId) => {
    setHidden((h) => new Set(h).add(id));
    setAsking((a) => a.filter((l) => l.id !== id));
    api.dontShow(id).catch(() => undefined);           // intentional: hidden here at once; the switch saves in the background
  }, [api]);

  const acceptHere = useCallback((line: CheckLine) => {
    setAcceptedHere((s) => new Set(s).add(factKey(line)));
    setAsking((a) => a.filter((l) => factKey(l) !== factKey(line)));
  }, []);

  const placed = useCallback((lines: readonly CheckLine[], draft: Pick<CheckDraft, "underlying" | "expiry" | "strike" | "right"> | null) => {
    if (!enabled || !lines.length) return;
    markSeen(lines);
    if (draft) api.accept(draft, [...new Set(lines.map((l) => l.id))]).catch(() => undefined);   // intentional: never awaited
  }, [enabled, api]);

  return { answer, pending, checksMs, hidden, expanded, setExpanded, asking, setAsking, acceptedHere, acceptHere, start, reset, mute, placed };
}
