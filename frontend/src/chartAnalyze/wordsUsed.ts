import { glossary, type GlossaryEntry, type GlossaryKey } from "../ui/glossary";
import type { AnalysisResult } from "./types";

/**
 * Owner CA-D3 (3 Nov): every method word a read uses (CHoCH, handover, LTF, commitment candle, ...) is explained in
 * plain English behind a "?" - the definitions come only from the one glossary (ui/glossary.ts), never a copy.
 * Only the words that appear in this read are listed, in the order below.
 */
const WORDS: readonly [GlossaryKey, RegExp][] = [
  ["choch", /\bCHoCH\b|change of character/i],
  ["bos", /\bBOS\b|break of structure/i],
  ["swing-hh", /\bHH\b|higher high/i],
  ["swing-hl", /\bHL\b|higher low/i],
  ["swing-lh", /\bLH\b|lower high/i],
  ["swing-ll", /\bLL\b|lower low/i],
  ["impulse", /\bimpulse/i],
  ["pullback", /\bpullback/i],
  ["buyer-area", /\bbuyer area/i],
  ["seller-area", /\bseller area/i],
  ["area-fresh", /\bfresh\b/i],
  ["area-tested", /\btested\b/i],
  ["handover", /\bhandover/i],
  ["commitment-candle", /\bcommitment/i],
  ["ltf", /\bLTF\b|lower[- ]timeframe/i],
  ["rsi-divergence", /\bRSI divergence|\bdivergence\b/i],
  ["invalidation", /\binvalidat/i],
];

/** All the words a person reads on the result (the story, the cards, the evidence, the levels). */
export function resultText(r: AnalysisResult): string {
  const parts: (string | null | undefined)[] = [
    ...r.story, r.trend.text, r.current_state.text, r.current_state.phase, r.rsi.text,
    r.setup.trigger.text, r.setup.invalidation.text, r.setup.waiting_for, ...r.setup.next_areas.map((n) => n.text),
    ...r.evidence.flatMap((e) => [e.name, e.reason]), r.scenarios.primary, r.scenarios.alternative,
    r.scenarios.primary_invalidated_at.text, r.what_to_wait_for?.condition,
    ...r.key_levels.flatMap((l) => [l.name, l.state, l.side ? `${l.side} area` : null]),
    "Invalidation",                                            // the setup card always shows the word
  ];
  return parts.filter(Boolean).join(" \n ");
}

export function wordsUsed(r: AnalysisResult): GlossaryEntry[] {
  const all = resultText(r);
  return WORDS.filter(([, re]) => re.test(all)).map(([key]) => glossary(key));
}
