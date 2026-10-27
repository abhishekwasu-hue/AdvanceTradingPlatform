/**
 * UX-11 "Numbers first, text on demand" (owner, 27 Oct; docs/owner-orders/ATP_ORDER_UX11.md): the shared pieces every
 * v2 card is built from. U1 Details, U2 Signed, U3 KeyValueTable / ProgressStat, U4 EmptyState, U5 PageFootnote; U6 the
 * text budget (textBudget.ts, checked in CI by e2e/text-budget-v2.e2e.ts).
 */
export { Details } from "./Details";
export { Signed, signedParts, type SignedProps } from "./Signed";
export { KeyValueTable, type KeyValueRow } from "./KeyValueTable";
export { ProgressStat, progressPct } from "./ProgressStat";
export { PageFootnote } from "./PageFootnote";
export { EmptyStateV2 as EmptyState, type EmptyAction } from "../dashboard/CardV2";
export { CARD_LINE_WORDS, cardTextProblems, wordCount, type CardText } from "./textBudget";
